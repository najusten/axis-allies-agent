"""
Monte Carlo search on top of the heuristic policy.

The old tree search could not run on the real game (it ignored phase
transitions and branched over every legal action). This agent instead:

1. asks HeuristicAgent for the top-K statically ranked actions,
2. plays each candidate on a *simulated* TurnController (cloned state,
   both sides driven by the heuristic policy, separate dice RNG) for a
   short horizon of phases,
3. scores the resulting position with a material + objective value,
4. spends its time budget as a UCB1 bandit over the candidates and picks
   the one with the best mean value.

That is "flat" MCTS with informed rollouts: it captures dice variance,
defensive fire, stacking and the opponent's immediate reply, which the
one-ply static scorer cannot see. It needs the live controller
(`agent.attach(controller)`) to know where in the turn structure it is;
without one it falls back to the heuristic choice.
"""

import math
import random
import time
from typing import List, Optional

from action import Action, PassAction, EndPhaseAction
from agents import HeuristicAgent
from game_state import GameState


class MCTSAgent(HeuristicAgent):
    TERMINAL_VALUE = 1000.0

    def __init__(self, name: str = "MCTS", time_limit: float = 1.5, top_k: int = 5,
                 horizon_phases: int = 3, min_rollouts: int = 1, ucb_c: float = 1.4,
                 movement_system=None, rng: Optional[random.Random] = None, **kw):
        super().__init__(name, movement_system=movement_system, rng=rng, **kw)
        self.time_limit = time_limit
        self.top_k = top_k
        self.horizon_phases = horizon_phases
        self.min_rollouts = min_rollouts
        self.ucb_c = ucb_c
        self.controller = None
        self._sim_rng = random.Random((rng or random).random())
        self.stats = {'decisions': 0, 'rollouts': 0, 'seconds': 0.0}

    # -- wiring -------------------------------------------------------------

    def attach(self, controller):
        """The live TurnController this agent plays in (needed to simulate forward)."""
        self.controller = controller
        if self.movement is None:
            self.movement = controller.movement_system

    # legacy hooks kept so old call sites don't break
    def set_action_executor(self, executor):
        pass

    def set_action_generator(self, generator):
        pass

    def set_evaluator(self, evaluator):
        pass

    # -- decision -------------------------------------------------------------

    def choose_action(self, game_state: GameState, legal_actions: List[Action], player: str) -> Action:
        tc = self.controller
        if tc is None or tc.game_state is not game_state:
            return super().choose_action(game_state, legal_actions, player)
        self._los_cache.clear()
        self._path_cache = {}
        scored = self.score_actions(game_state, legal_actions, player)
        pass_action = next((a for a in legal_actions if isinstance(a, PassAction)), None)
        candidates = [a for s, a in scored[:self.top_k] if s > -5]
        if pass_action is not None:
            candidates.append(pass_action)      # ending the phase is always an option
        if len(candidates) <= 1:
            return candidates[0] if candidates else super().choose_action(game_state, legal_actions, player)

        t0 = time.perf_counter()
        deadline = t0 + self.time_limit
        snap = tc.snapshot()
        n = [0] * len(candidates)
        total = [0.0] * len(candidates)
        rollouts = 0

        def run(i):
            nonlocal rollouts
            v = self._rollout(snap, candidates[i], player)
            if v is None:          # illegal in simulation: strongly discourage
                v = -self.TERMINAL_VALUE
            n[i] += 1
            total[i] += v
            rollouts += 1

        # every candidate gets a few samples, then UCB1 until the budget is spent
        for _ in range(self.min_rollouts):
            for i in range(len(candidates)):
                run(i)
        while time.perf_counter() < deadline:
            log_n = math.log(rollouts + 1)
            i = max(range(len(candidates)),
                    key=lambda k: total[k] / n[k] + self.ucb_c * math.sqrt(log_n / n[k]))
            run(i)

        # heuristic ranking as a tie-breaker (candidates are in rank order)
        best = max(range(len(candidates)),
                   key=lambda k: (total[k] / n[k] - 0.01 * k))
        elapsed = time.perf_counter() - t0
        self.stats['decisions'] += 1
        self.stats['rollouts'] += rollouts
        self.stats['seconds'] += elapsed
        return candidates[best]

    # -- simulation ----------------------------------------------------------

    def _rollout(self, snap: dict, action: Action, player: str) -> Optional[float]:
        from turn_controller import TurnController, INITIATIVE_PHASE, DEPLOY_ORDER_PHASE
        live = self.controller
        executor = live.executor
        dice = executor.dice
        saved = (executor.df_asker, dict(executor.df_decisions), dice.rng)
        try:
            dice.rng = self._sim_rng
            executor.df_decisions = {}
            policy = {p: HeuristicAgent(f"rollout-{p}", movement_system=self.movement,
                                        rng=random.Random(self._sim_rng.random()))
                      for p in ("player1", "player2")}
            sim = TurnController(snap['game_state'], executor, live.generator, live.initiative,
                                 policy, max_turns=live.max_turns, movement_system=self.movement)
            sim.restore(snap)        # clones the game state
            sim.events = []

            phases = 0
            first = True
            while not sim.game_over and phases < self.horizon_phases:
                cur = sim.current()
                if cur is None:
                    break
                phase, p = cur
                if phase in (INITIATIVE_PHASE, DEPLOY_ORDER_PHASE):
                    sim.choose_order(p) if phase == INITIATIVE_PHASE else sim.choose_deploy_order(p)
                    continue
                if first:
                    first = False
                    if isinstance(action, (PassAction, EndPhaseAction)):
                        sim.end_phase()
                        phases += 1
                        continue
                    res = sim.apply(action)
                    if not res.success:
                        return None
                    if sim.game_over:
                        break
                sim._run_ai_phase(p, None)      # deployment, movement, assault, ... via the policy
                sim.end_phase()
                phases += 1
            return self.value(sim, player)
        finally:
            executor.df_asker, executor.df_decisions, dice.rng = saved

    # -- evaluation --------------------------------------------------------

    def value(self, sim, player: str) -> float:
        gs = sim.game_state
        if sim.game_over:
            w = (sim.result or {}).get('winner')
            if w == player:
                return self.TERMINAL_VALUE
            if w in ("player1", "player2"):
                return -self.TERMINAL_VALUE
            return 0.0
        enemy = "player2" if player == "player1" else "player1"
        return (self._material(gs, player) - self._material(gs, enemy)
                + self._objective(gs, player) - self._objective(gs, enemy))

    @staticmethod
    def _material(gs: GameState, owner: str) -> float:
        total = 0.0
        for us in gs.get_units_by_owner(owner):
            if not us.is_alive:
                continue
            cost = float(us.unit.cost or 10)
            f = 1.0
            ph = gs.pending_hits.get(us.unit.id)
            if ph is not None and ph.has_destroyed():
                continue
            disrupted = us.is_disrupted or (ph is not None and ph.get_face_down_count() > 0)
            if disrupted:
                f *= 0.7
            if us.is_damaged:
                f *= 0.6
            total += cost * f
        return total

    @staticmethod
    def _objective(gs: GameState, owner: str) -> float:
        if gs.objective_position is None:
            return 0.0
        oq, orr = gs.objective_position
        turn = gs.turn_number
        # the objective decides the game from turn 7: weight it up as that approaches
        w = 4.0 + 4.0 * max(0, turn - 3)
        near = 0
        dist_sum = 0.0
        alive = 0
        for us in gs.get_units_by_owner(owner):
            if not us.is_alive or not us.is_deployed or us.carried_by_id:
                continue
            if 'Aircraft' in (us.unit.unit_type or ''):
                continue
            ph = gs.pending_hits.get(us.unit.id)
            if ph is not None and ph.has_destroyed():
                continue
            d = gs.board.hex_distance(us.position[0], us.position[1], oq, orr)
            alive += 1
            dist_sum += d
            if d <= 1:
                near += 1
        if alive == 0:
            return 0.0
        return w * min(near, 2) - 0.5 * (dist_sum / alive)
