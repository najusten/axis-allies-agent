"""
Smarter, fast agents.

HeuristicAgent scores every legal action with a static evaluation that
needs no cloning or simulation (microseconds per action):

    attacks  → expected value of the hit outcome (binomial over the dice,
               cover roll, target cost, focus on damaged targets)
    moves    → objective pressure (phased by turn), cover, expected damage
               dealt from the new hex next phase, expected damage taken
               there, rear exposure for vehicles, forest bog risk
    abilities/transports → small fixed values

LookaheadAgent keeps the top-K candidates by that static score and
evaluates each by executing it on a cloned state and scoring the result
with GameStateEvaluator — a pruned version of GreedyAgent that stays fast.

Both expose choose_action(game_state, legal_actions, player) like the
agents in game_runner.py, so they work with TurnController, simulate.py
and the server.
"""

import math
import random
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from action import (Action, MoveAction, AttackAction, PassAction, EndPhaseAction,
                    UseAbilityAction, BoardTransportAction, DismountTransportAction)
from board import Board
from game_state import GameState, GamePhase, UnitState
from movement import MovementSystem


# ----------------------------------------------------------------------
# Dice maths
# ----------------------------------------------------------------------

@lru_cache(maxsize=None)
def _p_at_least(n_dice: int, k: int, p: float) -> float:
    """P(Binomial(n, p) >= k)"""
    if k <= 0:
        return 1.0
    if k > n_dice:
        return 0.0
    total = 0.0
    for i in range(k, n_dice + 1):
        total += math.comb(n_dice, i) * p ** i * (1 - p) ** (n_dice - i)
    return total


def hit_distribution(n_dice: int, defense: int, threshold: int = 4) -> Tuple[float, float, float]:
    """(P(exactly 1 hit), P(2 hits), P(3 hits)) for n dice vs defense."""
    if n_dice <= 0 or defense <= 0:
        return 0.0, 0.0, 0.0
    p = max(0.0, min(1.0, (7 - threshold) / 6))
    p1 = _p_at_least(n_dice, defense, p)
    p2 = _p_at_least(n_dice, defense + 1, p)
    p3 = _p_at_least(n_dice, 2 * defense, p)
    return p1 - p2, p2 - p3, p3


def expected_attack_value(n_dice: int, defense: int, target_cost: float, is_vehicle: bool,
                          disrupted: bool, damaged: bool, in_cover: bool,
                          threshold: int = 4) -> float:
    """
    Expected fraction of the target's value removed by one attack (times cost).
    Soldiers: 1 hit = disrupted, 2+ = destroyed (or destroyed on 1 hit if already disrupted).
    Vehicles: 1 = disrupted, 2 = damaged, 3 = destroyed; already-damaged ones die on 2.
    A successful cover roll (soldier 4+, vehicle 5+) reduces any result to a single disrupt.
    """
    p1, p2, p3 = hit_distribution(n_dice, defense, threshold)
    if p1 + p2 + p3 <= 0:
        return 0.0
    if is_vehicle:
        kill = p3 + (p2 if damaged else 0.0)
        dmg = p2 if not damaged else 0.0
        dis = p1 if not disrupted else 0.0
        val = kill * 1.0 + dmg * 0.5 + dis * 0.25
        # already disrupted: another disrupt wastes nothing but adds little
        if disrupted and not damaged:
            val += p1 * 0.1
    else:
        kill = p2 + p3 + (p1 if disrupted else 0.0)
        dis = p1 if not disrupted else 0.0
        val = kill * 1.0 + dis * 0.35
    if in_cover:
        save = 2 / 6 if is_vehicle else 3 / 6      # 5+ / 4+
        any_hit = p1 + p2 + p3
        # on a save the result becomes a single disrupt
        val = val * (1 - save) + save * any_hit * (0.35 if not disrupted else 0.6)
    return val * target_cost


# ----------------------------------------------------------------------
# Unit helpers
# ----------------------------------------------------------------------

