// SVG board renderer. This is the one module that would be swapped for a
// different look (richer 2D art, or 3D). Its contract with app.js:
//   render(state)                 draw/update everything from a state payload
//   setHighlights([{q,r,kind,label,data}])
//   setSelection(unitId|null)
//   showFacingPicker(unitId, cb) / hideFacingPicker()
//   showLos(a, b) / hideLos()
//   playEvents(events) -> Promise (animates dice, moves, deaths)
//   setCoords(bool), setFast(bool)
import { SIZE, HEX_H, axialToPixel, polygonPoints, viewBox, DIRS, DIR_NAMES, dirAngleDeg, fmtHex, offsetifyText } from './hex.js';

const NS = 'http://www.w3.org/2000/svg';
export const el = (tag, attrs = {}, cls = null) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  if (cls) e.setAttribute('class', cls);
  return e;
};
export const TERRAIN_COLORS = {
  open: '#e8e4c9', forest: '#3f8f45', building: '#a39d93', water: '#4a7fe1', road: '#e8e4c9',
  hill: '#c9b98a', marsh: '#9fb7a4', town: '#d9a066', ruins: '#8f8a82', stream: '#7fb2e5', impassable: '#222',
};
export const EDGE_STYLES = {
  stream: { stroke: '#2f6fd6', width: 9, halo: '#cfe2ff' },
  hedge: { stroke: '#1f5d2a', width: 7, dash: '2 5', halo: '#8fbf7f' },
  hedgerow: { stroke: '#1f5d2a', width: 7, dash: '2 5', halo: '#8fbf7f' },
  'barbed wire': { stroke: '#7a4a12', width: 4, dash: '3 3' },
  destroyed_bridge: { stroke: '#2f6fd6', width: 9, dash: '2 4', halo: '#cfe2ff' },
};
export const ROAD_STYLE = { edge: '#6b4a24', fill: '#d8b37a', edgeWidth: 12, width: 8 };

// Small terrain symbols drawn on top of a hex's colour so terrain reads at a
// glance (and without relying on colour alone): trees, contour lines, reeds,
// roofs, waves. Deterministic per hex so the map doesn't shimmer on redraw.
export function terrainSymbol(terrain, x, y, seed = 0) {
  const g = el('g', { 'pointer-events': 'none' }, `sym sym-${terrain}`);
  const rnd = (i) => { const v = Math.sin((seed + 1) * 12.9898 + i * 78.233) * 43758.5453; return v - Math.floor(v); };
  if (terrain === 'forest') {
    for (const [dx, dy, r] of [[-12, -6, 9], [10, -9, 8], [0, 9, 10], [14, 8, 6], [-15, 10, 6]]) {
      const jx = (rnd(dx) - 0.5) * 4, jy = (rnd(dy) - 0.5) * 4;
      g.appendChild(el('circle', { cx: x + dx + jx, cy: y + dy + jy, r, fill: '#2d6e33', stroke: '#1f4f24', 'stroke-width': 1.2 }));
    }
  } else if (terrain === 'hill') {
    for (const [rx, ry] of [[26, 14], [17, 9], [8, 4.5]]) {
      g.appendChild(el('ellipse', { cx: x, cy: y + 3, rx, ry, fill: 'none', stroke: '#8a7443', 'stroke-width': 1.6 }));
    }
  } else if (terrain === 'marsh') {
    for (const [dx, dy] of [[-14, -8], [8, -12], [-4, 6], [14, 6], [-16, 12]]) {
      const bx = x + dx, by = y + dy;
      g.appendChild(el('path', { d: `M${bx - 5},${by} Q${bx - 3},${by - 9} ${bx - 1},${by - 12} M${bx},${by} L${bx},${by - 13} M${bx + 5},${by} Q${bx + 3},${by - 9} ${bx + 1},${by - 12}`,
        fill: 'none', stroke: '#3e6b4e', 'stroke-width': 1.4, 'stroke-linecap': 'round' }));
      g.appendChild(el('line', { x1: bx - 7, y1: by + 2, x2: bx + 7, y2: by + 2, stroke: '#4c86a8', 'stroke-width': 1.4 }));
    }
  } else if (terrain === 'town' || terrain === 'building' || terrain === 'ruins') {
    const spots = terrain === 'town' ? [[-13, -9], [7, -12], [-6, 6], [12, 5]] : [[0, 0]];
    spots.forEach(([dx, dy], i) => {
      const w = terrain === 'town' ? 12 : 18, h = terrain === 'town' ? 9 : 13;
      const bx = x + dx - w / 2, by = y + dy - h / 2;
      g.appendChild(el('rect', { x: bx, y: by, width: w, height: h, fill: terrain === 'ruins' ? '#77716a' : '#f3ecdc', stroke: '#5b4630', 'stroke-width': 1.2 }));
      g.appendChild(el('path', { d: `M${bx - 1},${by} L${bx + w / 2},${by - h * 0.55} L${bx + w + 1},${by} Z`,
        fill: terrain === 'ruins' ? '#5d5852' : '#a8452c', stroke: '#5b4630', 'stroke-width': 1 }));
    });
  } else if (terrain === 'water') {
    for (const dy of [-8, 2, 12]) {
      g.appendChild(el('path', { d: `M${x - 16},${y + dy} q4,-4 8,0 t8,0 t8,0 t8,0`, fill: 'none', stroke: '#dbe9ff', 'stroke-width': 1.5 }));
    }
  }
  return g;
}
const COVER = new Set(['forest', 'building', 'hill', 'town', 'ruins', 'marsh']);
const PLAYER = {
  player1: { fill: '#3b82f6', light: '#bfdbfe' },
  player2: { fill: '#ef4444', light: '#fecaca' },
};
const STACK_OFFSETS = [[[0, 0]], [[-10, 0], [10, 0]], [[-12, -7], [12, -7], [0, 10]]];

