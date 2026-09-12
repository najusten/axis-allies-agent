// SVG board renderer. This is the one module that would be swapped for a
// different look (richer 2D art, or 3D). Its contract with app.js:
//   render(state)                 draw/update everything from a state payload
//   setHighlights([{q,r,kind,label,data}])
//   setSelection(unitId|null)
//   showFacingPicker(unitId, cb) / hideFacingPicker()
//   showLos(a, b) / hideLos()
//   playEvents(events) -> Promise (animates dice, moves, deaths)
//   setCoords(bool), setFast(bool)
import { SIZE, HEX_H, axialToPixel, polygonPoints, viewBox, DIRS, DIR_NAMES, dirAngleDeg } from './hex.js';

const NS = 'http://www.w3.org/2000/svg';
const TERRAIN_COLORS = {
  open: '#e8e4c9', forest: '#2f8f3a', building: '#8a8a8a', water: '#4a7fe1', road: '#a0522d',
  hill: '#9fbf8f', marsh: '#5a6b2f', town: '#cd853f', ruins: '#6f6f6f', stream: '#7fb2e5', impassable: '#222',
};
const COVER = new Set(['forest', 'building', 'hill', 'town', 'ruins']);
const PLAYER = {
  player1: { fill: '#3b82f6', light: '#bfdbfe' },
  player2: { fill: '#ef4444', light: '#fecaca' },
};
const STACK_OFFSETS = [[[0, 0]], [[-10, 0], [10, 0]], [[-12, -7], [12, -7], [0, 10]]];

const sleep = (ms) => new Promise(res => setTimeout(res, ms));
const el = (tag, attrs = {}, cls = null) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  if (cls) e.setAttribute('class', cls);
  return e;
};
const txt = (x, y, s, cls, attrs = {}) => { const t = el('text', { x, y, ...attrs }, cls); t.textContent = s; return t; };

export class BoardRenderer {
  constructor(svg, handlers) {
    this.svg = svg;
    this.h = handlers;             // { onHexClick(q,r), onUnitClick(id), onHighlightClick(hl), onUnitHover(id|null) }
    this.layers = {};
    for (const name of ['terrain', 'labels', 'markers', 'highlights', 'units', 'overlay']) {
      const g = el('g', { id: `layer-${name}` });
      svg.appendChild(g);
      this.layers[name] = g;
    }
    this.boardKey = null;
    this.unitEls = new Map();      // id -> <g>
    this.unitPos = new Map();      // id -> [q,r] (tracks animation)
    this.state = null;
    this.selected = null;
    this.fast = false;
    this.showCoords = false;
    this.svg.addEventListener('click', (e) => this._onClick(e));
    this.svg.addEventListener('mousemove', (e) => this._onHover(e));
  }

  setFast(v) { this.fast = v; for (const g of this.unitEls.values()) g.classList.toggle('fast', v); }
  setZoom(z) {
    this.zoom = z;
    if (!this.vb) return;
    this.svg.setAttribute('width', (this.vb.w * z).toFixed(0));
    this.svg.setAttribute('height', (this.vb.h * z).toFixed(0));
  }
  fitZoom(cw, ch) { return this.vb ? Math.min((cw - 16) / this.vb.w, (ch - 16) / this.vb.h) : 1; }
  setCoords(v) { this.showCoords = v; this.layers.labels.style.display = v ? '' : 'none'; }

  // ------------------------------------------------------------------ render
  render(state) {
    this.state = state;
    const board = state.game.board;
    const key = `${board.width}x${board.height}:${board.hexes.length}`;
    if (key !== this.boardKey) {
      this.boardKey = key;
      this._drawBoard(board);
    } else {
      // terrain can change (AVRE destroys obstacles etc.) — cheap update
      for (const h of board.hexes) {
        const p = this.layers.terrain.querySelector(`polygon[data-q="${h.q}"][data-r="${h.r}"]`);
        if (p && p.dataset.terrain !== h.terrain) this._styleHex(p, h.terrain);
      }
    }
    this._drawMarkers(state.game);
    this._drawUnits(state);
    this._drawSelectionRing();
  }

