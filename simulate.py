#!/usr/bin/env python3
"""
Run AI-vs-AI games through the TurnController: engine fuzzer + agent benchmark.

    python3 simulate.py -n 20 --p1 aggressive --p2 greedy --seed 1
    python3 simulate.py -n 5 --events out.jsonl     # dump every event for inspection

After every action a set of invariants is checked; violations and crashes
are reported with the seed that reproduces them.
"""

import argparse
import json
import random
import sys
import time
import traceback
from collections import Counter
from typing import Dict, List, Optional

from evaluation import GameStateEvaluator
from game_runner import RandomAgent, AggressiveRandomAgent, GreedyAgent
from game_setup import quick_setup_broad
from game_state import GameState
from scenario import build_systems
from turn_controller import TurnController


AGENTS = ['random', 'aggressive', 'greedy', 'heuristic', 'lookahead', 'mcts']
MCTS_TIME = 1.0   # seconds per MCTS decision (--mcts-time)


def make_agent(kind: str, name: str, systems):
    if kind == 'random':
        return RandomAgent(name)
    if kind == 'aggressive':
        return AggressiveRandomAgent(name)
    if kind == 'greedy':
        return GreedyAgent(name, systems.executor, GameStateEvaluator())
    if kind == 'heuristic':
        from agents import HeuristicAgent
        return HeuristicAgent(name, movement_system=systems.movement, rng=random.Random(random.random()))
    if kind == 'lookahead':
        from agents import LookaheadAgent
        return LookaheadAgent(name, executor=systems.executor, evaluator=GameStateEvaluator(),
                              movement_system=systems.movement, rng=random.Random(random.random()))
    if kind == 'mcts':
        from mcts import MCTSAgent
        return MCTSAgent(name, time_limit=MCTS_TIME, movement_system=systems.movement,
                         rng=random.Random(random.random()))
    raise ValueError(f"unknown agent {kind!r}; choose from {AGENTS}")


# ----------------------------------------------------------------------
# Invariants
# ----------------------------------------------------------------------

def check_invariants(gs: GameState) -> List[str]:
    problems = []
    board = gs.board
    by_hex: Dict[tuple, list] = {}
    for uid, us in gs.units.items():
        if not us.is_alive or not us.is_deployed:
            continue
        q, r = us.position
        if board.get_hex(q, r) is None and not us.carried_by_id:
            problems.append(f"{uid} off-board at {us.position}")
        if us.current_health < 0:
            problems.append(f"{uid} negative health {us.current_health}")
        if us.current_health > (us.unit.defense_front or 0) + 5:
            problems.append(f"{uid} health {us.current_health} above plausible max")
        if us.carried_by_id:
            carrier = gs.units.get(us.carried_by_id)
            if carrier is None or not carrier.is_alive:
                problems.append(f"{uid} carried by missing/dead {us.carried_by_id}")
            elif carrier.carried_unit_id != uid and not us.carried_by_id:
                problems.append(f"{uid} carried_by {us.carried_by_id} but carrier does not reference it")
        else:
            by_hex.setdefault((q, r), []).append(us)
    for pos, all_units in by_hex.items():
        # Rulebook: Aircraft don't count toward the limit, but only one Aircraft per hex
        aircraft = [u for u in all_units if 'Aircraft' in (u.unit.unit_type or '')]
        if len(aircraft) > 1:
            problems.append(f">1 aircraft at {pos}: {[u.unit.name for u in aircraft]}")
        units = [u for u in all_units if u not in aircraft]
        owners = {u.owner for u in units}
        for owner in owners:
            mine = [u for u in units if u.owner == owner]
            if len(mine) > 2:
                problems.append(f"stacking >2 at {pos} for {owner}: {[u.unit.name for u in mine]}")
        vehicles = [u for u in units if (u.unit.unit_type or '').startswith('Vehicle') or any(a.lower() == 'large' for a in u.unit.abilities)]
        if len(vehicles) > 1:
            problems.append(f">1 vehicle at {pos}: {[u.unit.name for u in vehicles]}")
    for uid in gs.pending_hits:
        if uid not in gs.units:
            problems.append(f"pending hits for unknown unit {uid}")
    return problems


