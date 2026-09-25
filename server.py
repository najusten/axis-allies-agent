#!/usr/bin/env python3
"""
Flask JSON API + static frontend for Axis & Allies Miniatures.

    python3 server.py            → http://localhost:8080

Routes
    GET  /                 static/index.html
    GET  /api/state        full state: game, session, unit_actions, log
    POST /api/action       {type, ...} → {success, events, state} | {error}
    POST /api/new_game     {mode, ai, points, seed, scenario} → {state}
    GET  /api/abilities    ability name → description
    GET  /api/scenarios    available scenario files

Modes: vs_ai (human player1 vs AI player2), hotseat (two humans on one
screen), ai_vs_ai (watch; POST /api/action {type: step} advances a phase).
The sequence of play is owned by turn_controller.TurnController.
"""

import csv
import glob
import os
import random
import threading
from copy import deepcopy
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory

from action import (MoveAction, AttackAction, UseAbilityAction, DeployAction,
                    BoardTransportAction, DismountTransportAction, PlaceAircraftAction)
from agents import HeuristicAgent, LookaheadAgent
from evaluation import GameStateEvaluator
from game_runner import AggressiveRandomAgent, GreedyAgent, RandomAgent
from game_setup import load_all_units, GameSetup, GameSetupConfig
from board import Board
from game_state import GameState, GamePhase, UnitState
from scenario import build_action, build_systems, find_legal_action, load_scenario, ABILITY_CSV, SPECIAL_FLAGS
from turn_controller import (_unit_name, TurnController, format_event, VANGUARD_PHASE, INITIATIVE_PHASE,
                             DEPLOYMENT_PHASE, DEPLOY_ORDER_PHASE)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
SCENARIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scenarios')

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='/static')
_session_lock = threading.Lock()
_session: Optional['GameSession'] = None

PHASE_LABELS = {
    'movement': 'Movement', 'assault': 'Assault', 'flight': 'Flight',
    'airstrike': 'Airstrike', 'deployment': 'Deployment', VANGUARD_PHASE: 'Vanguard',
    INITIATIVE_PHASE: 'Initiative', DEPLOYMENT_PHASE: 'Deployment', DEPLOY_ORDER_PHASE: 'Deployment',
}
PHASE_HINTS = {
    'movement': 'Select a unit, then click a green hex to move (hover shows the route; shift-click hexes to set your own route). Yellow = board transport, orange OUT = dismount, purple = ability. Vehicles roll 4+ to enter forest.',
    'assault': 'Select a unit: red ⚔ = attack, orange IDF = indirect fire via spotter (no LOS needed), green = move instead of attacking, green with a red ring = Aggression move (may still attack afterwards). LOS checkbox shades what the unit cannot see.',
    'flight': 'Select an Aircraft in the sidebar, then click any cyan hex to place it. Antiair units may fire at it. Aircraft leave the map at the end of the turn.',
    'airstrike': 'Select an Aircraft on the map and click a red ⚔ hex to attack.',
    VANGUARD_PHASE: 'Pre-game Vanguard move (speed 4).',
    INITIATIVE_PHASE: 'You won the initiative roll: choose whether to go first or second this turn.',
    DEPLOYMENT_PHASE: 'Deploy your army: select a unit in the sidebar, then click a cyan hex within five hexes of your edge. All units must be placed.',
    DEPLOY_ORDER_PHASE: 'You won the coin flip: deploy first, or second (after seeing the opponent\'s setup)?',
}
SPECIAL_LABELS = {
    'rocket_salvo': 'Rocket Salvo', 'rockets_8': 'Rockets 8', 'top_mounted_rockets': 'Top-Mounted Rockets',
    'bombs': 'Bombs', 'remote_control': 'Remote Control', 'additional_hull_cannon': 'Hull-Mounted Cannon',
    'extra_hull_cannon': 'Extra Hull Cannon', 'armor_piercing': 'Armor-Piercing Rounds', 'headshot': 'Headshot',
    'he_round': 'HE Round', 'speed_boost': 'Speed Boost',
}
DICE_EVENT_TYPES = {'attack', 'cover_save', 'movement_roll', 'defensive_fire', 'initiative', 'casualty'}


# ---------------------------------------------------------------------------
# Game session
# ---------------------------------------------------------------------------