  _drawBoard(board) {
    const vb = viewBox(board.hexes);
    this.svg.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
    this.vb = vb;
    this.setZoom(this.zoom || 1);
    this.layers.terrain.innerHTML = '';
    this.layers.labels.innerHTML = '';
    for (const h of board.hexes) {
      const p = el('polygon', { points: polygonPoints(h.q, h.r), 'data-q': h.q, 'data-r': h.r });
      this._styleHex(p, h.terrain);
      this.layers.terrain.appendChild(p);
      const { x, y } = axialToPixel(h.q, h.r);
      this.layers.labels.appendChild(txt(x, y + 30, `${h.q},${h.r}`, 'hex-label', { 'text-anchor': 'middle' }));
    }
    this.layers.labels.style.display = this.showCoords ? '' : 'none';
  }

  _styleHex(p, terrain) {
    p.dataset.terrain = terrain;
    p.setAttribute('fill', TERRAIN_COLORS[terrain] || TERRAIN_COLORS.open);
    p.setAttribute('class', `hex hex-${terrain}${COVER.has(terrain) ? ' cover' : ''}`);
  }

  _drawMarkers(game) {
    const g = this.layers.markers;
    g.innerHTML = '';
    // edge obstacles
    for (const eo of game.board.edge_obstacles || []) {
      const a = axialToPixel(eo.a[0], eo.a[1]), b = axialToPixel(eo.b[0], eo.b[1]);
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy);
      const px = -dy / len * SIZE * 0.45, py = dx / len * SIZE * 0.45;
      const line = el('line', { x1: mx - px, y1: my - py, x2: mx + px, y2: my + py,
        stroke: eo.type.includes('wire') ? '#f59e0b' : '#8b5cf6', 'stroke-width': 4,
        'stroke-dasharray': eo.type.includes('wire') ? '3 3' : '' });
      const t = el('title'); t.textContent = eo.type; line.appendChild(t);
      g.appendChild(line);
    }
    // smoke
    for (const [q, r] of game.smoke || []) {
      const { x, y } = axialToPixel(q, r);
      const grp = el('g', {}, 'smoke');
      grp.appendChild(el('polygon', { points: polygonPoints(q, r), fill: 'rgba(200,200,200,.55)' }));
      for (const [dx, dy, rr] of [[-8, -4, 10], [8, -2, 12], [0, 8, 9]])
        grp.appendChild(el('circle', { cx: x + dx, cy: y + dy, r: rr, fill: 'rgba(230,230,230,.8)' }));
      g.appendChild(grp);
    }
    // wrecks
    for (const w of game.wrecks || []) {
      const { x, y } = axialToPixel(w.q, w.r);
      g.appendChild(txt(x, y + 4, '✖', null, { 'text-anchor': 'middle', 'font-size': 14, fill: '#333', opacity: .6 }));
    }
    // objective
    if (game.objective) {
      const { x, y } = axialToPixel(game.objective.q, game.objective.r);
      const color = game.objective.controller ? PLAYER[game.objective.controller].fill : '#fbbf24';
      const pts = [];
      for (let i = 0; i < 10; i++) {
        const rad = i % 2 ? 6 : 14, a = (Math.PI / 5) * i - Math.PI / 2;
        pts.push(`${(x + rad * Math.cos(a)).toFixed(1)},${(y + rad * Math.sin(a)).toFixed(1)}`);
      }
      const star = el('polygon', { points: pts.join(' '), fill: color, stroke: '#000', 'stroke-width': 1, opacity: .9 });
      const t = el('title'); t.textContent = 'Objective'; star.appendChild(t);
      g.appendChild(star);
    }
  }

  _stackOffsets(units) {
    const byHex = new Map();
    for (const u of units) {
      const k = `${u.position[0]},${u.position[1]}`;
      if (!byHex.has(k)) byHex.set(k, []);
      byHex.get(k).push(u);
    }
    const off = new Map();
    for (const list of byHex.values()) {
      const offs = STACK_OFFSETS[Math.min(list.length, 3) - 1];
      list.forEach((u, i) => off.set(u.id, offs[i % offs.length]));
    }
    return off;
  }

  _drawUnits(state) {
    const visible = state.game.units.filter(u => u.is_alive && !u.carried_by_id && (u.card.unit_type !== 'Aircraft' || u.is_aircraft_on_map));
    const offsets = this._stackOffsets(visible);
    const seen = new Set();
    for (const u of visible) {
      seen.add(u.id);
      let g = this.unitEls.get(u.id);
      if (!g) {
        g = el('g', { 'data-id': u.id }, 'unit');
        if (this.fast) g.classList.add('fast');
        this.layers.units.appendChild(g);
        this.unitEls.set(u.id, g);
      }
      this._updateUnit(g, u, offsets.get(u.id) || [0, 0], state);
      this.unitPos.set(u.id, [u.position[0], u.position[1]]);
    }
    for (const [id, g] of this.unitEls) {
      if (!seen.has(id)) {
        g.classList.add('dead');
        setTimeout(() => g.remove(), 500);
        this.unitEls.delete(id);
        this.unitPos.delete(id);
      }
    }
  }

  _updateUnit(g, u, off, state) {
    const { x, y } = axialToPixel(u.position[0], u.position[1]);
    g.setAttribute('transform', `translate(${(x + off[0]).toFixed(1)},${(y + off[1]).toFixed(1)})`);
    g.classList.toggle('spent', u.has_moved && u.has_attacked);
    const sig = JSON.stringify([u.card.icon, u.owner, u.facing, u.is_disrupted, u.is_damaged, u.has_moved, u.has_attacked,
      u.health, u.carried_unit_id, (state.unit_actions[u.id] || {}).pending_hits]);
    if (g.dataset.sig === sig) return;
    g.dataset.sig = sig;
    g.innerHTML = '';
    const pc = PLAYER[u.owner] || PLAYER.player1;
    g.appendChild(this._icon(u, pc));
    g.appendChild(this._badges(u, state));
    const t = el('title'); t.textContent = `${u.card.name} (${u.owner})`; g.appendChild(t);
  }

  _icon(u, pc) {
    const g = el('g', {}, 'icon');
    const kind = u.card.icon;
    const stroke = '#111';
    if (kind === 'soldier' || kind === 'mg' || kind === 'obstacle') {
      g.appendChild(el('circle', { cx: 0, cy: 0, r: 14, fill: pc.fill, stroke, 'stroke-width': 1.5 }));
      if (kind === 'mg') {
        g.appendChild(el('path', { d: 'M-9,6 L-3,-2 L3,-2 L9,6 M0,-2 L0,7 M-2,-4 L12,-9', stroke: '#fff', 'stroke-width': 2, fill: 'none', 'stroke-linecap': 'round' }));
      } else if (kind === 'obstacle') {
        g.appendChild(el('path', { d: 'M-8,-8 L8,8 M-8,8 L8,-8', stroke: '#fff', 'stroke-width': 2.5 }));
      } else {
        g.appendChild(el('circle', { cx: 0, cy: -6, r: 3, fill: '#fff' }));
        g.appendChild(el('path', { d: 'M0,-3 L0,5 M-5,0 L5,0 M0,5 L-4,10 M0,5 L4,10', stroke: '#fff', 'stroke-width': 2, fill: 'none', 'stroke-linecap': 'round' }));
      }
    } else if (kind === 'tank' || kind === 'transport' || kind === 'halftrack') {
      const rot = u.facing != null ? dirAngleDeg(u.facing) : 0;
      const body = el('g', { transform: `rotate(${rot.toFixed(1)})` });
      if (kind === 'tank') {
        body.appendChild(el('rect', { x: -13, y: -9, width: 26, height: 18, rx: 3, fill: pc.fill, stroke, 'stroke-width': 1.5 }));
        body.appendChild(el('rect', { x: -6, y: -4.5, width: 11, height: 9, rx: 2, fill: pc.light, stroke, 'stroke-width': 1 }));
        body.appendChild(el('line', { x1: 4, y1: 0, x2: 17, y2: 0, stroke: '#111', 'stroke-width': 2.5 }));
      } else if (kind === 'halftrack') {
        body.appendChild(el('rect', { x: -12, y: -8, width: 24, height: 16, rx: 3, fill: pc.fill, stroke, 'stroke-width': 1.5 }));
        body.appendChild(el('circle', { cx: 7, cy: 0, r: 3.5, fill: '#222' }));
        body.appendChild(el('rect', { x: -11, y: -3, width: 12, height: 6, fill: '#222', rx: 2 }));
      } else {
        body.appendChild(el('rect', { x: -12, y: -8, width: 24, height: 16, rx: 5, fill: pc.fill, stroke, 'stroke-width': 1.5 }));
        for (const cx of [-7, 7]) body.appendChild(el('circle', { cx, cy: 6, r: 3, fill: '#222' }));
        body.appendChild(el('rect', { x: -4, y: -5, width: 12, height: 7, fill: pc.light, rx: 1 }));
      }
      g.appendChild(body);
      if (u.facing != null) {
        // front-arc indicator: thick yellow arc on the facing edge
        const a = dirAngleDeg(u.facing) * Math.PI / 180;
        const ax = Math.cos(a), ay = Math.sin(a);
        g.appendChild(el('line', { x1: (ax * 20).toFixed(1), y1: (ay * 20).toFixed(1), x2: (ax * 32).toFixed(1), y2: (ay * 32).toFixed(1),
          stroke: '#ffe600', 'stroke-width': 3, 'stroke-linecap': 'round' }));
        g.appendChild(el('polygon', { points: `${(ax * 36).toFixed(1)},${(ay * 36).toFixed(1)} ${(ax * 28 - ay * 5).toFixed(1)},${(ay * 28 + ax * 5).toFixed(1)} ${(ax * 28 + ay * 5).toFixed(1)},${(ay * 28 - ax * 5).toFixed(1)}`, fill: '#ffe600', stroke: '#000', 'stroke-width': .8 }));
      }
    } else if (kind === 'aircraft') {
      g.appendChild(el('polygon', { points: '0,-16 14,0 0,16 -14,0', fill: pc.fill, stroke, 'stroke-width': 1.5 }));
      g.appendChild(el('path', { d: 'M0,-12 L0,12 M-10,0 L10,0', stroke: '#fff', 'stroke-width': 2 }));
    } else {
      g.appendChild(el('circle', { cx: 0, cy: 0, r: 12, fill: pc.fill, stroke, 'stroke-width': 1.5 }));
    }
    return g;
  }

  _badges(u, state) {
    const g = el('g', {}, 'badges');
    const maxHp = u.card.defense_front || 1;
    const frac = Math.max(0, Math.min(1, u.health / maxHp));
    g.appendChild(el('rect', { x: -12, y: 16, width: 24, height: 4, fill: '#222', rx: 1 }));
    g.appendChild(el('rect', { x: -12, y: 16, width: (24 * frac).toFixed(1), height: 4, rx: 1,
      fill: frac > .6 ? '#22c55e' : frac > .3 ? '#f59e0b' : '#ef4444' }));
    let rx = 16;
    const badge = (label, fill, color = '#000') => {
      g.appendChild(el('circle', { cx: rx, cy: -18, r: 6.5, fill, stroke: '#000', 'stroke-width': .8 }));
      g.appendChild(txt(rx, -18, label, 'badge', { fill: color }));
      rx -= 14;
    };
    const pending = (state.unit_actions[u.id] || {}).pending_hits || 0;
    if (pending) badge(String(pending), '#7c3aed', '#fff');
    if (u.is_disrupted) badge('!', '#fbbf24');
    if (u.is_damaged) badge('D', '#f97316');
    if (u.carried_unit_id) badge('T', '#a855f7', '#fff');
    let lx = -16;
    const lbadge = (label) => {
      g.appendChild(el('circle', { cx: lx, cy: -18, r: 6, fill: '#444', stroke: '#000', 'stroke-width': .6 }));
      g.appendChild(txt(lx, -18, label, 'badge', { fill: '#ddd' }));
      lx += 13;
    };
    if (u.has_moved) lbadge('M');
    if (u.has_attacked) lbadge('A');
    return g;
  }

  // ------------------------------------------------------------ highlights
  setHighlights(list) {
    const g = this.layers.highlights;
    g.innerHTML = '';
    this.highlights = list || [];
    for (const hl of this.highlights) {
      const p = el('polygon', { points: polygonPoints(hl.q, hl.r) }, `hl hl-${hl.kind}`);
      p.__hl = hl;
      g.appendChild(p);
      if (hl.label) {
        const { x, y } = axialToPixel(hl.q, hl.r);
        g.appendChild(txt(x, y - 22, hl.label, 'hl-label'));
      }
    }
  }

  setSelection(id) { this.selected = id; this._drawSelectionRing(); }

  _drawSelectionRing() {
    const old = this.layers.overlay.querySelector('.sel-ring');
    if (old) old.remove();
    if (!this.selected) return;
    const pos = this.unitPos.get(this.selected);
    if (!pos) return;
    this.layers.overlay.appendChild(el('polygon', { points: polygonPoints(pos[0], pos[1]) }, 'sel-ring'));
  }

  showLos(a, b) {
    this.hideLos();
    const p1 = axialToPixel(a[0], a[1]), p2 = axialToPixel(b[0], b[1]);
    this.layers.overlay.appendChild(el('line', { x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y }, 'los-line'));
  }
  hideLos() { this.layers.overlay.querySelectorAll('.los-line').forEach(e => e.remove()); }

  showFacingPicker(unitId, onPick) {
    this.hideFacingPicker();
    const pos = this.unitPos.get(unitId);
    if (!pos) return;
    const { x, y } = axialToPixel(pos[0], pos[1]);
    const grp = el('g', {}, 'facing-picker');
    for (let i = 0; i < 6; i++) {
      const a = dirAngleDeg(i) * Math.PI / 180;
      const bx = x + Math.cos(a) * 38, by = y + Math.sin(a) * 38;
      const b = el('g', {}, 'facing-btn');
      b.appendChild(el('circle', { cx: bx, cy: by, r: 10 }));
      b.appendChild(txt(bx, by, DIR_NAMES[i], null));
      b.addEventListener('click', (e) => { e.stopPropagation(); onPick(i); });
      grp.appendChild(b);
    }
    this.layers.overlay.appendChild(grp);
  }
  hideFacingPicker() { this.layers.overlay.querySelectorAll('.facing-picker').forEach(e => e.remove()); }

  // ------------------------------------------------------------- events
  _onClick(e) {
    const hl = e.target.closest('.hl');
    if (hl && hl.__hl) { this.h.onHighlightClick(hl.__hl); return; }
    const unit = e.target.closest('.unit');
    if (unit) { this.h.onUnitClick(unit.dataset.id); return; }
    const hex = e.target.closest('.hex');
    if (hex) { this.h.onHexClick(+hex.dataset.q, +hex.dataset.r); return; }
    this.h.onHexClick(null, null);
  }
  _onHover(e) {
    const unit = e.target.closest('.unit');
    const hl = e.target.closest('.hl');
    this.h.onHover(unit ? unit.dataset.id : null, hl && hl.__hl ? hl.__hl : null);
  }

  // ---------------------------------------------------------- animation
  async playEvents(events) {
    const speed = this.fast ? 0.3 : 1;
    for (const ev of events) {
      if (ev.type === 'action') {
        const subs = ev.events && ev.events.length ? ev.events : null;
        if (subs) {
          for (const s of subs) await this._playSub(s, ev, speed);
        } else if (ev.action_type === 'MoveAction' && ev.success && ev.from && ev.to) {
          await this._animateMove(ev.unit, [ev.from, ev.to], speed);
        }
      } else if (ev.type === 'initiative') {
        await this._banner(`Turn ${ev.turn} — Initiative`, `${ev.rolls.player1.text}\n${ev.rolls.player2.text}\n→ ${ev.first} first`, 1400 * speed);
      } else if (ev.type === 'casualty') {
        for (const [uid, name] of ev.destroyed || []) {
          const g = this.unitEls.get(uid);
          if (g) { await this._popup(this.unitPos.get(uid), `${name}`, [], 'DESTROYED', 'bad', 800 * speed); g.classList.add('dead'); }
        }
        await sleep(300 * speed);
      } else if (ev.type === 'game_over') {
        await this._banner('Game over', `${ev.winner} wins (${ev.reason})`, 1500 * speed);
      }
    }
  }

  async _playSub(s, parent, speed) {
    if (s.type === 'move') {
      await this._animateMove(s.unit, s.path && s.path.length > 1 ? s.path : [s.from, s.to], speed);
    } else if (s.type === 'movement_roll') {
      const pos = this.unitPos.get(s.unit);
      await this._popup(pos, `${s.name} — ${s.reason} check`, [{ v: s.roll, hit: s.success }],
        s.success ? `${s.roll} ≥ ${s.needed}: passes` : `${s.roll} < ${s.needed}: stuck`, s.success ? 'good' : 'bad', 1100 * speed,
        `need ${s.needed}+${s.reroll ? ' (rerolled)' : ''}`);
    } else if (s.type === 'attack') {
      const pos = s.target_hex || this.unitPos.get(s.target);
      const dice = (s.rolls || []).map(v => ({ v, hit: v >= (s.threshold || 4) }));
      const outcome = (s.outcome || '').toUpperCase();
      const cls = outcome === 'MISS' ? 'neutral' : 'bad';
      const from = this.unitPos.get(s.attacker);
      if (from && pos) this.showLos(from, pos);
      await this._popup(pos, `${s.attacker_name} → ${s.target_name}${s.blast ? ' (blast)' : ''}`, dice,
        `${s.successes}/${s.dice} vs def ${s.defense} — ${outcome}`, cls, 1500 * speed,
        `hit on ${s.threshold}+`);
      this.hideLos();
    } else if (s.type === 'cover_save') {
      const pos = this.unitPos.get(s.unit);
      await this._popup(pos, `${s.name} — cover roll`, [{ v: s.roll, hit: s.success }],
        s.success ? `${s.roll} ≥ ${s.needed}: saved (Disrupted only)` : `${s.roll} < ${s.needed}: no save`,
        s.success ? 'good' : 'bad', 1300 * speed, `need ${s.needed}+`);
    } else if (s.type === 'defensive_fire') {
      const pos = s.attack_hex || this.unitPos.get(s.target);
      const dice = (s.rolls || []).map(v => ({ v, hit: v >= 4 }));
      const from = this.unitPos.get(s.defender);
      if (from && pos) this.showLos(from, pos);
      await this._popup(pos, `Defensive fire`, dice,
        `${s.successes}/${s.dice} vs def ${s.defense} — ${s.disrupted ? 'DISRUPTED' : 'no effect'}${s.movement_stopped ? ', movement stopped' : ''}`,
        s.disrupted ? 'bad' : 'neutral', 1500 * speed);
      this.hideLos();
    } else if (s.type === 'pending_hits') {
      const g = this.unitEls.get(s.unit);
      if (g) { g.classList.add('flash'); setTimeout(() => g.classList.remove('flash'), 400); }
    } else if (s.type === 'status' && s.change === 'destroyed') {
      const g = this.unitEls.get(s.unit);
      if (g) g.classList.add('dead');
      await sleep(300 * speed);
    }
  }

  async _animateMove(unitId, path, speed) {
    const g = this.unitEls.get(unitId);
    if (!g || !path) return;
    for (let i = 1; i < path.length; i++) {
      const [q, r] = path[i];
      const { x, y } = axialToPixel(q, r);
      g.setAttribute('transform', `translate(${x.toFixed(1)},${y.toFixed(1)})`);
      this.unitPos.set(unitId, [q, r]);
      await sleep((this.fast ? 130 : 480) * (i === path.length - 1 ? 1 : 0.8));
    }
    if (this.selected === unitId) this._drawSelectionRing();
  }

  async _popup(pos, title, dice, outcome, cls, ms, sub) {
    if (!pos) return;
    const { x, y } = axialToPixel(pos[0], pos[1]);
    const w = Math.max(190, 24 + dice.length * 20), h = 58 + (sub ? 12 : 0);
    let px = x - w / 2, py = y - HEX_H / 2 - h - 6;
    if (this.vb) {
      px = Math.max(this.vb.x + 4, Math.min(px, this.vb.x + this.vb.w - w - 4));
      if (py < this.vb.y + 4) py = y + HEX_H / 2 + 6;
    }
    const g = el('g', { transform: `translate(${px.toFixed(1)},${py.toFixed(1)})` }, 'dice-pop');
    g.appendChild(el('rect', { x: 0, y: 0, width: w, height: h }, 'bg'));
    g.appendChild(txt(8, 14, title, 'title'));
    let dy = 22;
    if (sub) { g.appendChild(txt(8, 26, sub, 'sub')); dy = 32; }
    dice.forEach((d, i) => {
      g.appendChild(el('rect', { x: 8 + i * 20, y: dy, width: 16, height: 16 }, `die ${d.hit ? 'hit' : 'miss'}`));
      g.appendChild(txt(16 + i * 20, dy + 8, String(d.v), 'die-txt'));
    });
    g.appendChild(txt(8, h - 8, outcome, `outcome ${cls}`));
    this.layers.overlay.appendChild(g);
    await sleep(ms);
    g.remove();
  }

  async _banner(title, body, ms) {
    if (!this.vb) return;
    const w = 300, h = 80;
    const x = this.vb.x + this.vb.w / 2 - w / 2, y = this.vb.y + 20;
    const g = el('g', { transform: `translate(${x},${y})` }, 'dice-pop');
    g.appendChild(el('rect', { x: 0, y: 0, width: w, height: h }, 'bg'));
    g.appendChild(txt(12, 20, title, 'title'));
    body.split('\n').forEach((line, i) => g.appendChild(txt(12, 38 + i * 14, line, 'sub')));
    this.layers.overlay.appendChild(g);
    await sleep(ms);
    g.remove();
  }
}
