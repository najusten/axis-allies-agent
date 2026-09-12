#!/usr/bin/env python3
"""
Flask server for Human vs AI Axis & Allies Miniatures.

Usage:
    python3 server.py
Then open http://localhost:5000 in your browser.

Player 1 (blue) = Human
Player 2 (red)  = AI
"""

import json
import os
import threading
from copy import deepcopy
from flask import Flask, jsonify, request

from game_state import GameState, GamePhase
from game_setup import quick_setup_broad, load_all_units, GameSetup, GameSetupConfig
from action_generator import ActionGenerator
from action_executor import ActionExecutor
from abilities import AbilitySystem
from evaluation import GameStateEvaluator
from initiative import InitiativeSystem
from visualization import HTMLGenerator
from action import (MoveAction, AttackAction, PassAction,
                    BoardTransportAction, DismountTransportAction,
                    UseAbilityAction)
from movement import MovementSystem
from game_runner import AggressiveRandomAgent, GreedyAgent, RandomAgent
from turn_controller import TurnController, format_event

# Locate ability CSV (same logic as game_runner.py)
_ABILITY_CSV = (
    'Axis_and_Allies_Unit_Data_for_Analysis_-_Special_Abilities.csv'
    if os.path.exists('Axis_and_Allies_Unit_Data_for_Analysis_-_Special_Abilities.csv')
    else 'Axis and Allies Unit Data for Analysis - Special_Abilities.csv'
)

app = Flask(__name__)
_session_lock = threading.Lock()
_session = None


# ---------------------------------------------------------------------------
# Game Session
# ---------------------------------------------------------------------------