def attack_dice(unit, target, distance: int) -> int:
    cat = MovementSystem.get_range_category(distance)
    if 'Vehicle' in (target.unit_type or ''):
        return {'short': unit.veh_short, 'medium': unit.veh_medium, 'long': unit.veh_long}.get(cat, 0)
    return {'short': unit.per_short, 'medium': unit.per_medium, 'long': unit.per_long}.get(cat, 0)


def max_range(unit) -> int:
    if unit.veh_long or unit.per_long:
        return 8
    if unit.veh_medium or unit.per_medium:
        return 4
    if unit.veh_short or unit.per_short:
        return 1
    return 0


def is_vehicle(unit) -> bool:
    return 'Vehicle' in (unit.unit_type or '')


def unit_value(us: UnitState) -> float:
    cost = float(getattr(us.unit, 'cost', 10) or 10)
    if us.is_disrupted and us.is_damaged:
        return cost * 0.4
    if us.is_damaged:
        return cost * 0.6
    if us.is_disrupted:
        return cost * 0.75
    return cost


# ----------------------------------------------------------------------
# HeuristicAgent
# ----------------------------------------------------------------------

class HeuristicAgent:
    """Static-evaluation agent. Fast enough for thousands of decisions per second."""

    W_OBJECTIVE_EARLY = 1.2      # per hex closer to objective, turns 1-4
    W_OBJECTIVE_LATE = 4.0       # turns 5+
    W_COVER = 4.0
    W_THREAT = 0.9               # multiplier on expected damage taken
    W_OPPORTUNITY = 0.8          # multiplier on best expected damage dealt next phase
    W_REAR = 3.0
    W_BOG = 6.0
    W_SPREAD = 1.5               # penalty per friendly unit sharing the destination hex

    def __init__(self, name: str = "Heuristic", movement_system: Optional[MovementSystem] = None,
                 randomness: float = 0.15, rng: Optional[random.Random] = None):
        self.name = name
        self.movement = movement_system
        self.randomness = randomness
        self.rng = rng or random.Random()
        self._los_cache: Dict[tuple, bool] = {}

    # -- interface --------------------------------------------------------

    def choose_action(self, game_state: GameState, legal_actions: List[Action], player: str) -> Action:
        self._los_cache.clear()
        scored = self.score_actions(game_state, legal_actions, player)
        if not scored:
            return next((a for a in legal_actions if isinstance(a, PassAction)), legal_actions[0])
        best_score, best = scored[0]
        pass_action = next((a for a in legal_actions if isinstance(a, PassAction)), None)
        if best_score <= 0 and pass_action is not None:
            return pass_action
        # Mild randomness: pick among near-best to avoid deterministic loops
        if self.randomness > 0 and len(scored) > 1:
            cutoff = best_score - abs(best_score) * self.randomness - 0.01
            pool = [a for s, a in scored if s >= cutoff]
            return self.rng.choice(pool)
        return best

    def score_actions(self, game_state: GameState, legal_actions: List[Action], player: str):
        """[(score, action)] sorted best first; Pass/EndPhase excluded."""
        ctx = self._context(game_state, player)
        scored = []
        for a in legal_actions:
            if isinstance(a, (PassAction, EndPhaseAction)):
                continue
            s = self.score(game_state, a, player, ctx)
            if s is not None:
                scored.append((s, a))
        scored.sort(key=lambda t: t[0], reverse=True)
        return scored

    # -- scoring ----------------------------------------------------------

    def _context(self, gs: GameState, player: str) -> dict:
        enemy = "player2" if player == "player1" else "player1"
        return {
            'enemies': [u for u in gs.get_units_by_owner(enemy) if u.is_alive and not u.carried_by_id],
            'friends': [u for u in gs.get_units_by_owner(player) if u.is_alive and not u.carried_by_id],
            'objective': gs.objective_position,
            'turn': gs.turn_number,
            'w_obj': self.W_OBJECTIVE_LATE if gs.turn_number >= 5 else self.W_OBJECTIVE_EARLY,
        }

    def score(self, gs: GameState, a: Action, player: str, ctx: dict) -> Optional[float]:
        us = gs.get_unit_state(getattr(a, 'unit_id', None))
        if us is None:
            return None
        if isinstance(a, AttackAction):
            return self._score_attack(gs, a, us, ctx)
        if isinstance(a, MoveAction):
            return self._score_move(gs, a, us, ctx)
        if isinstance(a, BoardTransportAction):
            return 1.0
        if isinstance(a, DismountTransportAction):
            return self._score_position(gs, us, (a.to_q, a.to_r), ctx) - self._score_position(gs, us, us.position, ctx) + 0.5
        if isinstance(a, UseAbilityAction):
            name = (a.ability_name or '').lower()
            if 'smoke' in name:
                return 0.5
            if 'facing' in name:
                return -1.0
            return 0.2
        return 0.0

    def _score_attack(self, gs: GameState, a: AttackAction, us: UnitState, ctx: dict) -> float:
        target = gs.get_unit_state(a.target_id)
        if target is None or not target.is_alive:
            return 0.0
        # Don't waste fire on a unit that already has a pending destroyed counter
        ph = gs.pending_hits.get(a.target_id)
        if ph is not None and ph.has_destroyed():
            return 0.0
        dice = attack_dice(us.unit, target.unit, a.distance)
        if dice <= 0:
            return 0.0
        threshold = 5 if (us.is_disrupted or us.is_damaged) else 4
        hex_ = gs.board.get_hex(*target.position)
        in_cover = bool(hex_ and hex_.terrain in Board.COVER_TERRAIN)
        defense = target.unit.defense_front or 1
        if is_vehicle(target.unit) and target.facing is not None:
            from facing import is_front_arc_attack, HexDirection
            try:
                if not is_front_arc_attack(tuple(us.position), tuple(target.position), HexDirection(target.facing)):
                    defense = target.unit.defense_rear or defense
            except Exception:
                pass
        pending_disrupt = bool(ph and ph.get_face_down_count() >= 1)
        val = expected_attack_value(dice, defense, float(target.unit.cost or 10), is_vehicle(target.unit),
                                    target.is_disrupted or pending_disrupt, target.is_damaged, in_cover, threshold)
        # Attacking always beats doing nothing in the assault phase
        return 1.0 + val * 2.0

    def _score_move(self, gs: GameState, a: MoveAction, us: UnitState, ctx: dict) -> float:
        dest = (a.to_q, a.to_r)
        here = self._score_position(gs, us, us.position, ctx)
        there = self._score_position(gs, us, dest, ctx)
        score = there - here
        # Bog risk for vehicles entering forest
        hex_ = gs.board.get_hex(*dest)
        if hex_ and hex_.terrain == 'forest' and is_vehicle(us.unit):
            score -= self.W_BOG
        # In the assault phase moving forfeits the attack: only worth it if clearly better
        if gs.current_phase == GamePhase.ASSAULT:
            score -= 2.0
        return score

    def _score_position(self, gs: GameState, us: UnitState, pos: Tuple[int, int], ctx: dict) -> float:
        board = gs.board
        unit = us.unit
        score = 0.0
        # Objective pressure
        oq, orr = ctx['objective']
        d_obj = board.hex_distance(pos[0], pos[1], oq, orr)
        rng = max_range(unit)
        if rng >= 4 and attack_dice(unit, unit, 3) >= 4 and ctx['turn'] < 6:
            # long-range shooters: stage 2-4 hexes out rather than sitting on it
            score -= ctx['w_obj'] * abs(d_obj - 3)
        else:
            score -= ctx['w_obj'] * d_obj
        # Cover
        hex_ = board.get_hex(*pos)
        if hex_ and hex_.terrain in Board.COVER_TERRAIN:
            score += self.W_COVER
        # Threat / opportunity vs each enemy
        best_opp = 0.0
        threat = 0.0
        for e in ctx['enemies']:
            d = board.hex_distance(pos[0], pos[1], e.position[0], e.position[1])
            if d > 8:
                continue
            # what I could do to them from here
            my_dice = attack_dice(unit, e.unit, d)
            if my_dice > 0 and self._los(gs, unit, pos, e.position):
                ehex = board.get_hex(*e.position)
                v = expected_attack_value(my_dice, e.unit.defense_front or 1, float(e.unit.cost or 10),
                                          is_vehicle(e.unit), e.is_disrupted, e.is_damaged,
                                          bool(ehex and ehex.terrain in Board.COVER_TERRAIN))
                best_opp = max(best_opp, v)
            # what they could do to me
            their_dice = attack_dice(e.unit, unit, d)
            if their_dice > 0 and not e.is_disrupted and self._los(gs, e.unit, e.position, pos):
                defense = unit.defense_front or 1
                rear_exposed = False
                if is_vehicle(unit) and us.facing is not None:
                    from facing import is_front_arc_attack, HexDirection
                    try:
                        rear_exposed = not is_front_arc_attack(tuple(e.position), pos, HexDirection(us.facing))
                    except Exception:
                        pass
                if rear_exposed:
                    defense = unit.defense_rear or defense
                    threat += self.W_REAR
                threat += expected_attack_value(their_dice, defense, float(unit.cost or 10), is_vehicle(unit),
                                                us.is_disrupted, us.is_damaged,
                                                bool(hex_ and hex_.terrain in Board.COVER_TERRAIN))
        score += self.W_OPPORTUNITY * best_opp - self.W_THREAT * threat
        # Spread out (blast, stacking)
        sharing = sum(1 for f in ctx['friends'] if f is not us and tuple(f.position) == tuple(pos))
        score -= self.W_SPREAD * sharing
        return score

    def _los(self, gs: GameState, unit, a: Tuple[int, int], b: Tuple[int, int]) -> bool:
        if self.movement is None:
            return True
        key = (a, b)
        if key not in self._los_cache:
            ok, _ = self.movement.has_line_of_sight(gs.board, unit, a[0], a[1], b[0], b[1],
                                                    smoke_screens=gs.smoke_screens)
            self._los_cache[key] = ok
        return self._los_cache[key]


