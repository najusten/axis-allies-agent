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
from flask import Flask, jsonify, request

from game_state import GameState, GamePhase
from game_setup import quick_setup_broad
from action_generator import ActionGenerator
from action_executor import ActionExecutor
from abilities import AbilitySystem
from evaluation import GameStateEvaluator
from initiative import InitiativeSystem
from visualization import HTMLGenerator
from action import (MoveAction, AttackAction, PassAction,
                    BoardTransportAction, DismountTransportAction)
from movement import MovementSystem
from game_runner import AggressiveRandomAgent, GreedyAgent, RandomAgent

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
        self.initiative_system = InitiativeSystem(self.ability_system, self.movement_system)

        self.human_player = 'player1'
        self.ai_player = 'player2'
        self.events: list[str] = []  # game log (most recent last)

        # AI agent
        if ai_type == 'random':
            self.ai_agent = RandomAgent("AI (Random)")
        elif ai_type == 'greedy':
            evaluator = GameStateEvaluator()
            self.ai_agent = GreedyAgent("AI (Greedy)", self.action_executor, evaluator)
        else:
            self.ai_agent = AggressiveRandomAgent("AI (Aggressive)")

        # Create game state
        setup = quick_setup_broad(points=points)
        self.game_state: GameState = setup.create_game()

        # Phase sequencing
        self.turn_order: list[str] = [self.human_player, self.ai_player]
        self.phase_sequence: list[tuple[str, str]] = []  # (phase, player)
        self.phase_idx: int = 0

        # Start first turn
        self._start_turn()

    # ------------------------------------------------------------------
    # Turn / Phase management
    # ------------------------------------------------------------------

    def _start_turn(self):
        first, p1_msg, p2_msg = self.initiative_system.determine_first_player(self.game_state)
        second = self.ai_player if first == self.human_player else self.human_player
        self.turn_order = [first, second]

        self.events.append(
            f"▶ Turn {self.game_state.turn_number} — initiative: {first} goes first"
        )

        fp, sp = first, second
        self.phase_sequence = [
            (GamePhase.MOVEMENT, fp),
            (GamePhase.MOVEMENT, sp),
            (GamePhase.ASSAULT, fp),
            (GamePhase.ASSAULT, sp),
        ]
        self.phase_idx = 0
        self._apply_phase()

        # Reset action executor phase tracking
        self.action_executor.reset_defensive_fire_phase()

        # Advance past any AI / empty phases until human has actions
        self._run_until_human_turn()

    def _apply_phase(self):
        """Set game_state fields to match current phase_idx."""
        if self.phase_idx < len(self.phase_sequence):
            phase, player = self.phase_sequence[self.phase_idx]
            self.game_state.current_phase = phase
            self.game_state.active_player = player

    def _advance_phase(self):
        self.phase_idx += 1
        if self.phase_idx >= len(self.phase_sequence):
            self._end_turn()
        else:
            self._apply_phase()

    def _end_turn(self):
        # Casualty phase
        try:
            results = self.action_executor.resolve_casualty_phase(self.game_state)
            destroyed = results.get('units_destroyed', [])
            if destroyed:
                for uid in destroyed:
                    unit_state = self.game_state.units.get(uid)
                    name = unit_state.unit.name if unit_state else uid
                    self.events.append(f"  💥 {name} destroyed")
        except Exception:
            pass

        if self.game_state.is_game_over():
            return

        # Advance turn counter
        self.game_state.turn_number += 1
        self._start_turn()

    # ------------------------------------------------------------------
    # Legal action helpers
    # ------------------------------------------------------------------

    def _get_phase_actions(self, player: str) -> list:
        """Get legal actions for player in the current phase."""
        phase = self.game_state.current_phase
        try:
            all_actions = self.action_generator.get_all_legal_actions(self.game_state, player)
        except Exception:
            return []

        if phase == GamePhase.MOVEMENT:
            return [a for a in all_actions if isinstance(a, MoveAction)]
        elif phase == GamePhase.ASSAULT:
            return [a for a in all_actions if isinstance(a, AttackAction)]
        return all_actions

    # ------------------------------------------------------------------
    # AI auto-play
    # ------------------------------------------------------------------

    def _run_until_human_turn(self):
        """Play AI turns / skip empty phases until human has actions."""
        MAX = 300
        for _ in range(MAX):
            if self.game_state.is_game_over():
                return
            if self.phase_idx >= len(self.phase_sequence):
                return

            phase, player = self.phase_sequence[self.phase_idx]

            if player == self.human_player:
                # Always show assault phase to human so they see when in range
                if phase == GamePhase.ASSAULT:
                    return
                # Movement: skip only if truly no moves
                legal = self._get_phase_actions(self.human_player)
                if legal:
                    return  # human has moves
                self.events.append(
                    f"  {self.human_player} has no {phase} actions — skipping"
                )
                self._advance_phase()
            else:
                # Run full AI phase
                self._run_ai_phase(player, phase)
                self._advance_phase()

    def _run_ai_phase(self, player: str, phase: str):
        self.events.append(f"  AI {phase.lower()} phase:")
        if phase == GamePhase.MOVEMENT:
            self.action_executor.reset_defensive_fire_phase()

        for _ in range(30):  # cap iterations per phase
            legal = self._get_phase_actions(player)
            if not legal:
                break
            action = self.ai_agent.choose_action(self.game_state, legal, player)
            result = self.action_executor.execute_action(self.game_state, action)
            if result.success:
                self.events.append(f"    {result.message}")
            else:
                break

    # ------------------------------------------------------------------
    # Human action entry point
    # ------------------------------------------------------------------

    def execute_human_action(self, data: dict) -> dict:
        """Execute one human action then advance game."""
        if self.game_state.is_game_over():
            return {"error": "Game is already over"}

        if self.phase_idx >= len(self.phase_sequence):
            return {"error": "No active phase"}

        _, current_player = self.phase_sequence[self.phase_idx]
        if current_player != self.human_player:
            return {"error": "Not your turn"}

        action_type = data.get('type')

        if action_type == 'pass':
            phase = self.game_state.current_phase
            self.events.append(f"  {self.human_player} ends {phase}")
            self._advance_phase()
            self._run_until_human_turn()
            return {"success": True}

        action = self._build_action(data)
        if action is None:
            return {"error": "Could not build action from data"}

        result = self.action_executor.execute_action(self.game_state, action)
        if not result.success:
            return {"error": result.message}

        self.events.append(f"  You: {result.message}")

        # If the unit destroyed something, note it
        if getattr(result, 'unit_destroyed', None):
            self.events.append(f"    💥 {result.unit_destroyed} destroyed")

        # Check if human still has actions; if not, advance
        remaining = self._get_phase_actions(self.human_player)
        if not remaining:
            self._advance_phase()
            self._run_until_human_turn()

        return {"success": True}

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
            return MoveAction(unit_id, from_q, from_r, to_q, to_r)

        elif data['type'] == 'attack':
            tq, tr = int(data['target_q']), int(data['target_r'])
            defending_id = self._unit_at(tq, tr)
            if not defending_id:
                return None
            return AttackAction(unit_id, defending_id, from_q, from_r, tq, tr)

        elif data['type'] == 'board_transport':
            tid = data.get('transport_id')
            pq, pr = int(data['pos_q']), int(data['pos_r'])
            return BoardTransportAction(unit_id, tid, pq, pr)

        elif data['type'] == 'dismount':
            tid = data.get('transport_id')
            to_q, to_r = int(data['to_q']), int(data['to_r'])
            return DismountTransportAction(unit_id, tid, to_q, to_r)

        return None

    def _unit_at(self, q: int, r: int):
        for uid, us in self.game_state.units.items():
            if us.is_alive and us.position == (q, r):
                return uid
        return None

    # ------------------------------------------------------------------
    # HTML generation
    # ------------------------------------------------------------------

    def render_html(self) -> str:
        html = HTMLGenerator().generate_html(self.game_state)
        unit_actions = self._compute_unit_actions()
        html = _inject_unit_actions(html, unit_actions)
        html = _inject_server_js(html)
        html = _inject_overlay(html, self)
        return html

    def _compute_unit_actions(self) -> dict:
        """Valid moves/attacks/transport actions for human player, filtered by phase."""
        gs = self.game_state

        # Seed empty entries for every alive unit (needed for JS to find all units)
        unit_actions: dict = {
            uid: {'valid_moves': [], 'valid_attacks': [],
                  'board_actions': [], 'dismount_actions': [],
                  'can_move': False, 'can_attack': False}
            for uid, us in gs.units.items() if us.is_alive
        }

        if self.phase_idx >= len(self.phase_sequence):
            return unit_actions

        phase, active_player = self.phase_sequence[self.phase_idx]
        if active_player != self.human_player:
            return unit_actions

        try:
            all_actions = self.action_generator.get_all_legal_actions(gs, self.human_player)
        except Exception:
            return unit_actions

        for action in all_actions:
            uid = action.unit_id
            if uid not in unit_actions:
                continue
            entry = unit_actions[uid]

            if phase == GamePhase.MOVEMENT:
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
                    entry['valid_attacks'].append([action.to_q, action.to_r])
                    entry['can_attack'] = True

        return unit_actions


