"""
TurnController — the single sequence-of-play state machine.

Used by both the Flask server (which must pause for human input) and the
AI-vs-AI runner. Replaces the two diverging phase loops that used to live in
server.py and game_runner.py.

Sequence per turn (Axis & Allies Miniatures):
    Initiative → Movement (1st, 2nd) → Flight (1st, 2nd; only with aircraft
    off-map) → Assault (1st, 2nd) → Airstrike (1st, 2nd; only with aircraft
    on-map) → Casualty → objective check (turn ≥ 7) → end of turn.
Before turn 1: Vanguard pre-game movement if any unit has the ability.

Players are a dict {player: agent_or_None}; None means a human who will call
apply()/end_phase() through the server. Agents are duck-typed: anything with
choose_action(game_state, legal_actions, player).

Everything that happens is appended to self.events as plain dicts (see
_emit) so a frontend can animate it and a log can be derived from it.
"""

from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

from game_state import GameState, GamePhase
from action import (Action, MoveAction, AttackAction, PassAction, EndPhaseAction, DeployAction)


VANGUARD_PHASE = "vanguard"
INITIATIVE_PHASE = "initiative"   # waiting for the initiative winner to choose first/second
DEPLOYMENT_PHASE = "deployment"   # pre-game: place undeployed units in your zone
DEPLOY_ORDER_PHASE = "deploy_order"   # coin-flip winner chooses to deploy first or second
OBJECTIVE_CHECK_TURN = 7     # rulebook: end of turn 7, then every turn
POINTS_CHECK_TURN = 10       # rulebook: end of turn 10, points decide (if not tied)