class GameSession:
    """One game: engine systems + TurnController + undo stacks."""

    def __init__(self, mode: str = 'vs_ai', ai_type: str = 'heuristic',
                 points: int = 100, seed: Optional[int] = None,
                 scenario: Optional[str] = None, armies: str = 'random',
                 max_year: Optional[int] = None, historical: bool = False,
                 p1_units: Optional[list] = None, p2_units: Optional[list] = None,
                 deploy: bool = True, map_opts: Optional[dict] = None):
        self.mode = mode
        self.ai_type = ai_type
        self.seed = seed if seed is not None else random.randrange(1, 10 ** 6)
        self.setup_info = {'armies': armies, 'points': points, 'max_year': max_year, 'historical': historical}
        random.seed(self.seed)
        self.map_options = self._map_options(map_opts or {})
        self.setup_info['map'] = {'theater': self.map_options.theater, 'density': self.map_options.density}

        if scenario:
            sc = load_scenario(os.path.join(SCENARIO_DIR, scenario))
            self.systems = sc.systems
            game_state = sc.game_state
            self.scenario_name = sc.name
        else:
            self.systems = build_systems(seed=self.seed)
            if armies in ('random', 'custom'):
                cfg = GameSetupConfig(points_per_side=points, historical=historical,
                                      year_range=(1939, max_year) if max_year else None,
                                      map_options=self.map_options)
                setup = GameSetup(cfg)
                game_state = setup.create_game(p1_names=p1_units or None, p2_names=p2_units or None)
                self.setup_info['map']['theater'] = getattr(setup, 'theater', self.map_options.theater)
                if getattr(setup, 'matchup', None):
                    self.setup_info['matchup'] = {'allies': setup.matchup[0], 'axis': setup.matchup[1]}
            else:
                game_state = self._create_showcase_game()
            self.scenario_name = None
        game_state.rng_seed = self.seed

        players = {'player1': None, 'player2': None}
        if mode == 'vs_ai':
            players['player2'] = self._make_agent(ai_type, 'AI')
        elif mode == 'ai_vs_ai':
            players['player1'] = self._make_agent(ai_type, 'AI 1')
            players['player2'] = self._make_agent(ai_type, 'AI 2')
        self.players = players

        # Both sides deploy (rulebook setup: coin flip, then each army within five
        # hexes of its edge). Humans place units by hand, AIs use their policy.
        # Scenarios come pre-placed.
        if not scenario and deploy:
            for us in game_state.units.values():
                if 'Aircraft' not in (us.unit.unit_type or ''):
                    h = game_state.board.get_hex(*us.position)
                    if h is not None and h.unit is us.unit:
                        h.unit = None
                    us.is_deployed = False
                    us.position = (-99, -99)

        self.controller = TurnController(
            game_state, self.systems.executor, self.systems.generator,
            self.systems.initiative, players, movement_system=self.systems.movement,
        )
        for agent in players.values():
            if hasattr(agent, 'attach'):
                agent.attach(self.controller)    # search agents simulate forward from the live game
        self.pending_facing: Optional[str] = None
        self._undo_stack: list = []
        self._undo_actions: list = []
        self._redo_stack: list = []
        if mode != 'ai_vs_ai':
            self.controller.run_until_human()
        else:
            self.controller.start()

    # -- construction ---------------------------------------------------

    def _make_agent(self, kind: str, name: str):
        if kind == 'random':
            return RandomAgent(name)
        if kind == 'greedy':
            return GreedyAgent(name, self.systems.executor, GameStateEvaluator())
        if kind == 'aggressive':
            return AggressiveRandomAgent(name)
        if kind == 'lookahead':
            return LookaheadAgent(name, executor=self.systems.executor, evaluator=GameStateEvaluator(),
                                  movement_system=self.systems.movement)
        if kind == 'mcts':
            from mcts import MCTSAgent
            return MCTSAgent(name, time_limit=1.5, movement_system=self.systems.movement)
        return HeuristicAgent(name, movement_system=self.systems.movement)

    def _create_showcase_game(self) -> GameState:
        """Hand-picked armies that exercise many abilities, on a generated board."""
        unit_map = {u.name: u for u in load_all_units()}
        p1_names = ['Marines M2-2 Flamethrower', 'Hunting Sniper', 'M1 81mm Mortar',
                    '"Red Devil" Captain', 'M1 Garand Rifle', 'Bazooka', 'M18 Hellcat',
                    'Churchill AVRE', 'Inspiring Lieutenant']
        p2_names = ['Veteran Panzer III Ausf. L', 'Flammenwerfer 35', 'MG 42 Machine Gun Team',
                    'Nebelwerfer 41', 'Wehrmacht Oberleutnant', 'Panzerfaust 30',
                    'Panzergrenadier', 'Hummel', 'Mauser Kar 98k']

        def pick(names):
            return [deepcopy(unit_map[n]) for n in names if n in unit_map]

        setup = GameSetup(GameSetupConfig(points_per_side=100, map_options=self.map_options))
        board = setup.create_board()

        def zone(cols):
            hexes = [(q, r) for col in cols for q, r in board.column(col)
                     if board.get_hex(q, r).terrain != 'impassable']
            random.shuffle(hexes)
            return hexes

        def safe_id(name):
            return name.replace(' ', '_').replace('"', '').replace("'", '')

        states = {'player1': [], 'player2': []}
        for owner, names, cols, facing in (('player1', p1_names, range(2), 0),
                                            ('player2', p2_names, range(board.width - 2, board.width), 3)):
            spots = zone(cols)
            for i, unit in enumerate(pick(names)):
                unit.id = f"p{owner[-1]}_{safe_id(unit.name)}_{i}"
                if i >= len(spots):
                    break
                us = UnitState(unit, spots[i], owner, unit.defense_front or 1)
                if 'Vehicle' in (unit.unit_type or ''):
                    us.facing = facing
                states[owner].append(us)
        return GameState(board, states['player1'], states['player2'],
                         objective_position=getattr(setup, 'objective', None) or board.center())

    @staticmethod
    def _map_options(data: dict):
        """New-game map settings -> mapgen.MapOptions. theater 'random' picks one;
        each feature is True/False, or absent to let the theater decide."""
        from mapgen import MapOptions, THEATER_STYLES
        theater = data.get('theater') or 'auto'
        if theater.startswith('map:'):
            return MapOptions(theater=theater)       # a hand-made battlefield: nothing to switch
        if theater == 'random':
            theater = random.choice(sorted(THEATER_STYLES))
        elif theater not in THEATER_STYLES:
            theater = 'auto'      # match the armies (GameSetup decides after drawing them)
        opts = MapOptions(theater=theater, density=data.get('density') or 'normal')
        for feat in ('streams', 'marshes', 'hedges', 'forests', 'hills', 'villages'):
            if feat in data and data[feat] is not None:
                setattr(opts, feat, bool(data[feat]))
        return opts

    # -- views ------------------------------------------------------------

    @property
    def game_state(self) -> GameState:
        return self.controller.game_state

    def is_human_turn(self) -> bool:
        return self.controller.is_human_turn()

    def current_player(self) -> Optional[str]:
        return self.controller.current_player()

    def log_lines(self, limit: int = 80) -> list:
        lines = []
        for ev in self.controller.events:
            line = format_event(ev)
            if line:
                lines.extend(line.split("\n"))
        return lines[-limit:]

    def state_payload(self) -> dict:
        gs = self.game_state
        phase = self.controller.current_phase()
        player = self.current_player()
        return {
            'game': gs.to_dict(),
            'session': {
                'mode': self.mode,
                'ai': self.ai_type if self.mode != 'hotseat' else None,
                'seed': self.seed,
                'scenario': self.scenario_name,
                'setup': self.setup_info,
                'human_players': [p for p, a in self.players.items() if a is None],
                'current_player': player,
                'current_phase': phase,
                'phase_label': PHASE_LABELS.get(phase, phase or ''),
                'phase_hint': PHASE_HINTS.get(phase, ''),
                'is_human_turn': self.is_human_turn(),
                'pending_facing': self.pending_facing,
                'pending_initiative': (player if phase == INITIATIVE_PHASE else None),
                'pending_deploy_order': (player if phase == DEPLOY_ORDER_PHASE else None),
                'pending_defensive_fire': self._pending_df_payload(),
                'deployment_zone': (self._deployment_zone(player) if phase == DEPLOYMENT_PHASE else None),
                'can_undo': bool(self._undo_stack) and self.is_human_turn(),
                'undoable_units': self.undoable_units() if self.is_human_turn() else [],
                'can_redo': bool(self._redo_stack) and self.is_human_turn(),
                'game_over': self.controller.game_over,
                'result': self.controller.result,
                'turn_order': list(self.controller.turn_order),
                'events_total': len(self.controller.events),
            },
            'unit_actions': self.compute_unit_actions(),
            'log': self.log_lines(),
        }

    def _route_budget(self, us, action) -> tuple:
        """(max_speed, is_damaged, minimum_movement) the validator would use for this move."""
        relocate = getattr(action, 'is_relocate', False) or getattr(action, 'is_strike_and_fade', False)
        return (getattr(action, 'max_speed', None), us.is_damaged, not relocate)

    def _check_route(self, action, path: list) -> Optional[str]:
        gs = self.game_state
        us = gs.get_unit_state(action.unit_id)
        if us is None:
            return 'unknown unit'
        if path[0] != tuple(us.position) or path[-1] != (action.to_q, action.to_r):
            return 'Route must start at the unit and end at the destination'
        if len(set(path)) != len(path):
            return 'Route may not visit a hex twice'
        mv = self.systems.movement
        max_speed, damaged, min_move = self._route_budget(us, action)
        cost = mv.path_cost(gs.board, us.unit, path, max_speed=max_speed, is_damaged=damaged,
                            minimum_movement=min_move)
        if cost is None:
            # High Gear: a road-only route may use the bonus speed
            hg = (self.systems.executor.ability_system.get_movement_modifiers(us.unit) or {}).get('high_gear_bonus', 0) \
                if getattr(self.systems.executor, 'ability_system', None) else 0
            if hg:
                base = max_speed or mv.get_effective_speed(us.unit, {})
                cost = mv.path_cost(gs.board, us.unit, path, max_speed=base + hg, road_only=True)
        if cost is None:
            return 'That route is too long or crosses terrain this unit cannot enter'
        return None

    def _deployment_zone(self, player: str) -> list:
        """Hexes where this player's remaining units may still be placed (Partisans:
        board edges; Gliderborne: anywhere outside the enemy zone; others: 5 columns)."""
        gs = self.game_state
        zone = set()
        for us in self.controller._undeployed(player):
            zone.update(gs.deploy_zone_for(us))
        if not zone:
            zone = set(gs.deployment_zone(player))
        return [list(h) for h in sorted(zone)]

    def _pending_df_payload(self):
        pend = self.controller.pending_df
        if not pend:
            return None
        gs = self.game_state
        from facing import calculate_facing_for_defensive_fire, is_front_arc_attack
        from agents import hit_distribution
        mover = gs.get_unit_state(pend['unit_id'])
        options = []
        for o in pend['opportunities']:
            d = o.defender_state
            hexes = []
            steps = (('from', pend['step_from']), ('to', pend['step_to']))
            if tuple(pend['step_from']) == tuple(pend['step_to']):
                steps = (('to', pend['step_to']),)      # aircraft placement: one hex only
            for label, hex_ in steps:
                is_rear = False
                if 'Vehicle' in (mover.unit.unit_type or ''):
                    facing = calculate_facing_for_defensive_fire(pend['step_from'], pend['step_to'])
                    is_rear = not is_front_arc_attack(d.position, hex_, facing)
                defense, _ = self.systems.executor.defensive_fire.get_defense_value(mover.unit, mover, is_rear, gs)
                terrain = gs.board.get_hex(*hex_).terrain
                from board import Board
                cover = Board.gives_cover(terrain, mover.unit.unit_type)
                dist = gs.board.hex_distance(d.position[0], d.position[1], hex_[0], hex_[1])
                dice, _mod = self.systems.executor.defensive_fire.get_attack_dice(d.unit, mover.unit, dist, d)
                p1, p2, p3 = hit_distribution(dice, defense, 5 if (d.is_disrupted or d.is_damaged) else 4)
                p_hit = p1 + p2 + p3
                if cover:
                    p_hit *= (1 - (2 / 6 if 'Vehicle' in (mover.unit.unit_type or '') else 3 / 6))
                hexes.append({'which': label, 'q': hex_[0], 'r': hex_[1], 'terrain': terrain, 'cover': cover,
                              'rear': is_rear, 'defense': defense, 'dice': dice, 'p_disrupt': round(p_hit, 2)})
            suggested_hex = self.systems.executor.defensive_fire.choose_attack_hex(gs, o)
            options.append({
                'defender_id': d.unit.id, 'defender_name': d.unit.name, 'defender_pos': list(d.position),
                'hexes': hexes,
                'suggested': 'from' if (tuple(suggested_hex) == tuple(pend['step_from'])
                                        and len(hexes) > 1) else 'to',
            })
        return {
            'player': pend['player'],
            'kind': pend.get('kind', 'move'),
            'mover_id': mover.unit.id, 'mover_name': mover.unit.name, 'mover_owner': mover.owner,
            'step_from': list(pend['step_from']), 'step_to': list(pend['step_to']),
            'options': options,
        }

    def compute_unit_actions(self) -> dict:
        """Per-unit legal actions for the human whose phase it is (empty otherwise)."""
        gs = self.game_state
        out: dict = {}
        for uid, us in gs.units.items():
            if not us.is_alive:
                continue
            pending = self.systems.executor.casualty_system.get_pending_hits_summary(gs, uid)
            out[uid] = {
                'moves': [], 'aggression': [], 'attacks': [], 'special_attacks': [], 'board': [], 'dismount': [], 'abilities': [], 'place': [], 'deploy': [],
                'pending_hits': pending.get('total', 0),
            }
        if not self.is_human_turn() or self.pending_facing:
            return out
        try:
            legal = self.controller.legal_actions()
        except Exception:
            import traceback
            traceback.print_exc()
            return out
        for action in legal:
            entry = out.get(getattr(action, 'unit_id', None))
            if entry is None:
                continue
            if isinstance(action, UseAbilityAction):
                entry['abilities'].append({
                    'ability': action.ability_name,
                    'target_id': action.target_id,
                    'target': [action.target_q, action.target_r] if action.target_q is not None else None,
                    'parameters': action.parameters or None,
                })
            elif isinstance(action, MoveAction):
                if getattr(action, 'is_aggression', False):
                    entry['aggression'].append([action.to_q, action.to_r])   # move X and still attack
                else:
                    entry['moves'].append([action.to_q, action.to_r])
            elif isinstance(action, AttackAction):
                if getattr(action, 'improvised_attack', None):
                    continue
                special = next((f for f in SPECIAL_FLAGS if getattr(action, f, False)), None)
                if special:
                    # once-per-game / explicit attack options: buttons in the ability panel
                    entry['special_attacks'].append({
                        'special': special[3:], 'label': SPECIAL_LABELS.get(special[3:], special[3:].replace('_', ' ').title()),
                        'q': action.target_q, 'r': action.target_r, 'target_id': action.target_id})
                    continue
                entry['attacks'].append({'q': action.target_q, 'r': action.target_r,
                                         'target_id': action.target_id, 'distance': action.distance,
                                         'indirect': bool(getattr(action, 'indirect_fire', False))})
            elif isinstance(action, BoardTransportAction):
                entry['board'].append({'q': action.position_q, 'r': action.position_r,
                                       'transport_id': action.transport_id})
            elif isinstance(action, DismountTransportAction):
                entry['dismount'].append({'q': action.to_q, 'r': action.to_r,
                                          'transport_id': action.transport_id})
            elif isinstance(action, PlaceAircraftAction):
                entry['place'].append([action.to_q, action.to_r])
            elif isinstance(action, DeployAction):
                entry['deploy'].append([action.to_q, action.to_r])
        return out

    # -- undo -------------------------------------------------------------

    def _snapshot(self):
        return {'controller': self.controller.snapshot(), 'pending_facing': self.pending_facing}

    def _restore(self, snap):
        self.controller.restore(snap['controller'])
        self.pending_facing = snap['pending_facing']

    def _push_undo(self, data: Optional[dict] = None):
        self._undo_stack.append(self._snapshot())
        self._undo_actions.append(dict(data or {}))
        self._redo_stack.clear()

    def _clear_undo(self):
        self._undo_stack.clear()
        self._undo_actions.clear()
        self._redo_stack.clear()

    def undoable_units(self) -> list:
        """Units whose last action can still be taken back individually."""
        return sorted({d.get('unit_id') for d in self._undo_actions if d.get('unit_id')})

    def undo_unit(self, unit_id: str, start: int) -> dict:
        """Take back one unit's actions (its move/deploy/facing/boarding) while
        keeping everything other units did afterwards: rewind to the snapshot
        before that unit's first undoable action and replay the other actions.
        Only possible while no dice have been rolled since (the undo stack is
        cleared otherwise), so the replay is deterministic."""
        idx = next((i for i, d in enumerate(self._undo_actions) if d.get('unit_id') == unit_id), None)
        if idx is None:
            return {'error': 'Nothing to undo for that unit'}
        later = [d for d in self._undo_actions[idx + 1:] if d.get('unit_id') != unit_id]
        backup = (self._snapshot(), list(self._undo_stack), list(self._undo_actions))
        self._restore(self._undo_stack[idx])
        del self._undo_stack[idx:]
        del self._undo_actions[idx:]
        self._redo_stack.clear()
        for d in later:
            res = self.execute(d)
            if 'error' in res:
                self._restore(backup[0])
                self._undo_stack, self._undo_actions = backup[1], backup[2]
                return {'error': f"Cannot undo that unit: a later action depends on it ({res['error']})"}
        ev = {'type': 'undo', 'unit': unit_id,
              'message': f"{_unit_name(self.game_state, unit_id)}: action taken back"}
        self.controller.events.append(ev)
        return {'success': True, 'events': [ev]}

    # -- actions ----------------------------------------------------------

    def execute(self, data: dict) -> dict:
        """Apply one request; returns {success, events} or {error}."""
        kind = data.get('type')
        start = len(self.controller.events)

        if kind == 'undo_unit':
            if not self.is_human_turn():
                return {'error': 'Not your turn'}
            return self.undo_unit(data.get('unit_id'), start)

        if kind == 'undo':
            if not self._undo_stack:
                return {'error': 'Nothing to undo'}
            self._redo_stack.append((self._snapshot(), self._undo_actions.pop()))
            self._restore(self._undo_stack.pop())
            return {'success': True, 'events': []}

        if kind == 'redo':
            if not self._redo_stack:
                return {'error': 'Nothing to redo'}
            snap, action_data = self._redo_stack.pop()
            self._undo_stack.append(self._snapshot())
            self._undo_actions.append(action_data)
            self._restore(snap)
            return {'success': True, 'events': []}

        if self.controller.game_over:
            return {'error': 'Game is over'}

        if kind == 'step':
            # ai_vs_ai: advance one AI phase
            cur = self.controller.current()
            if cur and self.players.get(cur[1]) is not None:
                self.controller._run_ai_phase(cur[1], None)
                if not self.controller.game_over:
                    self.controller.end_phase()
            return {'success': True, 'events': self.controller.events[start:]}

        if kind == 'defensive_fire_decision':
            pend = self.controller.pending_df
            if not pend or self.players.get(pend['player']) is not None:
                return {'error': 'No defensive-fire decision pending'}
            decisions = data.get('decisions') or {}
            self._clear_undo()
            mover_id = pend['unit_id']
            self.controller.resume_defensive_fire(decisions)
            mover = self.game_state.get_unit_state(mover_id)
            # A human vehicle that completed its move (not stopped) still picks a facing
            if (mover and mover.is_alive and self.players.get(mover.owner) is None
                    and 'Vehicle' in (mover.unit.unit_type or '') and not self.controller.pending_df
                    and not mover.is_disrupted):
                self.pending_facing = mover_id
                return {'success': True, 'events': self.controller.events[start:]}
            self.controller.run_until_human()
            return {'success': True, 'events': self.controller.events[start:]}

        if kind == 'choose_deploy_order':
            cur = self.controller.current()
            if not cur or cur[0] != DEPLOY_ORDER_PHASE or self.players.get(cur[1]) is not None:
                return {'error': 'No deployment choice pending'}
            winner = cur[1]
            first = winner if data.get('first', True) else ('player2' if winner == 'player1' else 'player1')
            self._clear_undo()
            self.controller.choose_deploy_order(first)
            self.controller.run_until_human()
            return {'success': True, 'events': self.controller.events[start:]}

        if kind == 'choose_order':
            cur = self.controller.current()
            if not cur or cur[0] != INITIATIVE_PHASE or self.players.get(cur[1]) is not None:
                return {'error': 'No initiative choice pending'}
            winner = cur[1]
            first = winner if data.get('first', True) else ('player2' if winner == 'player1' else 'player1')
            self._clear_undo()
            self.controller.choose_order(first)
            self.controller.run_until_human()
            return {'success': True, 'events': self.controller.events[start:]}

        if kind == 'hold_fire':
            us = self.game_state.get_unit_state(data.get('unit_id', ''))
            if not us or self.players.get(us.owner) is not None:
                return {'error': 'Not one of your units'}
            us.hold_defensive_fire = bool(data.get('hold', True))
            self.controller.events.append({'type': 'hold_fire', 'unit': us.unit.id, 'name': us.unit.name,
                                           'hold': us.hold_defensive_fire,
                                           'message': f"{us.unit.name} will {'hold' if us.hold_defensive_fire else 'use'} defensive fire"})
            return {'success': True, 'events': []}

        if kind == 'set_facing':
            uid = data.get('unit_id')
            if not uid or uid != self.pending_facing:
                return {'error': 'No facing selection expected'}
            self._push_undo(data)
            us = self.game_state.get_unit_state(uid)
            facing = int(data.get('facing', 0))
            us.facing = facing
            from facing import get_direction_name, HexDirection
            self.controller.events.append({
                'type': 'facing', 'unit': uid, 'name': us.unit.name, 'facing': facing,
                'message': f"{us.unit.name} faces {get_direction_name(HexDirection(facing))}"})
            self.pending_facing = None
            self._advance_if_done()
            return {'success': True, 'events': self.controller.events[start:]}

        if self.pending_facing:
            return {'error': 'Choose a facing for your vehicle first'}
        if self.controller.pending_df:
            return {'error': 'Resolve the defensive-fire decision first'}
        if not self.is_human_turn():
            return {'error': 'Not your turn'}

        if kind == 'pass':
            if self.controller.current_phase() in (INITIATIVE_PHASE, DEPLOY_ORDER_PHASE):
                return {'error': 'Choose whether to go first or second'}
            if self.controller.current_phase() == DEPLOYMENT_PHASE:
                return {'error': 'Deploy all your units first'}
            self.pending_facing = None
            self._clear_undo()
            self.controller.end_phase()
            self.controller.run_until_human()
            return {'success': True, 'events': self.controller.events[start:]}

        # Only actions the generator offered are executed (this is what
        # enforces LOS, range, arcs, once-per-game abilities server-side).
        action = find_legal_action(self.controller.legal_actions(), data)
        if action is None:
            return {'error': 'That action is not legal right now'}
        if isinstance(action, MoveAction) and data.get('path'):
            # Player-chosen route (waypoints): must be a legal route for this unit
            path = [tuple(int(x) for x in h) for h in data['path']]
            err = self._check_route(action, path)
            if err:
                return {'error': err}
            action.path = path

        self._push_undo(data)
        result = self.controller.apply(action)
        if not result.success:
            self._undo_stack.pop()
            self._undo_actions.pop()
            del self.controller.events[start:]
            return {'error': result.message}

        # No undo once dice were rolled
        if any(e.get('type') in DICE_EVENT_TYPES
               for ev in self.controller.events[start:] for e in [ev] + ev.get('events', [])):
            self._clear_undo()

        if isinstance(action, (MoveAction, DeployAction)) and not self.controller.pending_df:
            us = self.game_state.get_unit_state(action.unit_id)
            if us and 'Vehicle' in (us.unit.unit_type or '') and us.is_alive and us.is_deployed:
                self.pending_facing = action.unit_id
                return {'success': True, 'events': self.controller.events[start:]}

        self._advance_if_done()
        return {'success': True, 'events': self.controller.events[start:]}

    def _advance_if_done(self):
        if self.pending_facing or self.controller.game_over:
            return
        if self.is_human_turn() and not self.controller.legal_actions():
            self.controller.end_phase()
            self.controller.run_until_human()


# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------

def _load_ability_descriptions() -> dict:
    out = {}
    try:
        with open(ABILITY_CSV) as f:
            for row in csv.DictReader(f):
                name = (row.get('Ability Name') or '').strip()
                desc = (row.get('Description') or '').strip()
                if name and desc:
                    out[name] = desc
    except OSError:
        pass
    return out


ABILITY_DESCRIPTIONS = _load_ability_descriptions()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _get_session() -> GameSession:
    global _session
    if _session is None:
        _session = GameSession()
    return _session


@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')


@app.route('/api/state')
def api_state():
    with _session_lock:
        return jsonify(_get_session().state_payload())


@app.route('/api/action', methods=['POST'])
def api_action():
    data = request.get_json(force=True) or {}
    with _session_lock:
        session = _get_session()
        try:
            result = session.execute(data)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'Internal error: {e}'}), 500
        if 'error' in result:
            return jsonify(result), 400
        result['state'] = session.state_payload()
        return jsonify(result)


@app.route('/api/new_game', methods=['POST'])
def api_new_game():
    global _session
    data = request.get_json(force=True, silent=True) or {}
    with _session_lock:
        try:
            _session = GameSession(
                mode=data.get('mode', 'vs_ai'),
                ai_type=data.get('ai', 'heuristic'),
                points=int(data.get('points', 100)),
                seed=int(data['seed']) if data.get('seed') not in (None, '') else None,
                scenario=data.get('scenario') or None,
                armies=data.get('armies', 'random'),
                p1_units=data.get('p1_units'), p2_units=data.get('p2_units'),
                deploy=bool(data.get('deploy', True)),
                max_year=int(data['max_year']) if data.get('max_year') not in (None, '') else None,
                historical=bool(data.get('historical', False)),
                map_opts=data.get('map') or None,
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'Could not start game: {e}'}), 400
        return jsonify({'success': True, 'state': _session.state_payload(),
                        'events': _session.controller.events})


