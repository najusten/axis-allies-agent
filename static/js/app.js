// Controller: holds the current state payload, wires the renderer and UI,
// turns clicks into /api/action calls, and animates the returned events.
import { api } from './api.js';
import { BoardRenderer } from './renderer.js';
import { UI } from './ui.js';

const $ = (id) => document.getElementById(id);

class App {
  constructor() {
    this.state = null;
    this.selected = null;
    this.busy = false;
    this.lastHumanPlayer = null;

    this.renderer = new BoardRenderer($('board'), {
      onHexClick: (q, r) => this.onHexClick(q, r),
      onUnitClick: (id) => this.onUnitClick(id),
      onHighlightClick: (hl) => this.onHighlightClick(hl),
      onHover: (unitId, hl) => this.onBoardHover(unitId, hl),
    });
    this.ui = new UI({
      onSelectUnit: (id) => this.select(id === this.selected ? null : id),
      onAbility: (a) => this.useAbility(a),
      onHoverAbility: (a) => this.hoverAbility(a),
      onEndPhase: () => this.endPhase(),
      onUndo: () => this.send({ type: 'undo' }),
      onRedo: () => this.send({ type: 'redo' }),
      onNewGame: (opts) => this.newGame(opts),
      onHoverUnit: (id) => this.hoverUnit(id),
      onHoldFire: (id, hold) => this.send({ type: 'hold_fire', unit_id: id, hold }),
      listScenarios: () => api.scenarios(),
      listUnits: () => api.units(),
    });
    this._setupZoomPan();
    $('chk-coords').onchange = (e) => this.renderer.setCoords(e.target.checked);
    $('chk-los').onchange = () => this.updateLos();
    $('chk-fast').onchange = (e) => { this.renderer.setFast(e.target.checked); localStorage.setItem('aa_fast', e.target.checked ? '1' : ''); };
    if (localStorage.getItem('aa_fast')) { $('chk-fast').checked = true; this.renderer.setFast(true); }
    document.addEventListener('click', () => this.renderer.skipAnimation(), true);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.select(null);
      if (e.key === 'e' && !e.metaKey && !e.ctrlKey && document.activeElement.tagName !== 'INPUT') this.endPhase();
    });
  }

  async start() {
    try { this.ui.setAbilityDescriptions(await api.abilities()); } catch (e) { /* optional */ }
    const state = await api.state();
    await this.applyState(state, []);
    if (!this.userZoomed) this.fitBoard();
    this._afterRequest();
  }

  _afterRequest() {
    const f = this._followUp;
    this._followUp = null;
    if (f) this.send(f);
  }

  // ------------------------------------------------------------ state
  async applyState(state, events) {
    const prev = this.state;
    this.state = state;
    // Animate what happened before showing the final state.
    if (events && events.length) {
      this.renderer.setHighlights([]);
      $('ability-panel').hidden = true;
      await this.renderer.playEvents(events);
    }
    // Hot-seat: hide the board between two different humans.
    const s = state.session;
    if (s.mode === 'hotseat' && s.is_human_turn && this.lastHumanPlayer && this.lastHumanPlayer !== s.current_player && !s.game_over) {
      await this.ui.showHandoff(s.current_player);
    }
    if (s.is_human_turn) this.lastHumanPlayer = s.current_player;

    if (this.selected && !state.game.units.some(u => u.id === this.selected && u.is_alive)) this.selected = null;
    this.render();
    if (s.pending_facing) {
      this.renderer.showFacingPicker(s.pending_facing, (f) => this.send({ type: 'set_facing', unit_id: s.pending_facing, facing: f }));
    } else {
      this.renderer.hideFacingPicker();
    }
    // Decisions the rules ask of the player between phases. The answer is
    // sent after the current request has fully finished (see _afterRequest).
    if (s.pending_initiative && !s.game_over) {
      const first = await this.ui.showInitiativeChoice(s.pending_initiative, state);
      this._followUp = { type: 'choose_order', first };
      return;
    }
    if (s.pending_deploy_order && !s.game_over) {
      const first = await this.ui.showDeployOrderChoice(s.pending_deploy_order);
      this._followUp = { type: 'choose_deploy_order', first };
      return;
    }
    if (s.pending_defensive_fire && !s.game_over) {
      const pend = s.pending_defensive_fire;
      this.render();
      const placed = pend.kind === 'aircraft_placed';
      this.renderer.setHighlights([
        ...(placed ? [{ q: pend.step_to[0], r: pend.step_to[1], kind: 'target', label: '✈' }]
                   : [{ q: pend.step_from[0], r: pend.step_from[1], kind: 'target', label: 'from' },
                      { q: pend.step_to[0], r: pend.step_to[1], kind: 'target', label: 'to' }]),
        ...pend.options.map(o => ({ q: o.defender_pos[0], r: o.defender_pos[1], kind: 'ability', label: '★' })),
      ]);
      if (s.mode === 'hotseat' && this.lastHumanPlayer && this.lastHumanPlayer !== pend.player) await this.ui.showHandoff(pend.player);
      const decisions = await this.ui.showDefensiveFireChoice(pend, state);
      this._followUp = { type: 'defensive_fire_decision', decisions };
      return;
    }
    // Deployment: auto-select the next unit to place so clicks go straight to the map
    if (s.current_phase === 'deployment' && s.is_human_turn) {
      const sel = this.selected && state.game.units.find(u => u.id === this.selected);
      if (!sel || sel.is_deployed || sel.owner !== s.current_player) {
        const next = state.game.units.find(u => u.owner === s.current_player && !u.is_deployed && (state.unit_actions[u.id] || {}).deploy?.length);
        if (next) this.select(next.id);
      }
    }
    if (s.game_over && (!prev || !prev.session.game_over)) this.ui.showGameOver(s.result);
  }

  render() {
    this.renderer.render(this.state);
    this.ui.renderTop(this.state, this.selected);
    this.ui.renderSidebar(this.state, this.selected);
    this.ui.renderCard(this.state, this.selected);
    this.ui.renderAbilityPanel(this.state, this.selected);
    this.ui.renderLog(this.state.log);
    this.renderer.setSelection(this.selected);
    this.renderer.setHighlights(this.highlightsFor(this.selected));
    this.renderer.setLosShade([]);
  }

  highlightsFor(id) {
    if (!id || !this.state.session.is_human_turn) return [];
    const ua = this.state.unit_actions[id];
    if (!ua) return [];
    const out = [];
    const u = this.state.game.units.find(x => x.id === id);
    for (const [q, r] of ua.moves) {
      const turnInPlace = u && u.position[0] === q && u.position[1] === r;
      out.push({ q, r, kind: turnInPlace ? 'turn' : 'move', label: turnInPlace ? '↻ turn' : undefined,
                 data: { type: 'move', unit_id: id, to_q: q, to_r: r } });
    }
    for (const a of ua.attacks) out.push({ q: a.q, r: a.r, kind: a.indirect ? 'indirect' : 'attack', label: a.indirect ? 'IDF' : '⚔', data: { type: 'attack', unit_id: id, target_id: a.target_id, target_q: a.q, target_r: a.r } });
    for (const b of ua.board) out.push({ q: b.q, r: b.r, kind: 'board', label: 'BOARD', data: { type: 'board_transport', unit_id: id, transport_id: b.transport_id, pos_q: b.q, pos_r: b.r } });
    for (const d of ua.dismount) out.push({ q: d.q, r: d.r, kind: 'dismount', label: 'OUT', data: { type: 'dismount', unit_id: id, transport_id: d.transport_id, to_q: d.q, to_r: d.r } });
    for (const [q, r] of (ua.place || [])) out.push({ q, r, kind: 'place', data: { type: 'place', unit_id: id, to_q: q, to_r: r } });
    for (const [q, r] of (ua.deploy || [])) out.push({ q, r, kind: 'place', data: { type: 'deploy', unit_id: id, to_q: q, to_r: r } });
    return out;
  }

  select(id) {
    this.selected = id;
    this.render();
    this.updateLos();
  }

  async updateLos() {
    if (!$('chk-los').checked || !this.selected) { this.renderer.setLosShade([]); return; }
    try {
      const res = await api.los(this.selected);
      if (res.unit === this.selected) this.renderer.setLosShade(res.blocked);
    } catch (e) { this.renderer.setLosShade([]); }
  }

  // ------------------------------------------------------------ input
  onUnitClick(id) {
    if (this.busy) return;
    const u = this.state.game.units.find(x => x.id === id);
    if (!u) return;
    // Clicking a unit standing on one of the selected unit's highlights = that action
    // (attack an enemy there, or "turn in place" on the selected vehicle itself)
    if (this.selected) {
      const hl = this.highlightsFor(this.selected).find(h => h.q === u.position[0] && h.r === u.position[1]);
      if (hl && (this.selected !== id || hl.kind === 'turn')) { this.onHighlightClick(hl); return; }
    }
    this.select(this.selected === id ? null : id);
  }

  onHexClick(q, r) {
    if (this.busy) return;
    if (q === null) { this.select(null); return; }
    const hl = this.highlightsFor(this.selected).find(h => h.q === q && h.r === r);
    if (hl) this.onHighlightClick(hl);
    else {
      // select a friendly unit standing there, if any
      const s = this.state.session;
      const u = this.state.game.units.find(x => x.is_alive && !x.carried_by_id && x.position[0] === q && x.position[1] === r && x.owner === s.current_player);
      this.select(u ? u.id : null);
    }
  }

  onHighlightClick(hl) {
    if (this.busy || !hl.data) return;
    this.send(hl.data);
  }

  onBoardHover(unitId, hl) {
    if (hl && hl.kind === 'attack' && this.selected) {
      const from = this.state.game.units.find(u => u.id === this.selected);
      if (from) this.renderer.showLos(from.position, [hl.q, hl.r]);
    } else {
      this.renderer.hideLos();
    }
    // route preview for moves (debounced; shows where terrain rolls will happen)
    const key = hl && hl.kind === 'move' && this.selected ? `${this.selected}:${hl.q},${hl.r}` : null;
    if (key !== this._hoverKey) {
      this._hoverKey = key;
      clearTimeout(this._hoverTimer);
      this.renderer.hidePath();
      if (key) {
        const [uid, q, r] = [this.selected, hl.q, hl.r];
        this._hoverTimer = setTimeout(async () => {
          try {
            const res = await api.path(uid, q, r);
            if (this._hoverKey === key) this.renderer.showPath(res.path, res.rolls);
          } catch (e) { /* ignore */ }
        }, 120);
      }
    }
  }

  hoverUnit(id) { /* sidebar hover: could highlight on board; keep minimal */ }

  hoverAbility(a) {
    if (a && a.target) this.renderer.setHighlights([...this.highlightsFor(this.selected), { q: a.target[0], r: a.target[1], kind: 'ability', label: '★' }]);
    else if (a && a.target_id) {
      const t = this.state.game.units.find(u => u.id === a.target_id);
      if (t) this.renderer.setHighlights([...this.highlightsFor(this.selected), { q: t.position[0], r: t.position[1], kind: 'ability', label: '★' }]);
    } else this.renderer.setHighlights(this.highlightsFor(this.selected));
  }

  useAbility(a) {
    const data = { type: 'use_ability', unit_id: this.selected, ability_name: a.ability, target_id: a.target_id };
    if (a.target) { data.target_q = a.target[0]; data.target_r = a.target[1]; }
    if (a.parameters) data.parameters = a.parameters;
    this.send(data);
  }

  endPhase() {
    const s = this.state && this.state.session;
    if (!s || this.busy) return;
    if (s.mode === 'ai_vs_ai') { this.send({ type: 'step' }); return; }
    if (!s.is_human_turn || s.pending_facing) return;
    this.send({ type: 'pass' });
  }

  // ------------------------------------------------------------ network
  async send(data) {
    if (this.busy) return;
    this.busy = true;
    this.ui.busy(true, data.type === 'pass' || data.type === 'step' ? 'Playing…' : 'Resolving…');
    try {
      const res = await api.action(data);
      this.ui.busy(false);
      await this.applyState(res.state, res.events || []);
    } catch (e) {
      this.ui.busy(false);
      this.ui.toast(e.message, true);
      try { await this.applyState(await api.state(), []); } catch (_) { /* ignore */ }
    } finally {
      this.busy = false;
      this.updateLos();
      this._afterRequest();
    }
  }

  async newGame(opts) {
    this.busy = true;
    this.ui.busy(true, 'Setting up…');
    try {
      const res = await api.newGame(opts);
      this.selected = null;
      this.lastHumanPlayer = null;
      this.renderer.boardKey = null;
      this.renderer.unitEls.forEach(g => g.remove());
      this.renderer.unitEls.clear();
      this.ui.busy(false);
      // draw the board first, then replay the setup events (coin flip, AI deployment)
      this.renderer.render(res.state);
      this.fitBoard();
      await this.applyState(res.state, (res.events || []).filter(e => e.type !== 'action'));
      this.fitBoard();
      this.ui.toast(`New game — seed ${res.state.session.seed}`);
    } catch (e) {
      this.ui.busy(false);
      this.ui.toast(e.message, true);
    } finally {
      this.busy = false;
      this._afterRequest();
    }
  }

  // ------------------------------------------------------------ zoom/pan
  _setupZoomPan() {
    const c = $('board-container'), b = $('board');
    const stored = parseFloat(localStorage.getItem('aa_zoom'));
    let zoom = stored || 1;
    this.userZoomed = !!stored;
    const apply = () => { this.renderer.setZoom(zoom); };
    const setZoom = (z, persist = true) => {
      zoom = Math.max(0.3, Math.min(3, z));
      apply();
      if (persist) { localStorage.setItem('aa_zoom', zoom); this.userZoomed = true; }
    };
    const fit = () => {
      setZoom(this.renderer.fitZoom(c.clientWidth, c.clientHeight), false);
      localStorage.removeItem('aa_zoom');
      this.userZoomed = false;
    };
    this.fitBoard = fit;
    $('zoom-in').onclick = () => setZoom(zoom * 1.15);
    $('zoom-out').onclick = () => setZoom(zoom / 1.15);
    $('zoom-reset').onclick = () => fit();
    c.addEventListener('wheel', (e) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      setZoom(zoom * (e.deltaY < 0 ? 1.1 : 0.9));
    }, { passive: false });
    window.addEventListener('resize', () => { if (!this.userZoomed) fit(); });
    let panning = false, sx = 0, sy = 0, sl = 0, st = 0, moved = false;
    c.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      if (e.target.closest('.unit') || e.target.closest('.hl') || e.target.closest('.facing-btn')) return;
      panning = true; moved = false; sx = e.clientX; sy = e.clientY; sl = c.scrollLeft; st = c.scrollTop;
    });
    window.addEventListener('mousemove', (e) => {
      if (!panning) return;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      if (Math.abs(dx) + Math.abs(dy) > 4) { moved = true; c.classList.add('panning'); }
      c.scrollLeft = sl - dx; c.scrollTop = st - dy;
    });
    window.addEventListener('mouseup', () => { panning = false; c.classList.remove('panning'); });
    // swallow the click that ends a drag so it doesn't deselect
    c.addEventListener('click', (e) => { if (moved) { e.stopPropagation(); moved = false; } }, true);
    apply();
  }
}

const app = new App();
window.app = app;
app.start().catch(e => { console.error(e); document.body.insertAdjacentHTML('beforeend', `<pre style="color:#f88;padding:20px">${e.stack || e}</pre>`); });