class CheckingController(TurnController):
    """TurnController that validates invariants after every action."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.violations: List[str] = []

    def apply(self, action):
        result = super().apply(action)
        for p in check_invariants(self.game_state):
            self.violations.append(f"turn {self.game_state.turn_number} {self.current()}: {p} (after {action})")
        return result

    def _end_turn(self):
        super()._end_turn()
        if self.game_state.pending_hits and not self.game_over:
            self.violations.append(f"pending hits survived casualty phase: {list(self.game_state.pending_hits)}")


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------

def play_one(seed: int, p1: str, p2: str, points: int, max_turns: int,
             events_out=None, deploy: bool = False, historical: bool = False) -> dict:
    random.seed(seed)                      # setup/agent randomness
    systems = build_systems(seed=seed)     # dice
    from game_setup import GameSetup, GameSetupConfig
    gs = GameSetup(GameSetupConfig(points_per_side=points, historical=historical)).create_game()
    gs.rng_seed = seed
    if deploy:   # exercise the rulebook deployment phase instead of the fixed placement
        for us in gs.units.values():
            if 'Aircraft' not in (us.unit.unit_type or ''):
                h = gs.board.get_hex(*us.position)
                if h is not None and h.unit is us.unit:
                    h.unit = None
                us.is_deployed = False
                us.position = (-99, -99)
    agents = {"player1": make_agent(p1, f"P1-{p1}", systems),
              "player2": make_agent(p2, f"P2-{p2}", systems)}
    tc = CheckingController(gs, systems.executor, systems.generator, systems.initiative,
                            agents, max_turns=max_turns, movement_system=systems.movement)
    for agent in agents.values():
        if hasattr(agent, 'attach'):
            agent.attach(tc)    # search agents simulate forward from the live controller
    t0 = time.perf_counter()
    crash = None
    try:
        tc.run_game()
    except Exception:
        crash = traceback.format_exc()
    elapsed = time.perf_counter() - t0
    if events_out:
        for ev in tc.events:
            events_out.write(json.dumps({'seed': seed, **ev}, default=str) + "\n")
    n_actions = sum(1 for e in tc.events if e['type'] == 'action')
    res = tc.result or {}
    return {
        'seed': seed, 'winner': res.get('winner'), 'reason': res.get('reason'),
        'turns': res.get('turns', gs.turn_number), 'actions': n_actions,
        'seconds': elapsed, 'violations': tc.violations, 'crash': crash,
        'failed_actions': [e['message'] for e in tc.events if e['type'] == 'action' and not e['success']],
        'search': {p: dict(a.stats) for p, a in agents.items() if hasattr(a, 'stats')},
    }


def main(argv=None):
    global MCTS_TIME
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-n', '--games', type=int, default=10)
    ap.add_argument('--p1', default='aggressive', choices=AGENTS)
    ap.add_argument('--p2', default='aggressive', choices=AGENTS)
    ap.add_argument('--seed', type=int, default=1, help='first seed; game i uses seed+i')
    ap.add_argument('--points', type=int, default=100)
    ap.add_argument('--max-turns', type=int, default=20)
    ap.add_argument('--events', help='write all events as JSON lines to this file')
    ap.add_argument('--show-failed', action='store_true', help='print failed action messages')
    ap.add_argument('--deploy', action='store_true', help='run the coin-flip/deployment phase (AI policy) instead of fixed placement')
    ap.add_argument('--historical', action='store_true', help='apply historical army limits')
    ap.add_argument('--mcts-time', type=float, default=1.0, help='seconds per MCTS decision')
    args = ap.parse_args(argv)
    MCTS_TIME = args.mcts_time

    out = open(args.events, 'w') if args.events else None
    wins = Counter()
    reasons = Counter()
    failed = Counter()
    total_time = 0.0
    total_turns = 0
    total_actions = 0
    bad = []
    for i in range(args.games):
        seed = args.seed + i
        r = play_one(seed, args.p1, args.p2, args.points, args.max_turns, out,
                     deploy=args.deploy, historical=args.historical)
        wins[r['winner']] += 1
        reasons[r['reason']] += 1
        total_time += r['seconds']
        total_turns += r['turns'] or 0
        total_actions += r['actions']
        for m in r['failed_actions']:
            failed[m.split(':')[0][:60]] += 1
        flag = ""
        if r['crash']:
            flag = "  CRASH"
            bad.append(r)
        elif r['violations']:
            flag = f"  {len(r['violations'])} violation(s)"
            bad.append(r)
        search = "".join(f"  [{p} {st['rollouts']} rollouts, {st['seconds'] / max(1, st['decisions']):.2f}s/decision]"
                         for p, st in r['search'].items() if st['decisions'])
        print(f"seed {seed:>4}: {str(r['winner']):8} {str(r['reason']):12} "
              f"{r['turns']:>2} turns {r['actions']:>4} actions {r['seconds']:5.1f}s{flag}{search}")
    if out:
        out.close()

    n = args.games
    print("\n" + "=" * 60)
    print(f"{args.p1} (P1) vs {args.p2} (P2): {n} games, seeds {args.seed}..{args.seed + n - 1}")
    for k, v in wins.most_common():
        print(f"  {str(k):8} {v:>3} ({100 * v / n:.0f}%)")
    print("  reasons:", dict(reasons))
    print(f"  avg turns {total_turns / n:.1f}, avg actions {total_actions / n:.0f}, "
          f"avg {total_time / n:.2f}s/game, {1000 * total_time / max(1, total_actions):.1f} ms/action")
    if failed:
        print(f"  failed actions (generator offered something the executor rejected): {sum(failed.values())}")
        if args.show_failed:
            for m, c in failed.most_common(15):
                print(f"    {c:>3}x {m}")
    if bad:
        print(f"\n{len(bad)} game(s) with problems:")
        for r in bad:
            print(f"  seed {r['seed']}:")
            if r['crash']:
                print("    " + r['crash'].strip().replace("\n", "\n    "))
            for v in r['violations'][:10]:
                print(f"    {v}")
        return 1
    print("\nno crashes, no invariant violations")
    return 0


if __name__ == '__main__':
    sys.exit(main())