@app.route('/api/theaters')
def api_theaters():
    """Map styles for the New Game dialog, with each one's default features."""
    from mapgen import THEATER_STYLES
    from maplib import list_maps
    styles = [{
        'id': key, 'label': st.label, 'description': st.description, 'kind': 'generated',
        'defaults': {'streams': st.stream > 0, 'marshes': st.marsh > 0, 'hedges': st.hedges > 0,
                     'forests': st.forest > 0, 'hills': st.hills > 0,
                     'villages': st.villages > 0 or st.central_village},
    } for key, st in THEATER_STYLES.items()]
    maps = [{**m, 'kind': 'map', 'defaults': None} for m in list_maps()]
    return jsonify(styles + maps)


@app.route('/api/suggest')
def api_suggest():
    """AI assist: the heuristic's best action for the human whose turn it is.
    Returns a description plus the same action data the board highlights send."""
    with _session_lock:
        session = _get_session()
        if not session.is_human_turn() or session.controller.pending_df or session.pending_facing:
            return jsonify({'suggestion': None, 'reason': 'nothing to decide right now'})
        gs = session.game_state
        player = session.current_player()
        try:
            legal = session.controller.legal_actions()
        except Exception:
            legal = []
        if not legal:
            return jsonify({'suggestion': None, 'reason': 'no legal actions: end the phase'})
        advisor = HeuristicAgent('advisor', movement_system=session.systems.movement, randomness=0.0)
        scored = advisor.score_actions(gs, legal, player)
        if not scored or scored[0][0] <= 0:
            return jsonify({'suggestion': None, 'reason': 'nothing worth doing: end the phase'})
        score, action = scored[0]
        us = gs.get_unit_state(action.unit_id)
        name = us.unit.name if us else action.unit_id
        data = None
        if isinstance(action, MoveAction):
            text = f"Move {name} to ({action.to_q},{action.to_r})" + (" (Aggression: still attacks)" if getattr(action, 'is_aggression', False) else "")
            data = {'type': 'move', 'unit_id': action.unit_id, 'to_q': action.to_q, 'to_r': action.to_r,
                    'aggression': bool(getattr(action, 'is_aggression', False))}
        elif isinstance(action, AttackAction):
            ts = gs.get_unit_state(action.target_id)
            text = f"{name}: attack {ts.unit.name if ts else action.target_id} at ({action.target_q},{action.target_r})"
            data = {'type': 'attack', 'unit_id': action.unit_id, 'target_id': action.target_id,
                    'target_q': action.target_q, 'target_r': action.target_r}
        elif isinstance(action, DeployAction):
            text = f"Deploy {name} at ({action.to_q},{action.to_r})"
            data = {'type': 'deploy', 'unit_id': action.unit_id, 'to_q': action.to_q, 'to_r': action.to_r}
        elif isinstance(action, PlaceAircraftAction):
            text = f"Place {name} at ({action.to_q},{action.to_r})"
            data = {'type': 'place', 'unit_id': action.unit_id, 'to_q': action.to_q, 'to_r': action.to_r}
        elif isinstance(action, BoardTransportAction):
            text = f"{name}: board transport"
            data = {'type': 'board_transport', 'unit_id': action.unit_id, 'transport_id': action.transport_id,
                    'pos_q': action.position_q, 'pos_r': action.position_r}
        elif isinstance(action, DismountTransportAction):
            text = f"{name}: dismount at ({action.to_q},{action.to_r})"
            data = {'type': 'dismount', 'unit_id': action.unit_id, 'transport_id': action.transport_id,
                    'to_q': action.to_q, 'to_r': action.to_r}
        elif isinstance(action, UseAbilityAction):
            text = f"{name}: use {action.ability_name}"
            data = {'type': 'use_ability', 'unit_id': action.unit_id, 'ability': action.ability_name,
                    'target_id': action.target_id, 'target_q': action.target_q, 'target_r': action.target_r,
                    'parameters': action.parameters or None}
        else:
            text = f"{name}: {type(action).__name__}"
        hex_ = None
        for key in (('to_q', 'to_r'), ('target_q', 'target_r'), ('pos_q', 'pos_r')):
            if data and data.get(key[0]) is not None:
                hex_ = [data[key[0]], data[key[1]]]
                break
        return jsonify({'suggestion': {'unit_id': action.unit_id, 'text': text, 'score': round(score, 2),
                                       'hex': hex_, 'data': data}})