def jsonable(value):
    """Recursively convert enums/sets/tuples so an event can be JSON-encoded."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if hasattr(value, 'unit') and hasattr(value, 'position'):   # UnitState
        return getattr(value.unit, 'id', str(value))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _unit_name(game_state: GameState, unit_id: str) -> str:
    us = game_state.get_unit_state(unit_id)
    return us.unit.name if us else unit_id


class TurnController:

    def __init__(self, game_state: GameState, action_executor, action_generator,
                 initiative_system, players: Dict[str, Any],
                 max_turns: int = 30, movement_system=None):
        self.game_state = game_state
        self.executor = action_executor
        self.generator = action_generator
        self.initiative = initiative_system
        self.movement_system = movement_system or getattr(action_executor, 'movement_system', None)
        self.players = players            # player -> agent or None (human)
        self.max_turns = max_turns

        self.events: List[dict] = []
        self.turn_order: List[str] = ["player1", "player2"]
        self.phase_queue: List[Tuple[str, str]] = []   # (phase, player)
        self.phase_idx: int = 0
        self.result: Optional[dict] = None             # set when the game ends
        self._actions_this_phase: int = 0
        self._started = False
        self._vanguard_moved: set = set()
        # A move paused for a human's defensive-fire decision (see resume_defensive_fire)
        self.pending_df: Optional[dict] = None
        self.executor.df_asker = lambda gs, opp: self.is_human(opp.defender_state.owner)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def game_over(self) -> bool:
        return self.result is not None

    def current(self) -> Optional[Tuple[str, str]]:
        """(phase, player) currently active, or None when the game is over."""
        if self.game_over or self.phase_idx >= len(self.phase_queue):
            return None
        return self.phase_queue[self.phase_idx]

    def current_phase(self) -> Optional[str]:
        cur = self.current()
        return cur[0] if cur else None

    def current_player(self) -> Optional[str]:
        cur = self.current()
        return cur[1] if cur else None

    def is_human(self, player: str) -> bool:
        return self.players.get(player) is None

    def is_human_turn(self) -> bool:
        cur = self.current()
        return bool(cur) and self.is_human(cur[1])

    def legal_actions(self) -> List[Action]:
        """Legal actions for the current (phase, player), without Pass/EndPhase."""
        cur = self.current()
        if not cur or self.pending_df:
            return []
        phase, player = cur
        if phase == VANGUARD_PHASE:
            return self._vanguard_actions(player)
        if phase in (INITIATIVE_PHASE, DEPLOY_ORDER_PHASE):
            return []      # answered via choose_order()/choose_deploy_order(), not an action
        if phase == DEPLOYMENT_PHASE:
            return self._deployment_actions(player)
        actions = self.generator.get_all_legal_actions(self.game_state, player)
        return [a for a in actions if not isinstance(a, (PassAction, EndPhaseAction))]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Queue deployment (if units are undeployed), the vanguard phase (if any) and the first turn."""
        if self._started:
            return
        self._started = True
        self.phase_queue = []
        self.phase_idx = 0
        if self._undeployed('player1') or self._undeployed('player2'):
            # Rulebook: flip a coin; the winner decides whether to deploy first or second
            winner = "player1" if self.executor.dice.roll_d6() <= 3 else "player2"
            self.deploy_winner = winner
            self._emit('coin_flip', winner=winner)
            if self.is_human(winner):
                self.phase_queue = [(DEPLOY_ORDER_PHASE, winner)]
                self.game_state.active_player = winner
                self._emit('phase', phase=DEPLOY_ORDER_PHASE, player=winner)
                return
            # AI winner: deploying second (seeing the opponent's setup) is the safer default
            self.choose_deploy_order(self._other(winner))
            return
        self._after_deployment()

    def choose_deploy_order(self, first: str):
        self.game_state.current_phase = GamePhase.DEPLOYMENT
        self.phase_queue = [(DEPLOYMENT_PHASE, first), (DEPLOYMENT_PHASE, self._other(first))]
        self.phase_idx = 0
        self._emit('deploy_order', first=first)
        self._enter_phase()

    def _after_deployment(self):
        self.phase_queue = []
        self.phase_idx = 0
        if self._any_unit_has_ability('vanguard'):
            self.phase_queue = [(VANGUARD_PHASE, "player1"), (VANGUARD_PHASE, "player2")]
            self._emit('phase', phase=VANGUARD_PHASE, player="player1")
            self._enter_phase()
        else:
            self._start_turn()

    def apply(self, action: Action):
        """Execute one action for the current player. Returns the ActionResult."""
        cur = self.current()
        if not cur:
            raise RuntimeError("Game is over")
        phase, player = cur
        result = self.executor.execute_action(self.game_state, action)
        self._actions_this_phase += 1
        self._record_action(player, action, result)
        if getattr(result, 'interrupted', None):
            info = result.interrupted
            opps = info['opportunities']
            self.pending_df = {
                'player': opps[0].defender_state.owner,
                'mover_player': player,
                'action': action,
                'unit_id': info['unit_id'],
                'step_from': info['step_from'],
                'step_to': info['step_to'],
                'remaining_path': info['remaining_path'],
                'opportunities': opps,
            }
            self._emit('defensive_fire_pending', player=self.pending_df['player'],
                       mover=info['unit_id'], step_from=list(info['step_from']), step_to=list(info['step_to']),
                       defenders=[o.defender_id for o in opps])
        if phase == VANGUARD_PHASE and result.success and isinstance(action, MoveAction):
            us = self.game_state.get_unit_state(action.unit_id)
            if us:
                us.has_moved = False   # vanguard move doesn't use up turn-1 movement
            self._vanguard_moved.add(action.unit_id)
        self._check_elimination()
        return result

    def end_phase(self):
        """Finish the current phase and advance (runs casualty / new turn as needed)."""
        cur = self.current()
        if not cur or self.pending_df:
            return
        phase, player = cur
        if phase in (INITIATIVE_PHASE, DEPLOY_ORDER_PHASE):
            return    # must be answered with choose_order()/choose_deploy_order()
        if phase == DEPLOYMENT_PHASE and self._undeployed(player):
            return    # every unit must be placed before the phase can end
        self._exit_phase(phase, player)
        self.phase_idx += 1
        if self.phase_idx >= len(self.phase_queue):
            if phase == DEPLOYMENT_PHASE:
                self._after_deployment()
            elif phase == VANGUARD_PHASE:
                self._start_turn()
            else:
                self._end_turn()
        else:
            self._enter_phase()

    def resume_defensive_fire(self, decisions: Dict[str, str]):
        """
        Answer a pending defensive-fire decision ({defender_id: 'from'|'to'|'hold'})
        and continue the interrupted move from where it paused.
        """
        pend = self.pending_df
        if not pend:
            return
        self.pending_df = None
        orig = pend['action']
        path = pend['remaining_path']
        cont = MoveAction(orig.unit_id, path[0][0], path[0][1], path[-1][0], path[-1][1],
                          path=list(path), movement_cost=getattr(orig, 'movement_cost', 0),
                          is_strike_and_fade=getattr(orig, 'is_strike_and_fade', False),
                          is_relocate=getattr(orig, 'is_relocate', False),
                          max_speed=getattr(orig, 'max_speed', None))
        # defenders not answered keep the automatic choice; unanswered 'ask' would re-pause
        self.executor.df_decisions = {o.defender_id: decisions.get(o.defender_id, 'auto')
                                      for o in pend['opportunities']}
        try:
            self.apply(cont)
        finally:
            self.executor.df_decisions = {}

    def run_until_human(self, max_actions_per_phase: Optional[int] = None) -> List[dict]:
        """
        Drive AI phases and skip empty human phases until a human has
        something to do or the game ends. Returns the events produced.
        """
        start = len(self.events)
        if not self._started:
            self.start()
        guard = 0
        while not self.game_over and guard < 1000 and not self.pending_df:
            guard += 1
            cur = self.current()
            if cur is None:
                break
            phase, player = cur
            if self.is_human(player):
                if phase in (INITIATIVE_PHASE, DEPLOY_ORDER_PHASE) or self.legal_actions():
                    break
                self._emit('skip', phase=phase, player=player, reason='no legal actions')
                self.end_phase()
            else:
                self._run_ai_phase(player, max_actions_per_phase)
                if not self.game_over:
                    self.end_phase()
        return self.events[start:]

    def run_game(self, max_actions_per_phase: Optional[int] = None) -> dict:
        """Play to completion (all players must be agents). Returns the result dict."""
        assert all(a is not None for a in self.players.values()), "run_game needs agents for both players"
        self.run_until_human(max_actions_per_phase)
        assert self.result is not None
        return self.result

    # ------------------------------------------------------------------
    # Snapshots (undo)
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            'game_state': self.game_state.clone(),
            'phase_queue': list(self.phase_queue),
            'phase_idx': self.phase_idx,
            'turn_order': list(self.turn_order),
            'events_len': len(self.events),
            'events_tail': None,
            'result': dict(self.result) if self.result else None,
            'vanguard_moved': set(self._vanguard_moved),
            'started': self._started,
            'initiative_winner': getattr(self, 'initiative_winner', None),
        }

    def restore(self, snap: dict):
        self.game_state = snap['game_state'].clone()
        self.phase_queue = list(snap['phase_queue'])
        self.phase_idx = snap['phase_idx']
        self.turn_order = list(snap['turn_order'])
        del self.events[snap['events_len']:]
        self.result = dict(snap['result']) if snap['result'] else None
        self._vanguard_moved = set(snap['vanguard_moved'])
        self._started = snap['started']
        self.initiative_winner = snap.get('initiative_winner')

    # ------------------------------------------------------------------
    # Turn structure
    # ------------------------------------------------------------------

    def _start_turn(self):
        """Roll initiative. The winner chooses to go first or second (rulebook):
        an AI decides at once; a human gets an INITIATIVE_PHASE to answer in."""
        gs = self.game_state
        self._emit('turn_start', turn=gs.turn_number)
        winner, p1_res, p2_res = self._roll_initiative_winner(gs)
        self._emit('initiative', turn=gs.turn_number, winner=winner,
                   rolls={"player1": self._init_result_dict(p1_res),
                          "player2": self._init_result_dict(p2_res)})
        self.initiative_winner = winner
        agent = self.players.get(winner)
        if agent is None:
            self.phase_queue = [(INITIATIVE_PHASE, winner)]
            self.phase_idx = 0
            gs.active_player = winner
            self._emit('phase', phase=INITIATIVE_PHASE, player=winner)
            return
        chooser = getattr(agent, 'choose_initiative', None)
        goes_first = True
        if chooser is not None:
            try:
                goes_first = chooser(gs, winner) != 'second'
            except Exception:
                goes_first = True
        self.choose_order(winner if goes_first else self._other(winner))

    def _roll_initiative_winner(self, gs):
        for _ in range(20):
            p1 = self.initiative.roll_initiative(gs, "player1")
            p2 = self.initiative.roll_initiative(gs, "player2")
            if p1.final_total != p2.final_total:
                return ("player1" if p1.final_total > p2.final_total else "player2"), p1, p2
            if p1.commander_bonus != p2.commander_bonus:
                return ("player1" if p1.commander_bonus > p2.commander_bonus else "player2"), p1, p2
            self._emit('initiative_reroll', turn=gs.turn_number)
        return "player1", p1, p2

    @staticmethod
    def _other(player: str) -> str:
        return "player2" if player == "player1" else "player1"

    def choose_order(self, first: str):
        """The initiative winner's decision: who is the first player this turn."""
        gs = self.game_state
        self.turn_order = [first, self._other(first)]
        self._emit('turn_order', turn=gs.turn_number, first=first, chosen_by=getattr(self, 'initiative_winner', first))

        self.phase_queue = []
        for p in self.turn_order:
            self.phase_queue.append((GamePhase.MOVEMENT, p))
        for p in self.turn_order:
            self.phase_queue.append((GamePhase.FLIGHT, p))
        for p in self.turn_order:
            self.phase_queue.append((GamePhase.ASSAULT, p))
        for p in self.turn_order:
            self.phase_queue.append((GamePhase.AIRSTRIKE, p))
        self.phase_idx = 0
        self._enter_phase()

    def _enter_phase(self):
        """Apply phase-start rules; skip phases that don't apply."""
        while self.phase_idx < len(self.phase_queue):
            phase, player = self.phase_queue[self.phase_idx]
            gs = self.game_state
            if phase == GamePhase.FLIGHT and not self._has_aircraft(player, on_map=False):
                self.phase_idx += 1
                continue
            if phase == GamePhase.AIRSTRIKE and not self._has_aircraft(player, on_map=True):
                self.phase_idx += 1
                continue
            gs.current_phase = (GamePhase.MOVEMENT if phase == VANGUARD_PHASE
                                else GamePhase.DEPLOYMENT if phase == DEPLOYMENT_PHASE else phase)
            gs.active_player = player
            self._actions_this_phase = 0
            if phase == GamePhase.MOVEMENT:
                self.executor.reset_defensive_fire_phase(gs)
                self._apply_exert_will(player)
            self._emit('phase', phase=phase, player=player)
            return
        # queue exhausted
        if self.phase_queue and self.phase_queue[-1][0] == DEPLOYMENT_PHASE:
            self._after_deployment()
        elif self.phase_queue and self.phase_queue[-1][0] == VANGUARD_PHASE:
            self._start_turn()
        else:
            self._end_turn()

    def _exit_phase(self, phase: str, player: str):
        if phase == GamePhase.MOVEMENT:
            self._apply_hard_charger(player)

    def _end_turn(self):
        gs = self.game_state
        results = self.executor.resolve_casualty_phase(gs)
        self._emit('casualty',
                   destroyed=[(uid, _unit_name(gs, uid)) for uid in results.get('units_destroyed', [])],
                   damaged=list(results.get('units_damaged', [])),
                   disrupted=list(results.get('units_disrupted', [])),
                   cleared=list(results.get('disruption_cleared', [])))

        if self._check_elimination():
            return

        # Rulebook "How to Win": at the end of turn 7 (and every turn after)
        # the player who controls the objective wins; at the end of turn 10
        # the higher surviving point total wins; if still tied, keep playing
        # until a turn ends with a controller or a points lead.
        if gs.turn_number >= OBJECTIVE_CHECK_TURN:
            controller = gs.check_objective_control()
            if controller:
                self._finish(controller, 'objective')
                return

        if gs.turn_number >= POINTS_CHECK_TURN:
            p1 = gs.get_total_points("player1")
            p2 = gs.get_total_points("player2")
            if p1 != p2:
                self._finish("player1" if p1 > p2 else "player2", 'points')
                return
            if gs.turn_number >= self.max_turns:
                self._finish(None, 'draw')
                return

        # End-of-turn housekeeping
        for us in gs.units.values():
            us.reset_for_turn()
        gs.smoke_screens.clear()
        gs.units_destroyed_last_turn = gs.units_destroyed_this_turn
        gs.units_destroyed_this_turn = {"player1": [], "player2": []}
        self._emit('turn_end', turn=gs.turn_number)
        gs.turn_number += 1
        self._start_turn()

    def _check_elimination(self) -> bool:
        winner = self.game_state.check_victory_conditions()
        if winner:
            self._finish(winner, 'elimination')
            return True
        return False

    def _finish(self, winner: Optional[str], reason: str):
        gs = self.game_state
        gs.game_over = True
        gs.winner = winner
        self.result = {
            'winner': winner or 'draw',
            'reason': reason,
            'turns': gs.turn_number,
            'p1_remaining': len([u for u in gs.get_units_by_owner("player1") if u.is_alive]),
            'p2_remaining': len([u for u in gs.get_units_by_owner("player2") if u.is_alive]),
            'p1_points': gs.get_total_points("player1"),
            'p2_points': gs.get_total_points("player2"),
        }
        self._emit('game_over', **self.result)

    # ------------------------------------------------------------------
    # AI driving
    # ------------------------------------------------------------------

    def _run_ai_phase(self, player: str, max_actions: Optional[int]):
        agent = self.players[player]
        gs = self.game_state
        if self.current_phase() == DEPLOYMENT_PHASE:
            self._ai_deploy(player, agent)
            return
        if max_actions is None:
            n_units = len([u for u in gs.get_units_by_owner(player) if u.is_alive])
            max_actions = max(20, 3 * n_units)
        for _ in range(max_actions):
            if self.game_over or self.pending_df:
                return
            legal = self.legal_actions()
            if not legal:
                return
            legal = legal + [PassAction(player)]
            action = agent.choose_action(gs, legal, player)
            if action is None or isinstance(action, (PassAction, EndPhaseAction)):
                return
            self.apply(action)   # a failed action is logged; keep going

    # ------------------------------------------------------------------
    # Special phases / abilities
    # ------------------------------------------------------------------

    def _vanguard_actions(self, player: str) -> List[Action]:
        gs = self.game_state
        actions: List[Action] = []
        if self.movement_system is None:
            return actions
        for us in gs.get_units_by_owner(player):
            if (not us.is_alive or us.unit.id in self._vanguard_moved
                    or not self._has_ability(us, 'vanguard')):
                continue
            q, r = us.position
            reachable = self.movement_system.get_reachable_hexes(gs.board, q, r, us.unit, max_speed=4)
            for (dq, dr) in reachable:
                if (dq, dr) != (q, r):
                    actions.append(MoveAction(unit_id=us.unit.id, from_q=q, from_r=r,
                                              to_q=dq, to_r=dr, max_speed=4))
        return actions

    def _ai_deploy(self, player: str, agent):
        """Default AI deployment: slow/indirect units at the back, others toward the
        front of the zone, in cover when available, spread across the rows."""
        import random as _r
        gs = self.game_state
        chooser = getattr(agent, 'choose_deployment', None)
        for us in list(self._undeployed(player)):
            legal = [a for a in self._deployment_actions(player) if a.unit_id == us.unit.id]
            if not legal:
                us.is_deployed = True   # nowhere to put it; treat as lost (shouldn't happen)
                continue
            if chooser is not None:
                action = chooser(gs, us, legal)
            else:
                speed = us.unit.speed if isinstance(us.unit.speed, int) else 0
                back = speed == 0 or any('indirect' in a.lower() or 'artillery' in (us.unit.unit_type or '').lower()
                                          for a in (us.unit.abilities or []))
                def score(a):
                    col = gs.board.axial_to_offset(a.to_q, a.to_r)[0]
                    depth = col if player == 'player1' else gs.board.width - 1 - col   # 0 = own edge
                    terrain = gs.board.get_hex(a.to_q, a.to_r).terrain
                    s = (-depth if back else depth) * 2.0
                    if terrain in ('forest', 'hill', 'town', 'building'):
                        s += 3 if 'Vehicle' not in (us.unit.unit_type or '') else 1
                    if terrain == 'road':
                        s += 1 if 'Vehicle' in (us.unit.unit_type or '') else 0
                    s -= 2 * len([u for u in gs.get_units_at_position(a.to_q, a.to_r) if u.owner == player])
                    return s + _r.random()
                action = max(legal, key=score)
            self.apply(action)

    def _undeployed(self, player: str):
        """Units that must be placed in the setup deployment (Paratroopers and
        Heroes arrive later, during movement phases)."""
        return [us for us in self.game_state.get_units_by_owner(player)
                if us.is_alive and not us.is_deployed and 'Aircraft' not in (us.unit.unit_type or '')
                and not self._has_ability(us, 'paratrooper')
                and not any(a.lower().endswith(' hero') for a in (us.unit.abilities or []))]

    def _deployment_actions(self, player: str) -> List[Action]:
        gs = self.game_state
        actions: List[Action] = []
        for us in self._undeployed(player):
            is_vehicle = 'Vehicle' in (us.unit.unit_type or '')
            for (q, r) in gs.deploy_zone_for(us):
                h = gs.board.get_hex(q, r)
                if h.terrain in ('water', 'impassable') or (is_vehicle and h.terrain == 'marsh'):
                    continue
                if not gs.can_stack_at(q, r, player, us.unit.unit_type, exclude_unit_id=us.unit.id):
                    continue
                actions.append(DeployAction(us.unit.id, q, r, setup=True))
        return actions

    def _apply_exert_will(self, player: str):
        """Start of movement: Exert Will removes Disrupted from adjacent friendly Soldiers."""
        gs = self.game_state
        units = [u for u in gs.get_units_by_owner(player) if u.is_alive]
        for us in units:
            if not self._has_ability(us, 'exert will'):
                continue
            for friend in units:
                if friend is us or friend.unit.unit_type != 'Soldier' or not friend.is_disrupted:
                    continue
                if gs.board.hex_distance(*us.position, *friend.position) == 1:
                    friend.is_disrupted = False
                    self._emit('status', unit=friend.unit.id, name=friend.unit.name,
                               change='disruption_cleared', cause='Exert Will')

    def _apply_hard_charger(self, player: str):
        """End of movement: Hard Charger units shed Disrupted."""
        for us in self.game_state.get_units_by_owner(player):
            if us.is_alive and us.is_disrupted and self._has_ability(us, 'hard charger'):
                us.is_disrupted = False
                self._emit('status', unit=us.unit.id, name=us.unit.name,
                           change='disruption_cleared', cause='Hard Charger')

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_ability(unit_state, name: str) -> bool:
        abilities = getattr(unit_state.unit, 'abilities', []) or []
        return any(a.lower() == name for a in abilities)

    def _any_unit_has_ability(self, name: str) -> bool:
        return any(us.is_alive and self._has_ability(us, name)
                   for us in self.game_state.units.values())

    def _has_aircraft(self, player: str, on_map: bool) -> bool:
        return any(u.is_alive and u.unit.unit_type == 'Aircraft' and u.is_aircraft_on_map == on_map
                   for u in self.game_state.get_units_by_owner(player))

    @staticmethod
    def _init_result_dict(res) -> dict:
        return {
            'dice': list(getattr(res, 'dice', ())),
            'base': getattr(res, 'base_total', None),
            'commander': getattr(res, 'commander_bonus', 0),
            'recon': getattr(res, 'recon_bonus', 0),
            'reroll': getattr(res, 'organization_reroll', False),
            'total': getattr(res, 'final_total', None),
            'text': str(res),
        }

    def _record_action(self, player: str, action: Action, result):
        ev = {
            'type': 'action',
            'player': player,
            'action_type': type(action).__name__,
            'unit': getattr(action, 'unit_id', None),
            'unit_name': _unit_name(self.game_state, getattr(action, 'unit_id', '') or ''),
            'success': result.success,
            'message': result.message,
        }
        if isinstance(action, MoveAction):
            ev.update({'from': [action.from_q, action.from_r], 'to': [action.to_q, action.to_r]})
        if isinstance(action, AttackAction):
            ev.update({'target': action.target_id, 'target_hex': [action.target_q, action.target_r]})
        if getattr(result, 'unit_destroyed', None):
            ev['unit_destroyed'] = result.unit_destroyed
        details = getattr(result, 'combat_details', None)
        if details:
            ev['combat'] = {k: v for k, v in details.items()
                            if k in ('attack_dice', 'attack_rolls', 'hit_threshold', 'successes',
                                     'defense', 'hits', 'outcome', 'cover_rolled', 'cover_roll',
                                     'cover_threshold', 'cover_success', 'target_new_status',
                                     'target_destroyed', 'notes', 'pending_counters',
                                     'pending_destroyed')}
        dfr = getattr(result, 'defensive_fire_results', None) or []
        if dfr:
            ev['defensive_fire'] = [{
                'defender': r.defender_id, 'rolls': list(getattr(r, 'rolls', [])),
                'successes': getattr(r, 'successes', 0), 'hit': getattr(r, 'hit', False),
                'cover_roll': getattr(r, 'cover_roll', None), 'cover_success': getattr(r, 'cover_success', None),
                'disrupted': getattr(r, 'target_disrupted', False),
                'movement_stopped': getattr(r, 'movement_stopped', False),
                'message': getattr(r, 'message', ''),
            } for r in dfr]
        extra = getattr(result, 'events', None)
        if extra:
            ev['events'] = list(extra)
        self.events.append(jsonable(ev))

    def _emit(self, type_: str, **fields):
        ev = {'type': type_}
        ev.update(fields)
        self.events.append(jsonable(ev))