class GameSession:
    """Manages a Human (player1) vs AI (player2) game."""

    def __init__(self, points: int = 100, ai_type: str = 'aggressive'):
        self.ability_system = AbilitySystem(_ABILITY_CSV)
        self.movement_system = MovementSystem(self.ability_system)
        self.action_generator = ActionGenerator(self.movement_system, None, self.ability_system)
        self.action_executor = ActionExecutor(self.movement_system, None, self.ability_system)
        self.initiative_system = InitiativeSystem(self.ability_system, self.movement_system,
                                                  dice=self.action_executor.dice)

        self.human_player = 'player1'
        self.ai_player = 'player2'
        self.pending_facing: str | None = None  # unit_id awaiting facing choice
        self._undo_stack: list = []  # list of (game_state_copy, events_copy, pending_facing) for undo
        self._redo_stack: list = []  # list of snapshots for redo

        # AI agent
        if ai_type == 'random':
            self.ai_agent = RandomAgent("AI (Random)")
        elif ai_type == 'greedy':
            evaluator = GameStateEvaluator()
            self.ai_agent = GreedyAgent("AI (Greedy)", self.action_executor, evaluator)
        else:
            self.ai_agent = AggressiveRandomAgent("AI (Aggressive)")

        # Create game state with curated units that showcase abilities
        game_state: GameState = self._create_showcase_game()

        # Sequence of play lives in TurnController; None = human player
        self.controller = TurnController(
            game_state, self.action_executor, self.action_generator,
            self.initiative_system,
            {self.human_player: None, self.ai_player: self.ai_agent},
            movement_system=self.movement_system,
        )
        self.controller.run_until_human()

    @property
    def game_state(self) -> GameState:
        return self.controller.game_state

    @property
    def events(self) -> list[str]:
        """Text log derived from the controller's structured events."""
        lines = []
        for ev in self.controller.events:
            line = format_event(ev)
            if line:
                lines.extend(line.split("\n"))
        return lines

    def _is_human_turn(self) -> bool:
        return self.controller.is_human_turn()

    def _human_legal_actions(self) -> list:
        return self.controller.legal_actions() if self._is_human_turn() else []

    # ------------------------------------------------------------------
    # Showcase game with curated units
    # ------------------------------------------------------------------

    def _create_showcase_game(self) -> GameState:
        """Create a game with hand-picked units that have interesting abilities."""
        import random
        from game_state import UnitState
        from copy import deepcopy

        all_units = load_all_units()
        unit_map = {u.name: u for u in all_units}

        # Player 1 (Blue/Allies) — interesting ability mix
        p1_names = [
            'Marines M2-2 Flamethrower',   # Flamethrower
            'Hunting Sniper',              # Crack Shot, Superior Camouflage
            'M1 81mm Mortar',              # Improved Indirect Fire, Shrapnel
            '"Red Devil" Captain',         # Commander +2, Close Assault 7
            'M1 Garand Rifle',             # Close Assault 7
            'Bazooka',                     # Close Assault 10 (anti-tank)
            'M18 Hellcat',                 # Strike and Fade
            'Churchill AVRE',              # Blast, AVRE
            'Inspiring Lieutenant',        # Commander +2
        ]

        # Player 2 (Red/Axis) — interesting ability mix
        p2_names = [
            'Veteran Panzer III Ausf. L',  # Smoke Screen
            'Flammenwerfer 35',            # Flamethrower
            'MG 42 Machine Gun Team',      # Double Shot
            'Nebelwerfer 41',              # Blast
            'Wehrmacht Oberleutnant',      # Commander, Close Assault 11
            'Panzerfaust 30',              # Close Assault 11 (anti-tank)
            'Panzergrenadier',             # Close Assault 8
            'Hummel',                      # Blast, Indirect Fire
            'Mauser Kar 98k',              # Close Assault 6
        ]

        def pick_units(names):
            units = []
            for name in names:
                if name in unit_map:
                    units.append(deepcopy(unit_map[name]))
                else:
                    print(f"WARNING: Unit '{name}' not found")
            return units

        p1_units = pick_units(p1_names)
        p2_units = pick_units(p2_names)

        # Create board using GameSetup's terrain generation
        setup = GameSetup(GameSetupConfig(points_per_side=100))
        board = setup.create_board(terrain_density=0.15)

        # Assign unique IDs and place units
        p1_states, p2_states = [], []
        occupied = set()

        # Player 1 starting zone (columns 0-1)
        p1_positions = [(q, r) for q in range(2)
                        for r in range(board.height)
                        if board.get_hex(q, r) and board.get_hex(q, r).terrain != 'impassable']
        random.shuffle(p1_positions)

        # Player 2 starting zone (last 2 columns)
        p2_positions = [(q, r) for q in range(board.width - 2, board.width)
                        for r in range(board.height)
                        if board.get_hex(q, r) and board.get_hex(q, r).terrain != 'impassable']
        random.shuffle(p2_positions)

        def _safe_id(name):
            return name.replace(' ', '_').replace('"', '').replace("'", '')

        for i, unit in enumerate(p1_units):
            unit.id = f"p1_{_safe_id(unit.name)}_{i}"
            if i < len(p1_positions):
                pos = p1_positions[i]
                us = UnitState(unit, pos, 'player1', unit.defense_front or 1)
                if 'Vehicle' in (unit.unit_type or ''):
                    us.facing = 0  # East
                p1_states.append(us)

        for i, unit in enumerate(p2_units):
            unit.id = f"p2_{_safe_id(unit.name)}_{i}"
            if i < len(p2_positions):
                pos = p2_positions[i]
                us = UnitState(unit, pos, 'player2', unit.defense_front or 1)
                if 'Vehicle' in (unit.unit_type or ''):
                    us.facing = 3  # West
                p2_states.append(us)

        obj_pos = (board.width // 2, board.height // 2)
        return GameState(board, p1_states, p2_states, objective_position=obj_pos)

    # ------------------------------------------------------------------
    # Turn / Phase management
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Human action entry point
    # ------------------------------------------------------------------

    def _save_undo_state(self):
        """Save current state for undo."""
        self._undo_stack.append(self._snapshot())
        self._redo_stack.clear()  # New action invalidates redo history

    def _snapshot(self):
        """Create a snapshot of current state."""
        return {
            'controller': self.controller.snapshot(),
            'pending_facing': self.pending_facing,
        }

    def _restore(self, snapshot):
        """Restore state from a snapshot."""
        self.controller.restore(snapshot['controller'])
        self.pending_facing = snapshot['pending_facing']

    def _undo(self) -> bool:
        """Restore the last saved state. Returns True if successful."""
        if not self._undo_stack:
            return False
        # Save current state to redo stack before undoing
        self._redo_stack.append(self._snapshot())
        snapshot = self._undo_stack.pop()
        self._restore(snapshot)
        return True

    def _redo(self) -> bool:
        """Re-apply the last undone action. Returns True if successful."""
        if not self._redo_stack:
            return False
        # Save current state to undo stack before redoing
        self._undo_stack.append(self._snapshot())
        snapshot = self._redo_stack.pop()
        self._restore(snapshot)
        return True

    def execute_human_action(self, data: dict) -> dict:
        """Execute one human action then advance game."""
        action_type = data.get('type')

        # Handle undo
        if action_type == 'undo':
            if self._undo():
                return {"success": True}
            return {"error": "Nothing to undo"}

        # Handle redo
        if action_type == 'redo':
            if self._redo():
                return {"success": True}
            return {"error": "Nothing to redo"}

        # Handle facing selection (vehicle just moved, player picks direction)
        if action_type == 'set_facing':
            self._save_undo_state()
            uid = data.get('unit_id')
            facing = int(data.get('facing', 0))
            if uid and uid == self.pending_facing:
                us = self.game_state.get_unit_state(uid)
                if us:
                    us.facing = facing
                    from facing import get_direction_name, HexDirection
                    self.controller.events.append({
                        'type': 'facing', 'unit': uid, 'name': us.unit.name,
                        'facing': facing,
                        'message': f"{us.unit.name} faces {get_direction_name(HexDirection(facing))}"})
                self.pending_facing = None
                self._advance_if_done()
                return {"success": True}
            return {"error": "No facing selection expected"}

        if self.controller.game_over:
            return {"error": "Game is already over"}

        # Block other actions while facing is pending
        if self.pending_facing:
            return {"error": "Select facing direction for your vehicle first"}

        if not self._is_human_turn():
            return {"error": "Not your turn"}

        if action_type == 'pass':
            self.pending_facing = None
            self._undo_stack.clear()  # Can't undo after ending phase
            self._redo_stack.clear()
            self.controller.end_phase()
            self.controller.run_until_human()
            return {"success": True}

        # Save state before move/attack for undo
        self._save_undo_state()

        action = self._build_action(data)
        if action is None:
            return {"error": "Could not build action from data"}

        result = self.controller.apply(action)
        if not result.success:
            # Roll back the failed attempt so it doesn't linger in the log/undo stack
            self._undo_stack.pop()
            return {"error": result.message}

        # Any action involving dice rolls — no undo (prevents re-rolling)
        # Attacks always involve dice. Moves may involve bog checks or defensive fire.
        # Ability actions like Demolitions involve dice rolls too.
        msg = result.message.lower()
        has_dice = (isinstance(action, AttackAction)
                    or isinstance(action, UseAbilityAction)
                    or 'bogged' in msg or 'defensive fire' in msg
                    or 'roll' in msg or 'DISRUPTED' in result.message
                    or 'MISS' in result.message)
        if has_dice:
            self._undo_stack.clear()
            self._redo_stack.clear()

        # After a vehicle move, prompt for facing
        if isinstance(action, MoveAction):
            us = self.game_state.get_unit_state(action.unit_id)
            if us and 'Vehicle' in (getattr(us.unit, 'unit_type', '') or ''):
                self.pending_facing = action.unit_id
                return {"success": True, "message": result.message}

        self._advance_if_done()
        return {"success": True, "message": result.message}

    def _advance_if_done(self):
        """If the human has nothing left to do this phase, move the game on."""
        if self.pending_facing or self.controller.game_over:
            return
        if self._is_human_turn() and not self.controller.legal_actions():
            self.controller.end_phase()
            self.controller.run_until_human()

    def _build_action(self, data: dict):
        unit_id = data.get('unit_id')
        if not unit_id:
            return None
        unit_state = self.game_state.get_unit_state(unit_id)
        if not unit_state:
            return None
        from_q, from_r = unit_state.position

        if data['type'] == 'move':
            to_q, to_r = int(data['to_q']), int(data['to_r'])
            action = MoveAction(unit_id, from_q, from_r, to_q, to_r)
            if self.game_state.current_phase == GamePhase.ASSAULT:
                # Check if this is a Strike and Fade move (unit just attacked)
                if unit_state.strike_and_fade_available:
                    action.is_strike_and_fade = True
                else:
                    action.is_relocate = True
            return action

        elif data['type'] == 'attack':
            tq, tr = int(data['target_q']), int(data['target_r'])
            defending_id = self._unit_at(tq, tr, prefer_enemy_of=unit_state.owner)
            if not defending_id:
                return None
            # Compute distance and range category
            dist = self.game_state.board.hex_distance(from_q, from_r, tq, tr)
            range_cat = MovementSystem.get_range_category(dist)
            return AttackAction(unit_id, from_q, from_r, defending_id, tq, tr,
                                range_cat, dist)

        elif data['type'] == 'board_transport':
            tid = data.get('transport_id')
            pq, pr = int(data['pos_q']), int(data['pos_r'])
            return BoardTransportAction(unit_id, tid, pq, pr)

        elif data['type'] == 'dismount':
            tid = data.get('transport_id')
            to_q, to_r = int(data['to_q']), int(data['to_r'])
            return DismountTransportAction(unit_id, tid, to_q, to_r)

        elif data['type'] == 'use_ability':
            ability_name = data.get('ability_name')
            target_id = data.get('target_id')
            target_q = int(data['target_q']) if data.get('target_q') is not None else None
            target_r = int(data['target_r']) if data.get('target_r') is not None else None
            return UseAbilityAction(unit_id, ability_name,
                                   target_id=target_id,
                                   target_q=target_q, target_r=target_r)

        return None

    def _unit_at(self, q: int, r: int, prefer_enemy_of: str = None):
        """Find a unit at (q, r). If prefer_enemy_of is set, prefer enemy units."""
        first_found = None
        for uid, us in self.game_state.units.items():
            if us.is_alive and us.position == (q, r):
                if prefer_enemy_of and us.owner != prefer_enemy_of:
                    return uid  # enemy — return immediately
                if first_found is None:
                    first_found = uid
        return first_found

    # ------------------------------------------------------------------
    # HTML generation
    # ------------------------------------------------------------------

    def render_html(self) -> str:
        html = HTMLGenerator().generate_html(self.game_state)
        unit_actions = self._compute_unit_actions()
        html = _inject_unit_actions(html, unit_actions, self.pending_facing)
        html = _inject_server_js(html)
        html = _inject_overlay(html, self)
        return html

    def _compute_unit_actions(self) -> dict:
        """Valid moves/attacks/transport actions for human player, filtered by phase."""
        gs = self.game_state

        # Seed empty entries for every alive unit (needed for JS to find all units)
        unit_actions: dict = {}
        for uid, us in gs.units.items():
            if not us.is_alive:
                continue
            # Get pending hit info from casualty system
            pending = self.action_executor.casualty_system.get_pending_hits_summary(self.game_state, uid)
            pending_count = pending.get('total', 0) if pending else 0
            unit_actions[uid] = {
                'valid_moves': [], 'valid_attacks': [],
                'board_actions': [], 'dismount_actions': [],
                'ability_actions': [],
                'can_move': False, 'can_attack': False,
                'pending_hits': pending_count,
                'is_disrupted': us.is_disrupted,
                'is_damaged': us.is_damaged,
            }

        if not self._is_human_turn():
            return unit_actions
        phase = gs.current_phase

        try:
            all_actions = self.action_generator.get_all_legal_actions(gs, self.human_player)
        except Exception:
            return unit_actions

        for action in all_actions:
            uid = action.unit_id
            if uid not in unit_actions:
                continue
            entry = unit_actions[uid]

            if isinstance(action, UseAbilityAction):
                ability_info = {
                    'ability_name': action.ability_name,
                    'target_id': action.target_id,
                    'target_q': action.target_q,
                    'target_r': action.target_r,
                }
                entry['ability_actions'].append(ability_info)
            elif phase == GamePhase.MOVEMENT:
                if isinstance(action, MoveAction):
                    entry['valid_moves'].append([action.to_q, action.to_r])
                    entry['can_move'] = True
                elif isinstance(action, BoardTransportAction):
                    entry['board_actions'].append({
                        'q': action.position_q, 'r': action.position_r,
                        'transport_id': action.transport_id
                    })
                elif isinstance(action, DismountTransportAction):
                    entry['dismount_actions'].append({
                        'q': action.to_q, 'r': action.to_r,
                        'transport_id': action.transport_id
                    })
            elif phase == GamePhase.ASSAULT:
                if isinstance(action, AttackAction):
                    entry['valid_attacks'].append([action.target_q, action.target_r])
                    entry['can_attack'] = True
                elif isinstance(action, MoveAction):
                    # Relocate moves during assault phase
                    entry['valid_moves'].append([action.to_q, action.to_r])
                    entry['can_move'] = True

        return unit_actions


# ---------------------------------------------------------------------------
# HTML injection helpers
# ---------------------------------------------------------------------------

def _load_ability_descriptions() -> dict:
    """Load ability descriptions from CSV for tooltip display."""
    import csv
    ability_csv = (
        'Axis_and_Allies_Unit_Data_for_Analysis_-_Special_Abilities.csv'
        if os.path.exists('Axis_and_Allies_Unit_Data_for_Analysis_-_Special_Abilities.csv')
        else 'Axis and Allies Unit Data for Analysis - Special_Abilities.csv'
    )
    descriptions = {}
    try:
        with open(ability_csv, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get('Ability Name', '').strip()
                desc = row.get('Description', '').strip()
                if name and desc:
                    descriptions[name.lower()] = desc
    except Exception:
        pass
    return descriptions

_ABILITY_DESCRIPTIONS = _load_ability_descriptions()


def _inject_unit_actions(html: str, unit_actions: dict,
                         pending_facing: str = None) -> str:
    facing_json = json.dumps(pending_facing) if pending_facing else 'null'
    script = (
        '<script>\n'
        f'const UNIT_ACTIONS = {json.dumps(unit_actions)};\n'
        f'const PENDING_FACING = {facing_json};\n'
        f'const ABILITY_DESCS = {json.dumps(_ABILITY_DESCRIPTIONS)};\n'
        '</script>'
    )
    return html.replace('</head>', f'{script}\n</head>')


_SERVER_JS = r"""
<script>
// ─── SERVER-AWARE INTERACTION ───────────────────────────────────────────────
(function() {
    let _selectedId = null;

    // ── Zoom (persisted in sessionStorage) ─────────────────────────────────
    let _zoom = parseFloat(sessionStorage.getItem('aa_zoom')) || 1.0;
    const _ZOOM_MIN = 0.3, _ZOOM_MAX = 3.0, _ZOOM_STEP = 0.15;

    function _applyZoom() {
        const c = document.querySelector('.board-container');
        const b = document.getElementById('game-board');
        if (b) b.style.transform = `scale(${_zoom})`;
        if (b) b.style.transformOrigin = 'top left';
        sessionStorage.setItem('aa_zoom', _zoom);
    }

    // ── Pan (drag-to-scroll) ────────────────────────────────────────────────
    let _isPanning = false, _panStartX = 0, _panStartY = 0, _scrollStartX = 0, _scrollStartY = 0;
    let _isDragging = false;  // true once mouse moves > threshold (distinguishes click from drag)
    const _DRAG_THRESHOLD = 5;  // pixels

    function _setupZoom() {
        const c = document.querySelector('.board-container');
        if (!c) return;
        c.style.overflow = 'auto';
        c.addEventListener('wheel', function(e) {
            e.preventDefault();
            _zoom = Math.min(_ZOOM_MAX, Math.max(_ZOOM_MIN,
                _zoom + (e.deltaY < 0 ? _ZOOM_STEP : -_ZOOM_STEP)));
            _applyZoom();
        }, { passive: false });

        // Drag-to-pan — only starts on empty board area (not on units or highlights)
        c.addEventListener('mousedown', function(e) {
            if (e.button !== 0) return;
            if (e.target.closest('.dyn-hl') || e.target.closest('.unit')) return;
            _isPanning = true;
            _isDragging = false;
            _panStartX = e.clientX; _panStartY = e.clientY;
            _scrollStartX = c.scrollLeft; _scrollStartY = c.scrollTop;
            c.style.cursor = 'grabbing';
            e.preventDefault();
        });
        window.addEventListener('mousemove', function(e) {
            if (!_isPanning) return;
            const dx = e.clientX - _panStartX;
            const dy = e.clientY - _panStartY;
            if (Math.abs(dx) > 3 || Math.abs(dy) > 3) _isDragging = true;
            c.scrollLeft = _scrollStartX - dx;
            c.scrollTop  = _scrollStartY - dy;
        });
        window.addEventListener('mouseup', function() {
            _isPanning = false;
            c.style.cursor = '';
            setTimeout(function() { _isDragging = false; }, 10);
        });
    }

    window.zoomIn  = function() { _zoom = Math.min(_ZOOM_MAX, _zoom + _ZOOM_STEP); _applyZoom(); };
    window.zoomOut = function() { _zoom = Math.max(_ZOOM_MIN, _zoom - _ZOOM_STEP); _applyZoom(); };
    window.zoomReset = function() { _zoom = 1.0; _applyZoom(); };

    // ── Highlights ────────────────────────────────────────────────────────────
    function clearHighlights() {
        document.querySelectorAll('.dyn-hl').forEach(el => el.remove());
        const abilityPanel = document.getElementById('aa-ability-panel');
        if (abilityPanel) abilityPanel.remove();
    }

    function _addHighlight(svg, points, fill, stroke, label, onClick, onTop) {
        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.classList.add('dyn-hl');
        g.style.cursor = 'pointer';
        g.setAttribute('pointer-events', 'all');

        const hl = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
        hl.setAttribute('points', points);
        hl.setAttribute('fill', fill);
        hl.setAttribute('stroke', stroke);
        hl.setAttribute('stroke-width', '3');
        hl.setAttribute('pointer-events', 'all');
        g.appendChild(hl);

        if (label) {
            const pts = points.split(' ').map(p => p.split(',').map(Number));
            const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
            const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
            const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            t.setAttribute('x', cx); t.setAttribute('y', cy + 4);
            t.setAttribute('text-anchor', 'middle');
            t.setAttribute('font-size', '12'); t.setAttribute('font-weight', 'bold');
            t.setAttribute('fill', '#fff'); t.setAttribute('pointer-events', 'none');
            t.textContent = label;
            g.appendChild(t);
        }

        g.addEventListener('click', function(e) {
            if (_isDragging) return;  // ignore drag-releases
            e.stopPropagation();
            e.preventDefault();
            onClick();
        });
        hl.addEventListener('click', function(e) {
            if (_isDragging) return;
            e.stopPropagation();
            e.preventDefault();
            onClick();
        });

        if (onTop) {
            svg.appendChild(g);
        } else {
            const layer = svg.querySelector('.unit-layer');
            if (layer) svg.insertBefore(g, layer); else svg.appendChild(g);
        }
    }

    function _hexPoints(q, r) {
        const hex = document.querySelector(`.hex[data-q="${q}"][data-r="${r}"]`);
        return hex ? hex.getAttribute('points') : null;
    }

    function _showAll(unitId) {
        if (typeof UNIT_ACTIONS === 'undefined' || !UNIT_ACTIONS[unitId]) return;
        const acts = UNIT_ACTIONS[unitId];
        const svg = document.getElementById('game-board');
        if (!svg) return;

        (acts.valid_moves || []).forEach(([q, r]) => {
            const pts = _hexPoints(q, r);
            if (pts) _addHighlight(svg, pts, 'rgba(34,197,94,0.4)', '#22c55e', null,
                () => _doAction({type:'move', unit_id: unitId, to_q: q, to_r: r}));
        });

        (acts.valid_attacks || []).forEach(([q, r]) => {
            const pts = _hexPoints(q, r);
            if (pts) _addHighlight(svg, pts, 'rgba(239,68,68,0.4)', '#ef4444', '⚔',
                () => _doAction({type:'attack', unit_id: unitId, target_q: q, target_r: r}),
                true);  // onTop — must be above unit layer to be clickable
        });

        // Board transport = yellow (infantry boards a nearby transport)
        (acts.board_actions || []).forEach(a => {
            const pts = _hexPoints(a.q, a.r);
            if (pts) _addHighlight(svg, pts, 'rgba(234,179,8,0.5)', '#eab308', 'BOARD',
                () => _doAction({type:'board_transport', unit_id: unitId,
                    transport_id: a.transport_id, pos_q: a.q, pos_r: a.r}));
        });

        // Dismount = orange (infantry exits transport)
        (acts.dismount_actions || []).forEach(a => {
            const pts = _hexPoints(a.q, a.r);
            if (pts) _addHighlight(svg, pts, 'rgba(249,115,22,0.5)', '#f97316', 'OUT',
                () => _doAction({type:'dismount', unit_id: unitId,
                    transport_id: a.transport_id, to_q: a.q, to_r: a.r}));
        });

        // Ability actions = purple buttons in a floating panel
        const abilities = acts.ability_actions || [];
        if (abilities.length > 0) {
            _showAbilityPanel(unitId, abilities);
        }
    }

    function _showAbilityPanel(unitId, abilities) {
        // Remove existing panel
        const old = document.getElementById('aa-ability-panel');
        if (old) old.remove();

        // Group abilities by name (some like Demolitions may have multiple targets)
        const grouped = {};
        abilities.forEach(a => {
            const key = a.ability_name;
            if (!grouped[key]) grouped[key] = [];
            grouped[key].push(a);
        });

        const panel = document.createElement('div');
        panel.id = 'aa-ability-panel';
        panel.style.cssText = 'position:fixed;top:10px;left:50%;transform:translateX(-50%);'
            + 'background:rgba(22,33,62,0.95);border:2px solid #9333ea;border-radius:8px;'
            + 'padding:10px 16px;z-index:3000;display:flex;gap:8px;flex-wrap:wrap;'
            + 'align-items:center;';

        const label = document.createElement('span');
        label.style.cssText = 'color:#c084fc;font-size:13px;font-weight:bold;margin-right:4px;';
        label.textContent = 'Abilities:';
        panel.appendChild(label);

        for (const [name, actions] of Object.entries(grouped)) {
            const prettyName = name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
            if (actions.length === 1) {
                const a = actions[0];
                const btn = document.createElement('button');
                btn.textContent = prettyName;
                btn.style.cssText = 'background:#9333ea;color:white;border:none;'
                    + 'padding:6px 14px;border-radius:4px;cursor:pointer;font-size:12px;'
                    + 'font-weight:bold;';
                btn.addEventListener('click', () => {
                    _doAction({type:'use_ability', unit_id: unitId,
                        ability_name: a.ability_name, target_id: a.target_id,
                        target_q: a.target_q, target_r: a.target_r});
                });
                panel.appendChild(btn);
            } else {
                // Multiple targets — show each with target info
                actions.forEach((a, i) => {
                    const btn = document.createElement('button');
                    btn.textContent = prettyName + (a.target_id ? ` #${i+1}` : '');
                    btn.style.cssText = 'background:#9333ea;color:white;border:none;'
                        + 'padding:6px 14px;border-radius:4px;cursor:pointer;font-size:12px;'
                        + 'font-weight:bold;';
                    btn.addEventListener('click', () => {
                        _doAction({type:'use_ability', unit_id: unitId,
                            ability_name: a.ability_name, target_id: a.target_id,
                            target_q: a.target_q, target_r: a.target_r});
                    });
                    // Highlight target hex on hover if it has coords
                    if (a.target_q != null && a.target_r != null) {
                        btn.addEventListener('mouseenter', () => {
                            const svg = document.getElementById('game-board');
                            const pts = _hexPoints(a.target_q, a.target_r);
                            if (svg && pts) _addHighlight(svg, pts,
                                'rgba(147,51,234,0.4)', '#9333ea', '★', () => {}, true);
                        });
                    }
                    panel.appendChild(btn);
                });
            }
        }

        document.body.appendChild(panel);
    }

    // ── Selection ─────────────────────────────────────────────────────────────
    function selectUnit(unitId) {
        clearHighlights();
        _selectedId = unitId;

        document.querySelectorAll('.unit').forEach(u => u.classList.remove('selected'));
        const el = document.querySelector(`.unit[data-unit-id="${unitId}"]`);
        if (el) el.classList.add('selected');

        document.querySelectorAll('.unit-item').forEach(item => {
            item.style.background = item.dataset.unitId === unitId
                ? 'rgba(255,255,255,0.2)' : '';
        });

        _showAll(unitId);
    }

    // ── Server actions ────────────────────────────────────────────────────────
    function _doAction(payload) {
        _showLoading(true);
        fetch('/action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        })
        .then(r => {
            if (!r.ok) return r.text().then(t => { throw new Error(t); });
            return r.json();
        })
        .then(data => {
            if (data.error) { _showLoading(false); alert('Error: ' + data.error); }
            else {
                // Show toast for combat results (attacks, defensive fire, bog checks)
                const msg = data.message || '';
                const hasCombat = msg.includes('MISS') || msg.includes('DISRUPTED')
                    || msg.includes('DAMAGED') || msg.includes('DESTROYED')
                    || msg.includes('defensive fire') || msg.includes('bogged');
                if (msg && (payload.type === 'attack' || hasCombat)) {
                    _showToast(msg);
                    setTimeout(() => location.reload(), 1500);
                } else {
                    location.reload();
                }
            }
        })
        .catch(err => { _showLoading(false); alert('Error: ' + err.message); });
    }

    function _showToast(msg) {
        _showLoading(false);
        let toast = document.getElementById('aa-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'aa-toast';
            toast.style.cssText = 'position:fixed;top:30%;left:50%;transform:translate(-50%,-50%);'
                + 'background:rgba(0,0,0,0.85);color:#fff;padding:18px 32px;border-radius:10px;'
                + 'font-size:18px;font-weight:bold;z-index:9999;text-align:center;'
                + 'border:2px solid #e94560;max-width:80%;';
            document.body.appendChild(toast);
        }
        // Color-code the result (handle newlines from defensive fire messages)
        let html = msg.replace(/\n/g, '<br>');
        html = html.replace(/MISS/g, '<span style="color:#888">MISS</span>');
        html = html.replace(/DISRUPTED/g, '<span style="color:#eab308">DISRUPTED</span>');
        html = html.replace(/DAMAGED/g, '<span style="color:#ef4444">DAMAGED</span>');
        html = html.replace(/DESTROYED/g, '<span style="color:#dc2626">DESTROYED</span>');
        html = html.replace(/bogged down/gi, '<span style="color:#f97316">BOGGED DOWN</span>');
        html = html.replace(/defensive fire/gi, '<span style="color:#ef4444">Defensive Fire</span>');
        toast.innerHTML = html;
        toast.style.display = 'block';
    }

    function _showLoading(show) {
        const el = document.getElementById('aa-loading');
        if (el) el.style.display = show ? 'flex' : 'none';
    }

    // ── Public API ────────────────────────────────────────────────────────────
    window.selectUnit  = selectUnit;
    window.deselectUnit = function() {
        _selectedId = null; clearHighlights();
        document.querySelectorAll('.unit').forEach(u => u.classList.remove('selected'));
        document.querySelectorAll('.unit-item').forEach(i => i.style.background = '');
    };
    window.endPhase = function() { _doAction({type:'pass'}); };
    window.undo = function() { _doAction({type:'undo'}); };
    window.redo = function() { _doAction({type:'redo'}); };
    window.newGame  = function() {
        _showLoading(true);
        fetch('/new_game', {method:'POST'}).then(() => location.reload());
    };

    // ── Pending hit indicators ───────────────────────────────────────────────
    function _renderPendingHits() {
        if (typeof UNIT_ACTIONS === 'undefined') return;
        const svg = document.getElementById('game-board');
        if (!svg) return;

        for (const [uid, info] of Object.entries(UNIT_ACTIONS)) {
            const count = info.pending_hits || 0;
            const disrupted = info.is_disrupted || false;
            const damaged = info.is_damaged || false;
            if (count === 0 && !disrupted && !damaged) continue;

            const unitEl = document.querySelector(`.unit[data-unit-id="${uid}"]`);
            if (!unitEl) continue;
            const q = parseInt(unitEl.dataset.q);
            const r = parseInt(unitEl.dataset.r);
            const hex = document.querySelector(`.hex[data-q="${q}"][data-r="${r}"]`);
            if (!hex) continue;
            const pts = hex.getAttribute('points').split(' ').map(p => p.split(',').map(Number));
            const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
            const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;

            // Pending hits = orange pulsing circle with count
            if (count > 0) {
                const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
                g.setAttribute('pointer-events', 'none');
                const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                c.setAttribute('cx', cx + 16); c.setAttribute('cy', cy - 16);
                c.setAttribute('r', '8');
                c.setAttribute('fill', '#f97316'); c.setAttribute('stroke', '#fff');
                c.setAttribute('stroke-width', '1.5');
                g.appendChild(c);
                const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                t.setAttribute('x', cx + 16); t.setAttribute('y', cy - 12);
                t.setAttribute('text-anchor', 'middle');
                t.setAttribute('font-size', '10'); t.setAttribute('font-weight', 'bold');
                t.setAttribute('fill', '#fff'); t.setAttribute('pointer-events', 'none');
                t.textContent = count;
                g.appendChild(t);
                svg.appendChild(g);
            }

            // Disrupted = yellow "!" badge
            if (disrupted) {
                const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                t.setAttribute('x', cx - 16); t.setAttribute('y', cy - 12);
                t.setAttribute('text-anchor', 'middle');
                t.setAttribute('font-size', '14'); t.setAttribute('font-weight', 'bold');
                t.setAttribute('fill', '#eab308'); t.setAttribute('stroke', '#000');
                t.setAttribute('stroke-width', '0.5');
                t.setAttribute('pointer-events', 'none');
                t.textContent = '!';
                svg.appendChild(t);
            }

            // Damaged = red "D" badge
            if (damaged) {
                const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                t.setAttribute('x', cx - 16); t.setAttribute('y', cy + 18);
                t.setAttribute('text-anchor', 'middle');
                t.setAttribute('font-size', '12'); t.setAttribute('font-weight', 'bold');
                t.setAttribute('fill', '#ef4444'); t.setAttribute('stroke', '#000');
                t.setAttribute('stroke-width', '0.5');
                t.setAttribute('pointer-events', 'none');
                t.textContent = 'D';
                svg.appendChild(t);
            }
        }
    }

    // ── Facing selection ────────────────────────────────────────────────────
    function _showFacingArrows(unitId) {
        const unitEl = document.querySelector(`.unit[data-unit-id="${unitId}"]`);
        const svg = document.getElementById('game-board');
        if (!unitEl || !svg) return;

        const q = parseInt(unitEl.dataset.q);
        const r = parseInt(unitEl.dataset.r);
        // Find hex center from the hex polygon
        const hex = document.querySelector(`.hex[data-q="${q}"][data-r="${r}"]`);
        if (!hex) return;
        const pts = hex.getAttribute('points').split(' ').map(p => p.split(',').map(Number));
        const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
        const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;

        // 6 directions: angles match flat-top hex edge centers
        // These are the angles toward each neighboring hex in the axial layout
        const dirs = [
            {f:0, label:'E',  angle:30},    // East: 30° below horizontal
            {f:1, label:'SE', angle:-30},   // Southeast: -30°
            {f:2, label:'SW', angle:-90},   // Southwest: straight up (SVG y-down)
            {f:3, label:'W',  angle:-150},  // West: -150°
            {f:4, label:'NW', angle:150},   // Northwest: 150°
            {f:5, label:'NE', angle:90}     // Northeast: 90° straight down
        ];
        const dist = 38;
        dirs.forEach(d => {
            const rad = d.angle * Math.PI / 180;
            const ax = cx + dist * Math.cos(rad);
            const ay = cy + dist * Math.sin(rad);

            const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            g.classList.add('dyn-hl');
            g.style.cursor = 'pointer';

            const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            c.setAttribute('cx', ax); c.setAttribute('cy', ay);
            c.setAttribute('r', '12');
            c.setAttribute('fill', 'rgba(234,179,8,0.8)');
            c.setAttribute('stroke', '#eab308');
            c.setAttribute('stroke-width', '2');
            g.appendChild(c);

            // Arrow character pointing in direction
            const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            t.setAttribute('x', ax); t.setAttribute('y', ay + 4);
            t.setAttribute('text-anchor', 'middle');
            t.setAttribute('font-size', '10'); t.setAttribute('font-weight', 'bold');
            t.setAttribute('fill', '#000'); t.setAttribute('pointer-events', 'none');
            t.textContent = d.label;
            g.appendChild(t);

            g.addEventListener('click', function(e) {
                e.stopPropagation();
                _doAction({type:'set_facing', unit_id: unitId, facing: d.f});
            });
            svg.appendChild(g);
        });
    }

    // ── Init ──────────────────────────────────────────────────────────────────
    function _init() {
        _setupZoom();
        _applyZoom();

        // Restore scroll position after reload
        const c = document.querySelector('.board-container');
        if (c) {
            const sx = parseFloat(sessionStorage.getItem('aa_scrollX')) || 0;
            const sy = parseFloat(sessionStorage.getItem('aa_scrollY')) || 0;
            c.scrollLeft = sx; c.scrollTop = sy;
            // Save scroll on any scroll event
            c.addEventListener('scroll', function() {
                sessionStorage.setItem('aa_scrollX', c.scrollLeft);
                sessionStorage.setItem('aa_scrollY', c.scrollTop);
            });
        }

        // Render status badges (pending hits, disrupted, damaged)
        _renderPendingHits();

        // If a vehicle needs facing selection, show arrows immediately
        if (typeof PENDING_FACING !== 'undefined' && PENDING_FACING) {
            _showFacingArrows(PENDING_FACING);
        }

        // SVG units: use event delegation on the SVG element itself
        // because SVG <g> elements don't natively capture clicks without a fill.
        const svg = document.getElementById('game-board');
        if (svg) {
            svg.addEventListener('click', function(e) {
                if (_isDragging) return;  // ignore drag-releases
                // Don't handle unit clicks while facing is pending
                if (typeof PENDING_FACING !== 'undefined' && PENDING_FACING) return;
                // Ignore clicks on highlight overlays (they have their own handlers)
                if (e.target.closest('.dyn-hl')) return;
                const unitG = e.target.closest('.unit');
                if (!unitG) return;
                e.stopPropagation();
                const id = unitG.dataset.unitId;
                if (!id) return;
                if (_selectedId === id) window.deselectUnit();
                else selectUnit(id);
            });
            // Make unit groups show pointer cursor
            document.querySelectorAll('.unit').forEach(u => {
                u.style.cursor = 'pointer';
            });
        }

        document.querySelectorAll('.unit-item').forEach(item => {
            item.addEventListener('click', () => {
                // Don't handle unit clicks while facing is pending
                if (typeof PENDING_FACING !== 'undefined' && PENDING_FACING) return;
                const id = item.dataset.unitId;
                if (id) selectUnit(id);
            });
        });

        // ── Stat card tooltip on hover ──────────────────────────────────────
        const statCard = document.createElement('div');
        statCard.id = 'aa-stat-card';
        statCard.style.cssText = 'display:none;position:fixed;z-index:5000;'
            + 'background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);'
            + 'border:2px solid #e94560;border-radius:8px;padding:12px 16px;'
            + 'color:#fff;font-size:12px;font-family:monospace;'
            + 'pointer-events:none;min-width:220px;max-width:300px;'
            + 'box-shadow:0 4px 20px rgba(0,0,0,0.6);';
        document.body.appendChild(statCard);

        document.querySelectorAll('.unit-item').forEach(item => {
            item.addEventListener('mouseenter', function(e) {
                const d = item.dataset;
                if (!d.unitName) return;
                const defStr = d.defFront === d.defRear
                    ? d.defFront : d.defFront + '/' + d.defRear;

                let html = '<div style="font-size:14px;font-weight:bold;color:#e94560;'
                    + 'margin-bottom:6px;border-bottom:1px solid #333;padding-bottom:4px;">'
                    + d.unitName + '</div>';
                html += '<div style="display:flex;gap:12px;margin-bottom:4px;">'
                    + '<span style="color:#888">' + d.type + '</span>'
                    + '<span style="color:#888">' + d.nation + '</span>'
                    + '<span style="color:#888">' + d.year + '</span>'
                    + '</div>';
                html += '<div style="display:flex;gap:16px;margin-bottom:6px;">'
                    + '<span><b style="color:#3b82f6">Cost:</b> ' + d.cost + '</span>'
                    + '<span><b style="color:#3b82f6">Speed:</b> ' + d.speed + '</span>'
                    + '<span><b style="color:#3b82f6">Def:</b> ' + defStr + '</span>'
                    + '</div>';

                // Attack table
                html += '<table style="width:100%;border-collapse:collapse;margin-bottom:6px;">'
                    + '<tr style="color:#888;font-size:10px;"><th></th>'
                    + '<th style="padding:0 6px">Short</th>'
                    + '<th style="padding:0 6px">Med</th>'
                    + '<th style="padding:0 6px">Long</th></tr>';
                const vsVeh = [d.vehS, d.vehM, d.vehL];
                const vsPer = [d.perS, d.perM, d.perL];
                function atkCell(v) {
                    const n = parseInt(v);
                    return n > 0
                        ? '<td style="text-align:center;color:#ef4444;padding:1px 6px;">' + v + '</td>'
                        : '<td style="text-align:center;color:#444;padding:1px 6px;">-</td>';
                }
                html += '<tr><td style="color:#f97316;font-size:11px;">vs Veh</td>'
                    + vsVeh.map(atkCell).join('') + '</tr>';
                html += '<tr><td style="color:#22c55e;font-size:11px;">vs Per</td>'
                    + vsPer.map(atkCell).join('') + '</tr>';
                html += '</table>';

                // Abilities with descriptions (clickable)
                const abilitiesRaw = d.abilities || 'None';
                const abilityList = abilitiesRaw.split(', ').filter(a => a && a !== 'None');
                html += '<div style="border-top:1px solid #333;padding-top:4px;'
                    + 'font-size:11px;color:#c084fc;line-height:1.4;">'
                    + '<b>Abilities:</b></div>';
                if (abilityList.length > 0 && typeof ABILITY_DESCS !== 'undefined') {
                    abilityList.forEach(function(ab) {
                        const key = ab.trim().toLowerCase();
                        // Try exact match, then prefix match for abilities like "Enhanced Range 16"
                        let desc = ABILITY_DESCS[key];
                        if (!desc) {
                            const base = key.replace(/\s*\d+$/, '');
                            desc = ABILITY_DESCS[base];
                        }
                        // Also try matching "COMMANDER ABILITIES:2" → "commander abilities"
                        if (!desc) {
                            const cleaned = key.replace(/:\d+$/, '').replace(/_/g, ' ');
                            desc = ABILITY_DESCS[cleaned];
                        }
                        html += '<div style="margin:2px 0;padding:2px 4px;'
                            + 'background:rgba(147,51,234,0.15);border-radius:3px;cursor:help;"'
                            + ' title="' + (desc || 'No description available').replace(/"/g, '&quot;') + '">'
                            + '<span style="color:#c084fc;font-weight:bold;">' + ab.trim() + '</span>';
                        if (desc) {
                            html += '<div style="color:#999;font-size:10px;margin-top:1px;">'
                                + desc.substring(0, 120) + (desc.length > 120 ? '…' : '') + '</div>';
                        }
                        html += '</div>';
                    });
                } else {
                    html += '<div style="color:#666;font-size:11px;">None</div>';
                }

                statCard.innerHTML = html;
                statCard.style.display = 'block';
                // Position near the item
                const rect = item.getBoundingClientRect();
                statCard.style.left = (rect.right + 8) + 'px';
                statCard.style.top = Math.max(10, rect.top - 40) + 'px';
                // If off-screen right, move to left side
                const cardRect = statCard.getBoundingClientRect();
                if (cardRect.right > window.innerWidth - 10) {
                    statCard.style.left = (rect.left - cardRect.width - 8) + 'px';
                }
                // If off-screen bottom, adjust
                if (cardRect.bottom > window.innerHeight - 10) {
                    statCard.style.top = (window.innerHeight - cardRect.height - 10) + 'px';
                }
            });
            item.addEventListener('mouseleave', function() {
                statCard.style.display = 'none';
            });
        });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _init);
    else _init();
})();
</script>
"""


def _inject_server_js(html: str) -> str:
    return html.replace('</body>', _SERVER_JS + '\n</body>')


_PHASE_LABELS = {
    'movement': 'Movement Phase',
    'assault':  'Assault Phase',
    'flight':   'Flight Phase',
    'airstrike': 'Airstrike Phase',
    'deployment': 'Deployment Phase',
    'consolidation': 'Consolidation Phase',
    'end': 'End of Turn',
}

_PHASE_HINTS = {
    'movement': 'Green = move · Yellow = board transport · Orange = dismount · Purple = ability',
    'assault':  'Red = attack target · Purple = ability · Click End Phase if no targets in range yet',
}


def _inject_overlay(html: str, session: 'GameSession') -> str:
    gs = session.game_state
    won = gs.is_game_over()

    if won:
        w = gs.check_victory_conditions() or "unknown"
        phase_label = f"Game Over — {w} wins!"
        is_human = False
    else:
        raw_phase = gs.current_phase
        phase_label = _PHASE_LABELS.get(raw_phase, raw_phase.upper())
        is_human = session._is_human_turn()

    if session.pending_facing:
        us = gs.get_unit_state(session.pending_facing)
        facing_name = us.unit.name if us else 'vehicle'
        hint = f'⟳ Choose facing direction for {facing_name} (click a yellow circle)'
    else:
        hint = _PHASE_HINTS.get(gs.current_phase, '') if not won else ''

    has_undo = len(session._undo_stack) > 0
    has_redo = len(session._redo_stack) > 0

    undo_btn = (
        '<button onclick="undo()" '
        'style="background:#0f3460;color:white;border:1px solid #1a6eb5;'
        'padding:6px 14px;border-radius:4px;cursor:pointer;font-size:13px;">'
        '↩ Undo</button>'
        if has_undo and (is_human or session.pending_facing) else ''
    )

    redo_btn = (
        '<button onclick="redo()" '
        'style="background:#0f3460;color:white;border:1px solid #1a6eb5;'
        'padding:6px 14px;border-radius:4px;cursor:pointer;font-size:13px;">'
        '↪ Redo</button>'
        if has_redo and (is_human or session.pending_facing) else ''
    )

    end_btn = (
        '<button onclick="endPhase()" '
        'style="background:#0f3460;color:white;border:1px solid #1a6eb5;'
        'padding:6px 14px;border-radius:4px;cursor:pointer;font-size:13px;">'
        'End Phase</button>'
        if is_human and not session.pending_facing else
        ('<span style="color:#f97316;font-weight:bold;">AI is playing…</span>'
         if not won else '')
    )

    log_lines = session.events[-40:]
    def _log_color(e):
        if "You:" in e: return "#22c55e"
        if "AI " in e or "AI:" in e: return "#ef4444"
        if "DISRUPTED" in e: return "#eab308"
        if "DAMAGED" in e or "DESTROYED" in e or "💥" in e: return "#dc2626"
        if "bogged" in e or "failed" in e: return "#f97316"
        if "Turn" in e or "▶" in e: return "#3b82f6"
        return "#aaa"
    log_html = ''.join(
        f'<div style="padding:1px 0;white-space:pre;color:{_log_color(e)}">'
        f'{e}</div>'
        for e in log_lines
    )

    overlay = f"""
<!-- ── SERVER OVERLAY ───────────────────────────────────────── -->
<style>
  /* Give sidebar room to scroll below the fixed overlay */
  .sidebar {{ padding-bottom: 130px !important; }}
  .board-container {{ padding-bottom: 130px !important; }}
</style>

<div id="aa-loading" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;
     background:rgba(0,0,0,0.65);z-index:9000;align-items:center;justify-content:center;">
  <div style="background:#16213e;padding:24px 48px;border-radius:12px;border:2px solid #0f3460;
              font-size:20px;color:#fff;">
    Processing…
  </div>
</div>

<div id="aa-overlay" style="position:fixed;bottom:0;left:0;right:0;z-index:2000;
     background:#16213e;border-top:2px solid #0f3460;">

  <!-- Control bar -->
  <div style="display:flex;align-items:center;gap:12px;padding:5px 12px;
              border-bottom:1px solid #0f3460;flex-wrap:wrap;">

    <!-- Turn / phase info -->
    <div>
      <span style="color:#e94560;font-weight:bold;font-size:13px;">
        Turn {gs.turn_number}
      </span>
      <span style="color:#aaa;font-size:13px;margin-left:6px;">{phase_label}</span>
      {'<span style="color:#3b82f6;font-weight:bold;font-size:13px;margin-left:8px;">▶ YOUR TURN</span>' if is_human else ''}
    </div>

    <!-- Hint -->
    {'<span style="color:#666;font-size:11px;flex:1;">' + hint + '</span>' if hint else '<span style="flex:1"></span>'}

    <!-- Zoom buttons -->
    <div style="display:flex;gap:4px;">
      <button onclick="zoomOut()"
        style="background:#0f3460;color:white;border:none;padding:4px 8px;
               border-radius:3px;cursor:pointer;font-size:12px;">−</button>
      <button onclick="zoomReset()"
        style="background:#0f3460;color:white;border:none;padding:4px 8px;
               border-radius:3px;cursor:pointer;font-size:11px;">1:1</button>
      <button onclick="zoomIn()"
        style="background:#0f3460;color:white;border:none;padding:4px 8px;
               border-radius:3px;cursor:pointer;font-size:12px;">+</button>
    </div>

    {undo_btn}
    {redo_btn}
    {end_btn}

    <button onclick="newGame()"
      style="background:#1a1a2e;color:#888;border:1px solid #333;
             padding:5px 12px;border-radius:4px;cursor:pointer;font-size:12px;">
      New Game
    </button>
  </div>

  <!-- Log -->
  <div id="aa-log"
    style="height:90px;overflow-y:auto;padding:3px 12px;font-size:12px;font-family:monospace;">
    {log_html}
    <div id="aa-log-end"></div>
  </div>
</div>

<script>
  (function() {{
    var end = document.getElementById('aa-log-end');
    if (end) end.scrollIntoView();
  }})();
</script>
<!-- ── END SERVER OVERLAY ────────────────────────────────────── -->
"""
    return html.replace('</body>', overlay + '\n</body>')


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    global _session
    with _session_lock:
        if _session is None:
            _session = GameSession()
        try:
            html = _session.render_html()
        except Exception as e:
            import traceback
            traceback.print_exc()
            return (f'<html><body style="background:#1a1a2e;color:#fff;padding:40px;">'
                    f'<h2>Render error</h2><pre>{e}</pre>'
                    f'<button onclick="fetch(\'/new_game\',{{method:\'POST\'}}).then(()=>location.reload())"'
                    f' style="padding:10px 20px;font-size:16px;">New Game</button>'
                    f'</body></html>'), 500
    return html


@app.route('/action', methods=['POST'])
def action():
    global _session
    with _session_lock:
        if _session is None:
            return jsonify({"error": "No session"}), 400
        data = request.get_json(force=True)
        try:
            result = _session.execute_human_action(data)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"error": f"Internal error: {str(e)}"}), 500
    return jsonify(result)


@app.route('/new_game', methods=['POST'])
def new_game():
    global _session
    with _session_lock:
        _session = GameSession()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print("=" * 60)
    print("  Axis & Allies Miniatures — Human vs AI")
    print("  http://localhost:8080")
    print("=" * 60)
    print("  Player 1 (blue) = YOU")
    print("  Player 2 (red)  = AI (Aggressive)")
    print()
    print("  How to play:")
    print("  • Click a unit to select it")
    print("  • Green hexes = valid moves (click to move)")
    print("  • Red hexes   = valid attacks (click to attack)")
    print("  • 'End Phase' to skip remaining actions")
    print("  • 'New Game' to restart")
    print("=" * 60)
    app.run(debug=False, port=8080)