@app.route('/api/los')
def api_los():
    """Hexes visible from a unit's position: {visible: [[q,r]], blocked: [[q,r]]} (debug aid)."""
    with _session_lock:
        session = _get_session()
        gs = session.game_state
        us = gs.get_unit_state(request.args.get('unit', ''))
        if not us:
            return jsonify({'error': 'unknown unit'}), 400
        q, r = us.position
        visible, blocked = [], []
        for (tq, tr) in gs.board.hexes:
            if (tq, tr) == (q, r):
                visible.append([tq, tr])
                continue
            ok, _ = session.systems.movement.has_line_of_sight(
                gs.board, us.unit, q, r, tq, tr, smoke_screens=gs.smoke_screens)
            (visible if ok else blocked).append([tq, tr])
        return jsonify({'unit': us.unit.id, 'from': [q, r], 'visible': visible, 'blocked': blocked})


@app.route('/api/units')
def api_units():
    """Unit catalog for the army builder: [{name, nation, unit_type, year, cost, speed, defense, abilities, side}]"""
    from game_setup import load_all_units, AXIS_NATIONS, ALLIED_NATIONS
    out = []
    for u in load_all_units():
        side = 'axis' if u.nation in AXIS_NATIONS else 'allies' if u.nation in ALLIED_NATIONS else 'other'
        d = u.to_dict()
        d['side'] = side
        out.append(d)
    out.sort(key=lambda d: (d['side'], d['nation'], d['cost'], d['name']))
    return jsonify(out)