# ----------------------------------------------------------------------
# Log formatting (text view of events, used by the server log and runner)
# ----------------------------------------------------------------------

def format_event(ev: dict) -> Optional[str]:
    t = ev.get('type')
    if t == 'turn_start':
        return f"▶ Turn {ev['turn']}"
    if t == 'initiative':
        lines = [f"  🎲 {ev['rolls']['player1']['text']}",
                 f"  🎲 {ev['rolls']['player2']['text']}",
                 f"  → {ev['winner']} wins the initiative"]
        return "\n".join(lines)
    if t == 'turn_order':
        return f"  → {ev['first']} goes first"
    if t == 'coin_flip':
        return f"🪙 Coin flip: {ev['winner']} chooses who deploys first"
    if t == 'deploy_order':
        return f"  → {ev['first']} deploys first"
    if t == 'initiative_reroll':
        return "  🎲 tie — reroll"
    if t == 'phase':
        return f"— {ev['player']} {ev['phase']} phase —"
    if t == 'skip':
        return f"  {ev['player']} has no {ev['phase']} actions — skipping"
    if t == 'action':
        prefix = "  " if ev.get('success') else "  ✗ "
        line = f"{prefix}{ev.get('message', '')}"
        if ev.get('unit_destroyed'):
            line += f"\n    💥 {ev['unit_destroyed']} destroyed"
        return line
    if t == 'status':
        return f"  ✓ {ev.get('name', ev.get('unit'))}: {ev.get('change')} ({ev.get('cause')})"
    if t in ('facing', 'hold_fire'):
        return f"  {ev.get('message', '')}"
    if t == 'casualty':
        parts = []
        for uid, name in ev.get('destroyed', []):
            parts.append(f"  💥 {name} destroyed")
        for uid in ev.get('damaged', []):
            parts.append(f"  🔧 {uid} damaged")
        for uid in ev.get('disrupted', []):
            parts.append(f"  ⚡ {uid} disrupted")
        for uid in ev.get('cleared', []):
            parts.append(f"  ✓ {uid} disruption cleared")
        return "\n".join(parts) if parts else "  (no casualties)"
    if t == 'turn_end':
        return f"— end of turn {ev['turn']} —"
    if t == 'game_over':
        return f"🏁 Game over — {ev['winner']} ({ev['reason']})"
    return None