# ----------------------------------------------------------------------
# LookaheadAgent: heuristic pruning + one-ply simulation
# ----------------------------------------------------------------------

class LookaheadAgent(HeuristicAgent):
    """
    Score all actions statically, keep the top K, execute each on a clone
    and evaluate the resulting state with GameStateEvaluator. Dice are
    sampled once per candidate (cheap; the static score already carries
    the expectation), so this mostly catches interactions the static
    scorer misses (blast on friends, defensive fire, stacking).
    """

    def __init__(self, name: str = "Lookahead", executor=None, evaluator=None,
                 movement_system: Optional[MovementSystem] = None, top_k: int = 6, **kw):
        super().__init__(name, movement_system=movement_system, **kw)
        self.executor = executor
        self.evaluator = evaluator
        self.top_k = top_k

    def set_action_executor(self, executor):
        self.executor = executor

    def choose_action(self, game_state: GameState, legal_actions: List[Action], player: str) -> Action:
        if self.executor is None or self.evaluator is None:
            return super().choose_action(game_state, legal_actions, player)
        self._los_cache.clear()
        scored = self.score_actions(game_state, legal_actions, player)
        pass_action = next((a for a in legal_actions if isinstance(a, PassAction)), None)
        if not scored:
            return pass_action or legal_actions[0]
        candidates = [a for s, a in scored[:self.top_k] if s > -5]
        if not candidates:
            return pass_action or scored[0][1]
        baseline = self.evaluator.evaluate(game_state, player)
        best, best_val = None, -math.inf
        for a in candidates:
            clone = game_state.clone()
            try:
                res = self.executor.execute_action(clone, a)
            except Exception:
                continue
            if not res.success:
                continue
            val = self.evaluator.evaluate(clone, player)
            # tiny tie-breaker toward the static ranking
            val += 0.01 * (self.top_k - candidates.index(a))
            if val > best_val:
                best, best_val = a, val
        if best is None:
            return pass_action or candidates[0]
        if pass_action is not None and best_val < baseline - 1e-9 and gs_phase_allows_pass(game_state):
            return pass_action
        return best


def gs_phase_allows_pass(gs: GameState) -> bool:
    return True