@app.route('/api/path')
def api_path():
    """Route a move would take: {path: [[q,r]...], rolls: [{q,r,reason}]} (hover preview)."""
    with _session_lock:
        session = _get_session()
        gs = session.game_state
        us = gs.get_unit_state(request.args.get('unit', ''))
        if not us:
            return jsonify({'error': 'unknown unit'}), 400
        to_q, to_r = int(request.args.get('q')), int(request.args.get('r'))
        legal = find_legal_action(session.controller.legal_actions(),
                                  {'type': 'move', 'unit_id': us.unit.id, 'to_q': to_q, 'to_r': to_r})
        friendly = {f.position for f in gs.get_units_by_owner(us.owner) if f.is_alive and f.unit.id != us.unit.id}
        mv = session.systems.movement
        # optional waypoints: via=q,r;q,r  -> route through them in order
        via = []
        for part in (request.args.get('via') or '').split(';'):
            if part.strip():
                a, b = part.split(',')
                via.append((int(a), int(b)))
        stops = [tuple(us.position)] + via + [(to_q, to_r)]
        path = [tuple(us.position)]
        for a, b in zip(stops, stops[1:]):
            seg = mv.find_path(gs.board, a[0], a[1], b[0], b[1], us.unit,
                               max_speed=getattr(legal, 'max_speed', None), friendly_positions=friendly)
            path.extend(seg[1:] if seg and seg[0] == a else seg)
        route_ok = True
        if via and legal is not None:
            route_ok = session._check_route(legal, path) is None
        rolls = []
        is_vehicle = 'Vehicle' in (us.unit.unit_type or '')
        for a, b in zip(path, path[1:]):
            h = gs.board.get_hex(*b)
            ha = gs.board.get_hex(*a)
            along_road = gs.board.road_between(a[0], a[1], b[0], b[1])
            if h and h.terrain == 'forest' and is_vehicle and not along_road:
                rolls.append({'q': b[0], 'r': b[1], 'reason': 'forest 4+'})
            kind = gs.board.get_edge_obstacle(a[0], a[1], b[0], b[1])
            if kind and not along_road:
                need = '5+' if kind in Board.EDGE_HEDGE else '4+'
                rolls.append({'q': b[0], 'r': b[1], 'reason': f'{kind} {need}'})
        return jsonify({'path': [list(h) for h in path], 'rolls': rolls, 'legal': route_ok and legal is not None})


@app.route('/api/abilities')
def api_abilities():
    return jsonify(ABILITY_DESCRIPTIONS)


@app.route('/api/scenarios')
def api_scenarios():
    """Playable situations (playable: true) with name/description; test scenarios are omitted."""
    import yaml
    out = []
    for f in sorted(glob.glob(os.path.join(SCENARIO_DIR, '**', '*.yaml'), recursive=True)):
        try:
            with open(f) as fh:
                d = yaml.safe_load(fh) or {}
        except Exception:
            continue
        if d.get('playable'):
            out.append({'file': os.path.relpath(f, SCENARIO_DIR), 'name': d.get('name', f),
                        'description': d.get('description', '')})
    return jsonify(out)


if __name__ == '__main__':
    print("Axis & Allies Miniatures — http://localhost:8080")
    app.run(debug=False, port=8080)