# ---------------------------------------------------------------------------
# HTML injection helpers
# ---------------------------------------------------------------------------

def _inject_unit_actions(html: str, unit_actions: dict) -> str:
    script = (
        '<script>\n'
        f'const UNIT_ACTIONS = {json.dumps(unit_actions)};\n'
        '</script>'
    )
    return html.replace('</head>', f'{script}\n</head>')


_SERVER_JS = r"""
<script>
// ─── SERVER-AWARE INTERACTION ───────────────────────────────────────────────
(function() {
    let _selectedId = null;

    // ── Zoom ─────────────────────────────────────────────────────────────────
    let _zoom = 1.0;
    const _ZOOM_MIN = 0.3, _ZOOM_MAX = 3.0, _ZOOM_STEP = 0.15;

    function _applyZoom() {
        const c = document.querySelector('.board-container');
        const b = document.getElementById('game-board');
        if (b) b.style.transform = `scale(${_zoom})`;
        if (b) b.style.transformOrigin = 'top left';
    }

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
    }

    window.zoomIn  = function() { _zoom = Math.min(_ZOOM_MAX, _zoom + _ZOOM_STEP); _applyZoom(); };
    window.zoomOut = function() { _zoom = Math.max(_ZOOM_MIN, _zoom - _ZOOM_STEP); _applyZoom(); };
    window.zoomReset = function() { _zoom = 1.0; _applyZoom(); };

    // ── Highlights ────────────────────────────────────────────────────────────
    function clearHighlights() {
        document.querySelectorAll('.dyn-hl').forEach(el => el.remove());
    }

    function _addHighlight(svg, points, fill, stroke, label, onClick) {
        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.classList.add('dyn-hl');
        g.style.cursor = 'pointer';

        const hl = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
        hl.setAttribute('points', points);
        hl.setAttribute('fill', fill);
        hl.setAttribute('stroke', stroke);
        hl.setAttribute('stroke-width', '2.5');
        g.appendChild(hl);

        if (label) {
            // Compute centroid from points string
            const pts = points.split(' ').map(p => p.split(',').map(Number));
            const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
            const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
            const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            t.setAttribute('x', cx); t.setAttribute('y', cy + 4);
            t.setAttribute('text-anchor', 'middle');
            t.setAttribute('font-size', '9'); t.setAttribute('font-weight', 'bold');
            t.setAttribute('fill', stroke); t.setAttribute('pointer-events', 'none');
            t.textContent = label;
            g.appendChild(t);
        }

        g.addEventListener('click', function(e) { e.stopPropagation(); onClick(); });
        const layer = svg.querySelector('.unit-layer');
        if (layer) svg.insertBefore(g, layer); else svg.appendChild(g);
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
            if (pts) _addHighlight(svg, pts, 'rgba(239,68,68,0.4)', '#ef4444', null,
                () => _doAction({type:'attack', unit_id: unitId, target_q: q, target_r: r}));
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
        .then(r => r.json())
        .then(data => {
            if (data.error) { _showLoading(false); alert('Error: ' + data.error); }
            else             { location.reload(); }
        })
        .catch(err => { _showLoading(false); alert('Network error: ' + err); });
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
    window.newGame  = function() {
        _showLoading(true);
        fetch('/new_game', {method:'POST'}).then(() => location.reload());
    };

    // ── Init ──────────────────────────────────────────────────────────────────
    function _init() {
        _setupZoom();
        document.querySelectorAll('.unit').forEach(u => {
            u.addEventListener('click', function(e) {
                e.stopPropagation();
                const id = this.closest('.unit').dataset.unitId;
                if (!id) return;
                if (_selectedId === id) window.deselectUnit();
                else selectUnit(id);
            });
        });
        document.querySelectorAll('.unit-item').forEach(item => {
            item.addEventListener('click', () => {
                const id = item.dataset.unitId;
                if (id) selectUnit(id);
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
    'movement': 'Green = move · Yellow = board transport · Orange = dismount',
    'assault':  'Red = attack target · Click End Phase if no targets in range yet',
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
        is_human = (
            session.phase_idx < len(session.phase_sequence) and
            session.phase_sequence[session.phase_idx][1] == session.human_player
        )

    hint = _PHASE_HINTS.get(gs.current_phase, '') if not won else ''

    end_btn = (
        '<button onclick="endPhase()" '
        'style="background:#0f3460;color:white;border:1px solid #1a6eb5;'
        'padding:6px 14px;border-radius:4px;cursor:pointer;font-size:13px;">'
        'End Phase</button>'
        if is_human else
        ('<span style="color:#f97316;font-weight:bold;">AI is playing…</span>'
         if not won else '')
    )

    log_lines = session.events[-30:]
    log_html = ''.join(
        f'<div style="padding:1px 0;white-space:pre;color:'
        f'{"#22c55e" if "You:" in e else "#ef4444" if "AI:" in e else "#888"}">'
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

    {end_btn}

    <button onclick="newGame()"
      style="background:#1a1a2e;color:#888;border:1px solid #333;
             padding:5px 12px;border-radius:4px;cursor:pointer;font-size:12px;">
      New Game
    </button>
  </div>

  <!-- Log -->
  <div id="aa-log"
    style="height:60px;overflow-y:auto;padding:3px 12px;font-size:11px;font-family:monospace;">
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
        html = _session.render_html()
    return html


@app.route('/action', methods=['POST'])
def action():
    global _session
    with _session_lock:
        if _session is None:
            return jsonify({"error": "No session"}), 400
        data = request.get_json(force=True)
        result = _session.execute_human_action(data)
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
