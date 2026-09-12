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

from action import (MoveAction, AttackAction, UseAbilityAction,
                    BoardTransportAction, DismountTransportAction)
from evaluation import GameStateEvaluator
from game_runner import AggressiveRandomAgent, GreedyAgent, RandomAgent
from game_setup import load_all_units, GameSetup, GameSetupConfig
from game_state import GameState, GamePhase, UnitState
from scenario import build_action, build_systems, find_legal_action, load_scenario, ABILITY_CSV
from turn_controller import TurnController, format_event, VANGUARD_PHASE

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
SCENARIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scenarios')

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='/static')
_session_lock = threading.Lock()
_session: Optional['GameSession'] = None

PHASE_LABELS = {
    'movement': 'Movement', 'assault': 'Assault', 'flight': 'Flight',
    'airstrike': 'Airstrike', 'deployment': 'Deployment', VANGUARD_PHASE: 'Vanguard',
}
PHASE_HINTS = {
    'movement': 'Select a unit, then click a green hex to move. Yellow = board transport, orange OUT = dismount, purple = ability. Vehicles roll 4+ to enter forest.',
    'assault': 'Select a unit: red ⚔ = attack, orange IDF = indirect fire via spotter (no LOS needed), green = move instead of attacking. LOS checkbox shades what the unit cannot see.',
    'flight': 'Place aircraft on the board.',
    'airstrike': 'Aircraft attack.',
    VANGUARD_PHASE: 'Pre-game Vanguard move (speed 4).',
}
DICE_EVENT_TYPES = {'attack', 'cover_save', 'movement_roll', 'defensive_fire', 'initiative', 'casualty'}


# ---------------------------------------------------------------------------
# Game session
# ---------------------------------------------------------------------------

class GameSession:
    """One game: engine systems + TurnController + undo stacks."""

    def __init__(self, mode: str = 'vs_ai', ai_type: str = 'aggressive',
                 points: int = 100, seed: Optional[int] = None,
                 scenario: Optional[str] = None):
        self.mode = mode
        self.ai_type = ai_type
        self.seed = seed if seed is not None else random.randrange(1, 10 ** 6)
        random.seed(self.seed)

        if scenario:
            sc = load_scenario(os.path.join(SCENARIO_DIR, scenario))
            self.systems = sc.systems
            game_state = sc.game_state
            self.scenario_name = sc.name
        else:
            self.systems = build_systems(seed=self.seed)
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

        self.controller = TurnController(
            game_state, self.systems.executor, self.systems.generator,
            self.systems.initiative, players, movement_system=self.systems.movement,
        )
        self.pending_facing: Optional[str] = None
        self._undo_stack: list = []
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
        return AggressiveRandomAgent(name)

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

        setup = GameSetup(GameSetupConfig(points_per_side=100))
        board = setup.create_board(terrain_density=0.15)

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
                         objective_position=board.center())

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
                'human_players': [p for p, a in self.players.items() if a is None],
                'current_player': player,
                'current_phase': phase,
                'phase_label': PHASE_LABELS.get(phase, phase or ''),
                'phase_hint': PHASE_HINTS.get(phase, ''),
                'is_human_turn': self.is_human_turn(),
                'pending_facing': self.pending_facing,
                'can_undo': bool(self._undo_stack) and self.is_human_turn(),
                'can_redo': bool(self._redo_stack) and self.is_human_turn(),
                'game_over': self.controller.game_over,
                'result': self.controller.result,
                'turn_order': list(self.controller.turn_order),
                'events_total': len(self.controller.events),
            },
            'unit_actions': self.compute_unit_actions(),
            'log': self.log_lines(),
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
                'moves': [], 'attacks': [], 'board': [], 'dismount': [], 'abilities': [],
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
                entry['moves'].append([action.to_q, action.to_r])
            elif isinstance(action, AttackAction):
                if getattr(action, 'improvised_attack', None) or any(
                        getattr(action, f, False) for f in ('is_rocket_salvo', 'is_rockets_8', 'is_top_mounted_rockets',
                                                           'is_bombs', 'is_remote_control', 'is_additional_hull_cannon',
                                                           'is_extra_hull_cannon')):
                    continue   # special attack variants: not exposed in the UI yet
                entry['attacks'].append({'q': action.target_q, 'r': action.target_r,
                                         'target_id': action.target_id, 'distance': action.distance,
                                         'indirect': bool(getattr(action, 'indirect_fire', False))})
            elif isinstance(action, BoardTransportAction):
                entry['board'].append({'q': action.position_q, 'r': action.position_r,
                                       'transport_id': action.transport_id})
            elif isinstance(action, DismountTransportAction):
                entry['dismount'].append({'q': action.to_q, 'r': action.to_r,
                                          'transport_id': action.transport_id})
        return out

    # -- undo -------------------------------------------------------------

    def _snapshot(self):
        return {'controller': self.controller.snapshot(), 'pending_facing': self.pending_facing}

    def _restore(self, snap):
        self.controller.restore(snap['controller'])
        self.pending_facing = snap['pending_facing']

    def _push_undo(self):
        self._undo_stack.append(self._snapshot())
        self._redo_stack.clear()

    def _clear_undo(self):
        self._undo_stack.clear()
        self._redo_stack.clear()

    # -- actions ----------------------------------------------------------

    def execute(self, data: dict) -> dict:
        """Apply one request; returns {success, events} or {error}."""
        kind = data.get('type')
        start = len(self.controller.events)

        if kind == 'undo':
            if not self._undo_stack:
                return {'error': 'Nothing to undo'}
            self._redo_stack.append(self._snapshot())
            self._restore(self._undo_stack.pop())
            return {'success': True, 'events': []}

        if kind == 'redo':
            if not self._redo_stack:
                return {'error': 'Nothing to redo'}
            self._undo_stack.append(self._snapshot())
            self._restore(self._redo_stack.pop())
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

        if kind == 'set_facing':
            uid = data.get('unit_id')
            if not uid or uid != self.pending_facing:
                return {'error': 'No facing selection expected'}
            self._push_undo()
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
        if not self.is_human_turn():
            return {'error': 'Not your turn'}

        if kind == 'pass':
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

        self._push_undo()
        result = self.controller.apply(action)
        if not result.success:
            self._undo_stack.pop()
            del self.controller.events[start:]
            return {'error': result.message}

        # No undo once dice were rolled
        if any(e.get('type') in DICE_EVENT_TYPES
               for ev in self.controller.events[start:] for e in [ev] + ev.get('events', [])):
            self._clear_undo()

        if isinstance(action, MoveAction):
            us = self.game_state.get_unit_state(action.unit_id)
            if us and 'Vehicle' in (us.unit.unit_type or '') and us.is_alive:
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
                ai_type=data.get('ai', 'aggressive'),
                points=int(data.get('points', 100)),
                seed=int(data['seed']) if data.get('seed') not in (None, '') else None,
                scenario=data.get('scenario') or None,
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'Could not start game: {e}'}), 400
        return jsonify({'success': True, 'state': _session.state_payload()})


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


@app.route('/api/abilities')
def api_abilities():
    return jsonify(ABILITY_DESCRIPTIONS)


@app.route('/api/scenarios')
def api_scenarios():
    files = sorted(glob.glob(os.path.join(SCENARIO_DIR, '**', '*.yaml'), recursive=True))
    return jsonify([os.path.relpath(f, SCENARIO_DIR) for f in files])


if __name__ == '__main__':
    print("Axis & Allies Miniatures — http://localhost:8080")
    app.run(debug=False, port=8080)