let _skip = false;   // set by a click during playEvents: fast-forward the rest
const sleep = (ms) => new Promise(res => setTimeout(res, _skip ? Math.min(ms, 30) : ms));

const txt = (x, y, s, cls, attrs = {}) => { const t = el('text', { x, y, ...attrs }, cls); t.textContent = s; return t; };

// "M4A1 Sherman" -> "Sherman", "MG 42 Machine Gun Team" -> "MG 42": a label that fits under a token
function shortName(name) {
  const words = String(name || '').replace(/["“”]/g, '').split(/\s+/).filter(Boolean);
  if (!words.length) return '';
  const hasDigit = (w) => /\d/.test(w);
  let pick = words.find(w => w.length >= 4 && !hasDigit(w) && !/^(the|and|of|de|mk|ausf|type|model)$/i.test(w));
  if (!pick || /^(machine|infantry|rifle|gun|team|gunner|veteran|elite)$/i.test(pick)) pick = words.slice(0, 2).join(' ');
  return pick.length > 10 ? pick.slice(0, 9) + '…' : pick;
}

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

  setFast(v) { this.setPace(v ? 'fast' : 'normal'); }

  // fast 0.3x (testing) | normal | slow 2x | step: slow, and wait for Next after each enemy action
  setPace(p) {
    this.pace = p;
    this.fast = p === 'fast';
    for (const g of this.unitEls.values()) g.classList.toggle('fast', this.fast);
  }

  nextMove() {
    if (this._next) { const r = this._next; this._next = null; this.waitingForNext = false; r(); }
  }

  _caption(ev, show) {
    const box = document.getElementById('move-caption');
    if (!box) return;
    if (!show) { box.hidden = true; return; }
    box.querySelector('.who').textContent = `${ev.player === 'player1' ? 'Player 1' : 'Player 2'}${ev.unit_name ? ' · ' + ev.unit_name : ''}`;
    box.querySelector('.who').className = `who ${ev.player === 'player1' ? 'p1' : 'p2'}`;
    box.querySelector('.what').textContent = offsetifyText(String(ev.message || '').split('\n')[0]);
    box.querySelector('#btn-next-move').hidden = this.pace !== 'step';
    box.hidden = false;
  }
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
    this.zone = state.session.deployment_zone || null;
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
    const symbols = [];
    for (const h of board.hexes) {
      const p = el('polygon', { points: polygonPoints(h.q, h.r), 'data-q': h.q, 'data-r': h.r });
      // a road is drawn as a road, over whatever ground it crosses; 'road' terrain
      // (hand-made scenarios) is plain ground with a road on it
      this._styleHex(p, h.terrain === 'road' ? 'open' : h.terrain);
      this.layers.terrain.appendChild(p);
      const c0 = axialToPixel(h.q, h.r);
      symbols.push(terrainSymbol(h.terrain, c0.x, c0.y, h.q * 31 + h.r * 17));
      const { x, y } = axialToPixel(h.q, h.r);
      this.layers.labels.appendChild(txt(x, y + 30, fmtHex(h.q, h.r), 'hex-label', { 'text-anchor': 'middle' }));
    }
    const symG = el('g', { 'pointer-events': 'none' }, 'terrain-symbols');
    symbols.forEach(sy => symG.appendChild(sy));
    this.layers.terrain.appendChild(symG);
    const roadG = this._drawRoads(board);
    this.layers.terrain.appendChild(roadG);
    this.layers.labels.style.display = this.showCoords ? '' : 'none';
  }

  // Roads as smooth lines: the link graph is split into chains between junctions
  // and dead ends, each drawn as one path with rounded bends (quadratic curves
  // through the midpoints of its links); a road that reaches the map edge runs on
  // off the map instead of stopping at the last hex centre.
  _drawRoads(board) {
    const links = board.roads || [];
    const g = el('g', { 'pointer-events': 'none' }, 'roads');
    if (!links.length) return g;
    const key = (h) => `${h[0]},${h[1]}`;
    const adj = new Map();
    const add = (a, b) => { if (!adj.has(key(a))) adj.set(key(a), { h: a, n: [] }); adj.get(key(a)).n.push(b); };
    for (const [a, b] of links) { add(a, b); add(b, a); }
    const cols = board.width, rows = board.height;
    const off = (h) => [h[0], h[1] + (h[0] - (h[0] & 1)) / 2];
    const onEdge = (h) => { const [c, r] = off(h); return c === 0 || c === cols - 1 || r === 0 || r === rows - 1; };
    const outward = (h) => {           // unit vector leaving the map from an edge hex
      const [c, r] = off(h);
      if (c === 0) return [-1, 0];
      if (c === cols - 1) return [1, 0];
      if (r === 0) return [0, -1];
      return [0, 1];
    };
    const used = new Set();
    const edgeKey = (a, b) => [key(a), key(b)].sort().join('|');
    const chains = [];
    const nodes = [...adj.values()];
    const isStop = (n) => n.n.length !== 2;
    const walk = (start, next) => {
      const chain = [start.h];
      let prev = start, cur = adj.get(key(next));
      used.add(edgeKey(start.h, next));
      chain.push(cur.h);
      while (!isStop(cur)) {
        const nxt = cur.n.find(x => !used.has(edgeKey(cur.h, x)));
        if (!nxt) break;
        used.add(edgeKey(cur.h, nxt));
        prev = cur; cur = adj.get(key(nxt));
        chain.push(cur.h);
      }
      return chain;
    };
    for (const n of nodes.filter(isStop)) for (const nb of n.n) if (!used.has(edgeKey(n.h, nb))) chains.push(walk(n, nb));
    for (const n of nodes) for (const nb of n.n) if (!used.has(edgeKey(n.h, nb))) chains.push(walk(n, nb));   // loops
    const P = (h) => axialToPixel(h[0], h[1]);
    const pathFor = (chain) => {
      const pts = chain.map(P);
      // run off the map at dead ends on the edge
      const ext = (h, p) => { const [dx, dy] = outward(h); return { x: p.x + dx * SIZE * 1.6, y: p.y + dy * SIZE * 1.6 }; };
      const endHex = (i) => adj.get(key(chain[i])).n.length === 1 && onEdge(chain[i]);
      if (endHex(0)) pts.unshift(ext(chain[0], pts[0]));
      if (endHex(chain.length - 1)) pts.push(ext(chain[chain.length - 1], pts[pts.length - 1]));
      let d = `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
      for (let i = 1; i < pts.length - 1; i++) {
        const m = { x: (pts[i].x + pts[i + 1].x) / 2, y: (pts[i].y + pts[i + 1].y) / 2 };
        d += ` Q${pts[i].x.toFixed(1)},${pts[i].y.toFixed(1)} ${m.x.toFixed(1)},${m.y.toFixed(1)}`;
      }
      const last = pts[pts.length - 1];
      d += ` L${last.x.toFixed(1)},${last.y.toFixed(1)}`;
      return d;
    };
    const ds = chains.map(pathFor);
    for (const [stroke, w] of [[ROAD_STYLE.edge, ROAD_STYLE.edgeWidth], [ROAD_STYLE.fill, ROAD_STYLE.width]]) {
      for (const d of ds) g.appendChild(el('path', { d, fill: 'none', stroke, 'stroke-width': w,
        'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    }
    // a village is where roads meet: a small square under the junction
    return g;
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
      const px = -dy / len * SIZE * 0.5, py = dx / len * SIZE * 0.5;   // the whole shared hex side
      const style = EDGE_STYLES[eo.type] || { stroke: '#8b5cf6', width: 4 };
      if (style.halo) {
        g.appendChild(el('line', { x1: mx - px, y1: my - py, x2: mx + px, y2: my + py,
          stroke: style.halo, 'stroke-width': style.width + 5, 'stroke-linecap': 'round', opacity: 0.9 }));
      }
      const line = el('line', { x1: mx - px, y1: my - py, x2: mx + px, y2: my + py,
        stroke: style.stroke, 'stroke-width': style.width, 'stroke-dasharray': style.dash || '', 'stroke-linecap': 'round' });
      const t = el('title'); t.textContent = eo.type; line.appendChild(t);
      g.appendChild(line);
      // a road crossing a stream is a bridge
      if (eo.type === 'stream' && eo.bridge) {
        // a bridge deck carrying the road across, with dark parapets
        const b1 = { x: a.x + dx * .32, y: a.y + dy * .32 }, b2 = { x: a.x + dx * .68, y: a.y + dy * .68 };
        g.appendChild(el('line', { x1: b1.x, y1: b1.y, x2: b2.x, y2: b2.y, stroke: '#3b2a17', 'stroke-width': 16, 'stroke-linecap': 'butt' }));
        g.appendChild(el('line', { x1: b1.x, y1: b1.y, x2: b2.x, y2: b2.y, stroke: ROAD_STYLE.fill, 'stroke-width': 11, 'stroke-linecap': 'butt' }));
      }
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
    // deployment zone shading
    if (this.zone && this.zone.length) {
      for (const [q, r] of this.zone) g.appendChild(el('polygon', { points: polygonPoints(q, r), fill: 'rgba(34,211,238,.12)', 'pointer-events': 'none' }));
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
    const visible = state.game.units.filter(u => u.is_alive && u.is_deployed && !u.carried_by_id && (u.card.unit_type !== 'Aircraft' || u.is_aircraft_on_map));
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
    const W = (d, w = 2) => el('path', { d, stroke: '#fff', 'stroke-width': w, fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
    const SOLDIER_KINDS = ['soldier', 'mg', 'artillery', 'commander', 'sniper', 'antitank', 'obstacle'];
    if (SOLDIER_KINDS.includes(kind)) {
      g.appendChild(el('circle', { cx: 0, cy: 0, r: 14, fill: pc.fill, stroke, 'stroke-width': 1.5 }));
      if (kind === 'mg') {
        g.appendChild(W('M-9,6 L-3,-2 L3,-2 L9,6 M0,-2 L0,7 M-2,-4 L12,-9'));
      } else if (kind === 'obstacle') {
        g.appendChild(W('M-8,-8 L8,8 M-8,8 L8,-8', 2.5));
      } else if (kind === 'artillery') {
        // mortar / gun: tube on a baseplate with a shell
        g.appendChild(W('M-8,8 L8,8 M-4,8 L6,-7 M-7,-1 L1,3', 2.5));
        g.appendChild(el('circle', { cx: 8, cy: -9, r: 2.2, fill: '#fff' }));
      } else if (kind === 'sniper') {
        g.appendChild(el('circle', { cx: 0, cy: 0, r: 7, fill: 'none', stroke: '#fff', 'stroke-width': 2 }));
        g.appendChild(W('M0,-11 L0,-4 M0,4 L0,11 M-11,0 L-4,0 M4,0 L11,0'));
        g.appendChild(el('circle', { cx: 0, cy: 0, r: 1.8, fill: '#fff' }));
      } else if (kind === 'antitank') {
        // shoulder-fired tube with a big warhead
        g.appendChild(el('circle', { cx: -3, cy: -6, r: 3, fill: '#fff' }));
        g.appendChild(W('M-3,-3 L-3,5 M-3,5 L-7,10 M-3,5 L1,10 M-10,2 L10,-6', 2));
        g.appendChild(el('circle', { cx: 10, cy: -6, r: 3, fill: '#fff' }));
      } else {
        g.appendChild(el('circle', { cx: 0, cy: -6, r: 3, fill: '#fff' }));
        g.appendChild(W('M0,-3 L0,5 M-5,0 L5,0 M0,5 L-4,10 M0,5 L4,10'));
        if (kind === 'commander') {
          g.appendChild(el('polygon', { points: '9,-13 10.8,-9.4 14.8,-8.9 11.9,-6.1 12.6,-2.2 9,-4.1 5.4,-2.2 6.1,-6.1 3.2,-8.9 7.2,-9.4',
            fill: '#ffe600', stroke: '#000', 'stroke-width': .6 }));
        }
      }
    } else if (['tank', 'tank_destroyer', 'assault_gun', 'armored_car', 'halftrack', 'transport', 'sp_artillery'].includes(kind)) {
      const rot = u.facing != null ? dirAngleDeg(u.facing) : 0;
      const body = el('g', { transform: `rotate(${rot.toFixed(1)})` });
      const hull = (w, h, rx) => el('rect', { x: -w / 2, y: -h / 2, width: w, height: h, rx, fill: pc.fill, stroke, 'stroke-width': 1.5 });
      const tracks = (w, h) => { for (const y of [-h / 2 - 1.5, h / 2 - 1.5]) body.appendChild(el('rect', { x: -w / 2, y, width: w, height: 3, fill: '#222', rx: 1.5 })); };
      const wheels = (xs, y) => { for (const cx of xs) for (const cy of [-y, y]) body.appendChild(el('circle', { cx, cy, r: 3, fill: '#222', stroke: '#000', 'stroke-width': .5 })); };
      if (kind === 'tank') {
        body.appendChild(hull(26, 18, 3)); tracks(26, 18);
        body.appendChild(el('rect', { x: -6, y: -4.5, width: 11, height: 9, rx: 2, fill: pc.light, stroke, 'stroke-width': 1 }));
        body.appendChild(el('line', { x1: 4, y1: 0, x2: 17, y2: 0, stroke: '#111', 'stroke-width': 2.5 }));
      } else if (kind === 'tank_destroyer') {
        body.appendChild(hull(26, 18, 3)); tracks(26, 18);
        body.appendChild(el('polygon', { points: '-8,-5 6,-4 6,4 -8,5', fill: pc.light, stroke, 'stroke-width': 1 }));
        body.appendChild(el('line', { x1: 5, y1: 0, x2: 21, y2: 0, stroke: '#111', 'stroke-width': 2.5 }));
      } else if (kind === 'assault_gun') {
        body.appendChild(hull(26, 18, 2)); tracks(26, 18);
        body.appendChild(el('polygon', { points: '-10,-6 8,-6 10,-2 10,2 8,6 -10,6', fill: pc.light, stroke, 'stroke-width': 1 }));
        body.appendChild(el('line', { x1: 8, y1: 0, x2: 18, y2: 0, stroke: '#111', 'stroke-width': 3.5 }));
      } else if (kind === 'sp_artillery') {
        body.appendChild(hull(26, 18, 2)); tracks(26, 18);
        body.appendChild(el('rect', { x: -12, y: -6, width: 12, height: 12, rx: 1, fill: pc.light, stroke, 'stroke-width': 1 }));
        body.appendChild(el('line', { x1: -4, y1: 0, x2: 20, y2: -5, stroke: '#111', 'stroke-width': 3 }));
      } else if (kind === 'armored_car') {
        body.appendChild(hull(24, 14, 6)); wheels([-7, 7], 7.5);
        body.appendChild(el('circle', { cx: 0, cy: 0, r: 4.5, fill: pc.light, stroke, 'stroke-width': 1 }));
        body.appendChild(el('line', { x1: 3, y1: 0, x2: 13, y2: 0, stroke: '#111', 'stroke-width': 2 }));
      } else if (kind === 'halftrack') {
        body.appendChild(hull(24, 16, 3));
        for (const y of [-9.5, 6.5]) body.appendChild(el('rect', { x: -12, y, width: 14, height: 3, fill: '#222', rx: 1.5 }));
        wheels([8], 8);
        body.appendChild(el('rect', { x: -10, y: -4, width: 12, height: 8, fill: pc.light, rx: 1, stroke, 'stroke-width': .8 }));
      } else {
        body.appendChild(hull(24, 16, 5)); wheels([-7, 7], 8);
        body.appendChild(el('rect', { x: -4, y: -5, width: 12, height: 10, fill: pc.light, rx: 1, stroke, 'stroke-width': .8 }));
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
      // plan view: fuselage, swept wings, tailplane
      g.appendChild(el('path', { d: 'M0,-17 L3,-10 L15,-2 L15,2 L3,0 L2,9 L7,12 L7,14 L0,12 L-7,14 L-7,12 L-2,9 L-3,0 L-15,2 L-15,-2 L-3,-10 Z',
        fill: pc.fill, stroke, 'stroke-width': 1.2, 'stroke-linejoin': 'round' }));
      g.appendChild(el('circle', { cx: 0, cy: -6, r: 2, fill: '#fff' }));
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
    g.appendChild(txt(0, 27, shortName(u.card.name), 'unit-name', { 'text-anchor': 'middle' }));
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

  // Shade hexes not visible from the selected unit (LOS debug overlay)
  setLosShade(blocked) {
    let g = this.layers.markers.querySelector('.los-shade');
    if (g) g.remove();
    if (!blocked || !blocked.length) return;
    g = el('g', {}, 'los-shade');
    for (const [q, r] of blocked) g.appendChild(el('polygon', { points: polygonPoints(q, r), fill: 'rgba(0,0,0,.45)', 'pointer-events': 'none' }));
    this.layers.markers.appendChild(g);
  }

  _drawSelectionRing() {
    const old = this.layers.overlay.querySelector('.sel-ring');
    if (old) old.remove();
    if (!this.selected) return;
    const pos = this.unitPos.get(this.selected);
    if (!pos) return;
    this.layers.overlay.appendChild(el('polygon', { points: polygonPoints(pos[0], pos[1]) }, 'sel-ring'));
  }

  showPath(path, rolls, waypoints = [], legal = true) {
    this.hidePath();
    if (!path || path.length < 2) return;
    const g = el('g', {}, 'path-preview');
    const pts = path.map(([q, r]) => { const p = axialToPixel(q, r); return `${p.x.toFixed(1)},${p.y.toFixed(1)}`; }).join(' ');
    g.appendChild(el('polyline', { points: pts, fill: 'none', stroke: legal ? '#fff' : '#ef4444', 'stroke-width': 3, 'stroke-dasharray': '6 5', 'stroke-linejoin': 'round', opacity: .9, 'pointer-events': 'none' }));
    waypoints.forEach(([q, r], i) => {
      const p = axialToPixel(q, r);
      g.appendChild(el('circle', { cx: p.x, cy: p.y + 22, r: 8, fill: '#fff', stroke: '#000', 'pointer-events': 'none' }));
      g.appendChild(txt(p.x, p.y + 22, String(i + 1), 'badge'));
    });
    for (const roll of rolls || []) {
      const p = axialToPixel(roll.q, roll.r);
      g.appendChild(el('circle', { cx: p.x, cy: p.y - 24, r: 9, fill: '#fbbf24', stroke: '#000', 'pointer-events': 'none' }));
      g.appendChild(txt(p.x, p.y - 24, '🎲', 'badge', { 'font-size': 11 }));
      const t = el('title'); t.textContent = roll.reason; g.appendChild(t);
    }
    this.layers.overlay.appendChild(g);
  }
  hidePath() { this.layers.overlay.querySelectorAll('.path-preview').forEach(e => e.remove()); }

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
    if (hl && hl.__hl) { this.h.onHighlightClick(hl.__hl, { shift: e.shiftKey }); return; }
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
  async playEvents(events, opts = {}) {
    const pace = this.pace || 'normal';
    const speed = { fast: 0.3, normal: 1, slow: 2, step: 1.6 }[pace] || 1;
    const narrate = opts.narrate || (() => false);
    _skip = false;
    this.animating = true;
    for (const ev of events) {
      if (ev.type === 'action') {
        // narrate the opponent's moves in play (not the setup placements)
        const told = pace !== 'fast' && narrate(ev.player) && ev.success !== false
          && ev.action_type !== 'DeployAction';
        if (told) {
          this._caption(ev, true);
          const g = this.unitEls.get(ev.unit);
          if (g) g.classList.add('acting');
        }
        const subs = ev.events && ev.events.length ? ev.events : null;
        if (subs) {
          for (const s of subs) await this._playSub(s, ev, speed);
        } else if (ev.action_type === 'MoveAction' && ev.success && ev.from && ev.to) {
          await this._animateMove(ev.unit, [ev.from, ev.to], speed);
        }
        if (told) {
          if (pace === 'step' && !_skip) {
            this.waitingForNext = true;
            await new Promise(res => { this._next = res; });
          } else {
            await sleep(pace === 'slow' ? 1400 : 500);
          }
          const g = this.unitEls.get(ev.unit);
          if (g) g.classList.remove('acting');
          this._caption(ev, false);
        }
      } else if (ev.type === 'initiative') {
        await this._banner(`Turn ${ev.turn} — Initiative`, `${ev.rolls.player1.text}\n${ev.rolls.player2.text}\n→ ${ev.winner} wins initiative`, 1400 * speed);
      } else if (ev.type === 'coin_flip') {
        await this._banner('Coin flip', `${ev.winner} wins the toss and chooses who deploys first`, 1600 * speed);
      } else if (ev.type === 'deploy_order') {
        await this._banner('Deployment', `${ev.first} deploys first`, 900 * speed);
      } else if (ev.type === 'turn_order') {
        await this._banner(`Turn ${ev.turn}`, `${ev.first} goes first`, 700 * speed);
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
    this.animating = false;
    _skip = false;
  }

  skipAnimation() {
    if (!this.animating) return;
    _skip = true;
    this.nextMove();      // a click also releases a step-through pause (and skips the rest)
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
