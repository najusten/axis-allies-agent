// DOM panels around the board: top bar, sidebar lists, unit card, log,
// ability panel, modals (new game, hot-seat handoff, game over), toasts.
import { fmtHex, offsetifyText } from './hex.js';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export class UI {
  constructor(handlers) {
    this.h = handlers;   // { onSelectUnit(id), onAbility(ability), onEndPhase, onUndo, onRedo, onNewGame(opts), onHoverUnit(id|null) }
    this.abilityDescs = {};
    $('btn-end').onclick = () => this.h.onEndPhase();
    $('btn-undo').onclick = () => this.h.onUndo();
    $('btn-redo').onclick = () => this.h.onRedo();
    $('btn-new').onclick = () => this.showNewGame();
    this._toastTimer = null;
  }

  setAbilityDescriptions(d) { this.abilityDescs = d || {}; }

  // ------------------------------------------------------------- top bar
  renderTop(state, selectedId) {
    const s = state.session, g = state.game;
    $('tb-turn').textContent = `Turn ${g.turn}`;
    $('tb-phase').textContent = s.game_over ? 'Game over' : `${s.phase_label} phase`;
    const pl = $('tb-player');
    pl.className = 'pill player ' + (s.current_player === 'player1' ? 'p1' : 'p2');
    if (s.game_over) {
      pl.textContent = `${s.result?.winner ?? '—'} wins (${s.result?.reason ?? ''})`;
    } else if (s.is_human_turn) {
      pl.textContent = s.mode === 'hotseat' ? `${this.playerName(s.current_player)} — your turn` : 'Your turn';
    } else {
      pl.textContent = `${this.playerName(s.current_player)} (AI) is playing…`;
      pl.classList.add('ai');
    }
    $('tb-hint').textContent = s.pending_facing ? 'Choose a facing direction for your vehicle (yellow buttons).' : (s.is_human_turn ? s.phase_hint : 'Click anywhere to skip the animation.');
    $('btn-undo').disabled = !s.can_undo;
    $('btn-redo').disabled = !s.can_redo;
    $('btn-end').disabled = !s.is_human_turn || !!s.pending_facing || s.game_over;
    $('btn-end').textContent = s.mode === 'ai_vs_ai' ? 'Next phase ▶' : 'End Phase';
    if (s.mode === 'ai_vs_ai') $('btn-end').disabled = s.game_over;
  }

  playerName(p) { return p === 'player1' ? 'Player 1' : 'Player 2'; }

  // ------------------------------------------------------------- sidebar
  renderSidebar(state, selectedId) {
    for (const p of ['player1', 'player2']) {
      const ul = $(p === 'player1' ? 'units-p1' : 'units-p2');
      ul.innerHTML = '';
      const units = state.game.units.filter(u => u.owner === p && u.is_alive);
      $(p === 'player1' ? 'p1-count' : 'p2-count').textContent = `(${units.length})`;
      for (const u of units) {
        const li = document.createElement('li');
        li.className = `unit-item ${p === 'player1' ? 'p1' : 'p2'}${u.id === selectedId ? ' selected' : ''}`;
        li.dataset.id = u.id;
        const tags = [];
        const ua = state.unit_actions[u.id] || {};
        if (ua.pending_hits) tags.push(`<span class="tag pend">${ua.pending_hits} pending</span>`);
        if (u.is_disrupted) tags.push('<span class="tag dis">disrupted</span>');
        if (u.is_damaged) tags.push('<span class="tag dmg">damaged</span>');
        if (u.carried_by_id) tags.push('<span class="tag">aboard</span>');
        if (u.card.unit_type === 'Aircraft' && !u.is_aircraft_on_map) tags.push('<span class="tag">off-map</span>');
        if (!u.is_deployed && u.card.unit_type !== 'Aircraft') tags.push('<span class="tag pend">deploy</span>');
        if (u.has_moved && u.has_attacked) tags.push('<span class="tag done">done</span>');
        else if (u.has_moved) tags.push('<span class="tag done">moved</span>');
        else if (u.has_attacked) tags.push('<span class="tag done">fired</span>');
        const pos = (u.card.unit_type === 'Aircraft' && !u.is_aircraft_on_map) ? '✈' : !u.is_deployed ? '—' : fmtHex(u.position[0], u.position[1]);
        li.innerHTML = `<span class="nm">${esc(u.card.name)}</span>${tags.join('')}<span class="st">${pos}</span>`;
        li.onclick = () => this.h.onSelectUnit(u.id);
        li.onmouseenter = () => this.h.onHoverUnit(u.id);
        li.onmouseleave = () => this.h.onHoverUnit(null);
        ul.appendChild(li);
      }
    }
  }

  renderCard(state, unitId) {
    const box = $('unit-card');
    const u = unitId && state.game.units.find(x => x.id === unitId);
    if (!u) { box.hidden = true; return; }
    const s = state.session;
    const hidden = s.mode === 'hotseat' && u.owner !== s.current_player && !s.game_over;
    const c = u.card;
    let html = `<h4>${esc(c.name)}</h4><div class="meta">${esc(c.nation)} · ${esc(c.unit_type)} · ${c.year ?? ''} · cost ${c.cost}</div>`;
    if (hidden) {
      html += `<div class="hidden-note">Opponent's card is hidden in hot-seat mode.</div>`;
    } else {
      html += `<table><tr><th></th><th>short</th><th>med</th><th>long</th></tr>
        <tr><th>vs Soldier</th><td>${c.per[0]}</td><td>${c.per[1]}</td><td>${c.per[2]}</td></tr>
        <tr><th>vs Vehicle</th><td>${c.veh[0]}</td><td>${c.veh[1]}</td><td>${c.veh[2]}</td></tr></table>
        <div>Defense <b>${c.defense_front}${c.defense_rear !== c.defense_front ? '/' + c.defense_rear : ''}</b> · Speed <b>${c.speed}</b> · Health <b>${u.health}</b></div>`;
      const mine = s.human_players.includes(u.owner);
      if (mine && !s.game_over) {
        html += `<div style="margin:6px 0"><label class="chk" style="margin:0"><input type="checkbox" id="card-holdfire" ${u.hold_defensive_fire ? 'checked' : ''}> hold defensive fire (rulebook: optional)</label></div>`;
      }
      if (c.abilities.length) {
        html += '<div class="ab">';
        for (const a of c.abilities) {
          const d = this.abilityDesc(a);
          html += `<div><b>${esc(a)}</b>${d ? ' — ' + esc(d) : ''}</div>`;
        }
        html += '</div>';
      }
    }
    box.innerHTML = html;
    box.hidden = false;
    const hf = box.querySelector('#card-holdfire');
    if (hf) hf.onchange = () => this.h.onHoldFire(u.id, hf.checked);
  }

  abilityDesc(name) {
    const d = this.abilityDescs;
    if (d[name]) return d[name];
    const base = name.replace(/\s+\d+$/, '').replace(/:\d+$/, '').replace(/_/g, ' ');
    if (d[base]) return d[base];
    const lower = Object.keys(d).find(k => k.toLowerCase() === base.toLowerCase());
    return lower ? d[lower] : '';
  }

  // ------------------------------------------------------------- abilities
  renderAbilityPanel(state, unitId) {
    const panel = $('ability-panel');
    const ua = unitId && state.unit_actions[unitId];
    if (!ua || !ua.abilities.length || !state.session.is_human_turn) { panel.hidden = true; return; }
    const groups = new Map();
    for (const a of ua.abilities) {
      if (!groups.has(a.ability)) groups.set(a.ability, []);
      groups.get(a.ability).push(a);
    }
    panel.innerHTML = '<span class="ab-title">Abilities:</span>';
    for (const [name, variants] of groups) {
      if (variants.length === 1) {
        const b = document.createElement('button');
        b.className = 'btn sm purple';
        b.textContent = name;
        b.title = this.abilityDesc(name);
        b.onclick = () => this.h.onAbility(variants[0]);
        panel.appendChild(b);
      } else {
        const wrap = document.createElement('span');
        wrap.style.display = 'inline-flex'; wrap.style.gap = '3px'; wrap.style.alignItems = 'center';
        const lbl = document.createElement('span'); lbl.textContent = name + ':'; lbl.style.color = '#c4b5fd';
        wrap.appendChild(lbl);
        variants.forEach((v, i) => {
          const b = document.createElement('button');
          b.className = 'btn sm purple';
          const tgt = v.target_id ? (state.game.units.find(u => u.id === v.target_id)?.card.name || v.target_id)
            : v.parameters?.new_facing != null ? ['SE', 'NE', 'N', 'NW', 'SW', 'S'][v.parameters.new_facing]
            : v.target ? `${v.target[0]},${v.target[1]}` : `#${i + 1}`;
          b.textContent = tgt;
          b.title = this.abilityDesc(name);
          b.onclick = () => this.h.onAbility(v);
          b.onmouseenter = () => this.h.onHoverAbility(v);
          b.onmouseleave = () => this.h.onHoverAbility(null);
          wrap.appendChild(b);
        });
        panel.appendChild(wrap);
      }
    }
    panel.hidden = false;
  }

  // ------------------------------------------------------------- log
  renderLog(lines) {
    const log = $('log');
    log.innerHTML = lines.map(l => {
      let cls = '';
      if (l.startsWith('▶')) cls = 'l-turn';
      else if (l.startsWith('—')) cls = 'l-phase';
      else if (/DESTROYED|💥|failed|stuck|bogged/.test(l)) cls = 'l-bad';
      else if (/DISRUPTED|DAMAGED|⚡|🔧/.test(l)) cls = 'l-warn';
      else if (/MISS|skipping|no casualties/.test(l)) cls = 'l-dim';
      else if (/saved|✓/.test(l)) cls = 'l-good';
      return `<div class="${cls}">${esc(offsetifyText(l))}</div>`;
    }).join('');
    log.scrollTop = log.scrollHeight;
  }

  // ------------------------------------------------------------- overlays
  toast(msg, isErr = false, ms = 2500) {
    msg = offsetifyText(msg);
    const t = $('toast');
    t.textContent = msg;
    t.className = isErr ? 'err' : '';
    t.hidden = false;
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { t.hidden = true; }, ms);
  }

  busy(on, text) {
    $('busy').hidden = !on;
    if (text) $('busy-text').textContent = text;
  }

  _modal(html) {
    $('modal-box').innerHTML = html;
    $('modal').hidden = false;
    return $('modal-box');
  }
  closeModal() { $('modal').hidden = true; }

  showHandoff(player) {
    return new Promise(resolve => {
      const box = this._modal(`<h2 class="${player === 'player1' ? 'p1' : 'p2'}">${this.playerName(player)}</h2>
        <p>Pass the screen. Click when ready.</p><div class="actions"><button class="btn primary" id="m-ok">I'm ${this.playerName(player)} — continue</button></div>`);
      box.className = 'modal-box handoff';
      box.querySelector('#m-ok').onclick = () => { this.closeModal(); resolve(); };
    });
  }

  showInitiativeChoice(player, state) {
    const rolls = (state.log || []).filter(l => l.includes('🎲')).slice(-2).map(esc).join('<br>');
    return new Promise(resolve => {
      const box = this._modal(`<h2 class="${player === 'player1' ? 'p1' : 'p2'}">${this.playerName(player)} wins the initiative</h2>
        <p style="font-family:monospace">${rolls}</p>
        <p>Go first (act before the opponent) or second (see their moves, then react)?</p>
        <div class="actions"><button class="btn" id="m-second">Go second</button><button class="btn primary" id="m-first">Go first</button></div>`);
      box.className = 'modal-box handoff';
      box.querySelector('#m-first').onclick = () => { this.closeModal(); resolve(true); };
      box.querySelector('#m-second').onclick = () => { this.closeModal(); resolve(false); };
    });
  }

  showDeployOrderChoice(player) {
    return new Promise(resolve => {
      const box = this._modal(`<h2 class="${player === 'player1' ? 'p1' : 'p2'}">${this.playerName(player)} wins the coin flip</h2>
        <p>Deploy first, or second (you'll see the opponent's setup before placing)?</p>
        <div class="actions"><button class="btn" id="m-second">Deploy second</button><button class="btn primary" id="m-first">Deploy first</button></div>`);
      box.className = 'modal-box handoff';
      box.querySelector('#m-first').onclick = () => { this.closeModal(); resolve(true); };
      box.querySelector('#m-second').onclick = () => { this.closeModal(); resolve(false); };
    });
  }

  showDefensiveFireChoice(pend, state) {
    const unitName = (id) => state.game.units.find(u => u.id === id)?.card.name || id;
    const hexLabel = (h) => `(${fmtHex(h.q, h.r)}) ${h.terrain}${h.cover ? ' · cover' : ''}${h.rear ? ' · REAR armor' : ''} · def ${h.defense} · ${h.dice} dice · ${Math.round(h.p_disrupt * 100)}% disrupt`;
    const decisions = {};
    return new Promise(resolve => {
      const rows = pend.options.map((o, i) => `
        <div class="df-row" data-i="${i}" style="border:1px solid var(--line);border-radius:6px;padding:8px;margin:6px 0">
          <div style="font-weight:700;margin-bottom:4px">${esc(o.defender_name)} <span style="color:var(--muted)">at (${fmtHex(o.defender_pos[0], o.defender_pos[1])})</span></div>
          ${o.hexes.map(h => `<label class="chk" style="display:block;margin:2px 0"><input type="radio" name="df-${i}" value="${h.which}" ${o.suggested === h.which ? 'checked' : ''}> Fire while it is in ${esc(hexLabel(h))}${o.suggested === h.which ? ' <b>(suggested)</b>' : ''}</label>`).join('')}
          <label class="chk" style="display:block;margin:2px 0"><input type="radio" name="df-${i}" value="hold"> Hold fire (keep this unit's defensive fire for later this phase)</label>
        </div>`).join('');
      const box = this._modal(`<h2 class="${pend.player === 'player1' ? 'p1' : 'p2'}">${this.playerName(pend.player)}: defensive fire?</h2>
        <p>${pend.kind === 'aircraft_placed'
          ? `${esc(unitName(pend.mover_id))} was placed at (${fmtHex(pend.step_to[0], pend.step_to[1])}) within reach of your Antiair/Ace unit${pend.options.length > 1 ? 's' : ''}. A reaction shot can only disrupt it.`
          : `${esc(unitName(pend.mover_id))} is moving from (${fmtHex(pend.step_from[0], pend.step_from[1])}) to (${fmtHex(pend.step_to[0], pend.step_to[1])}) past your unit${pend.options.length > 1 ? 's' : ''}. Defensive fire can only disrupt; a hit stops the move in that hex.`}</p>
        ${rows}
        <div class="actions"><button class="btn primary" id="df-ok">Resolve</button></div>`);
      box.className = 'modal-box';
      box.style.maxWidth = '640px';
      box.querySelector('#df-ok').onclick = () => {
        pend.options.forEach((o, i) => {
          const v = box.querySelector(`input[name="df-${i}"]:checked`);
          decisions[o.defender_id] = v ? v.value : o.suggested;
        });
        this.closeModal();
        resolve(decisions);
      };
    });
  }

  showGameOver(result) {
    const box = this._modal(`<h2>${esc(result.winner)} wins</h2>
      <p>by ${esc(result.reason)} on turn ${result.turns}</p>
      <p>Player 1: ${result.p1_remaining} units (${Math.round(result.p1_points)} pts) · Player 2: ${result.p2_remaining} units (${Math.round(result.p2_points)} pts)</p>
      <div class="actions"><button class="btn" id="m-close">Look at board</button><button class="btn primary" id="m-new">New game</button></div>`);
    box.className = 'modal-box gameover';
    box.querySelector('#m-close').onclick = () => this.closeModal();
    box.querySelector('#m-new').onclick = () => this.showNewGame();
  }

  async showNewGame() {
    let scenarios = [];
    try { scenarios = await this.h.listScenarios(); } catch (e) { /* ignore */ }
    const box = this._modal(`<h2>New game</h2>
      <div class="row"><label>Mode</label><select id="ng-mode">
        <option value="vs_ai">Human vs AI</option>
        <option value="hotseat">Human vs Human (hot-seat)</option>
        <option value="ai_vs_ai">AI vs AI (watch)</option></select></div>
      <div class="row"><label>AI</label><select id="ng-ai">
        <option value="heuristic">Heuristic (strongest)</option>
        <option value="mcts">Monte Carlo (heuristic + rollouts, ~1.5 s per decision)</option>
        <option value="lookahead">Lookahead (heuristic + 1-ply simulation)</option>
        <option value="aggressive">Aggressive (random-ish, easy)</option>
        <option value="greedy">Greedy (old evaluator)</option>
        <option value="random">Random</option></select></div>
      <div class="row"><label>Armies</label><select id="ng-armies">
        <option value="random">Random armies (points budget)</option>
        <option value="custom">Build my own (points budget)</option>
        <option value="showcase">Showcase (fixed, ability-rich)</option></select></div>
      <div id="ng-custom" hidden class="row"><label></label><span style="color:var(--muted)">You'll pick units for each human side next; AI sides are built automatically.</span></div>
      <div class="row"><label>Points/side</label><input id="ng-points" value="100" inputmode="numeric"></div>
      <div class="row"><label>Max year</label><select id="ng-year"><option value="">any</option>
        ${[1939,1940,1941,1942,1943,1944,1945].map(y => `<option value="${y}">${y}</option>`).join('')}</select></div>
      <div class="row"><label>Historical</label><label style="width:auto"><input type="checkbox" id="ng-hist"> enforce historical army limits (rulebook p.27)</label></div>
      <div class="row"><label>Seed</label><input id="ng-seed" placeholder="random" inputmode="numeric"></div>
      <div class="row"><label>Situation</label><select id="ng-scenario"><option value="">Full game (armies as above)</option>
        ${scenarios.map(s => `<option value="${esc(s.file)}" title="${esc(s.description)}">${esc(s.name)}</option>`).join('')}</select></div>
      <div id="ng-scen-desc" class="row" hidden><label></label><span style="color:var(--muted);font-size:12px"></span></div>
      <div class="actions"><button class="btn" id="ng-cancel">Cancel</button><button class="btn primary" id="ng-start">Start</button></div>`);
    box.className = 'modal-box';
    box.querySelector('#ng-cancel').onclick = () => this.closeModal();
    box.querySelector('#ng-armies').onchange = (e) => { box.querySelector('#ng-custom').hidden = e.target.value !== 'custom'; };
    box.querySelector('#ng-scenario').onchange = (e) => {
      const sc = scenarios.find(s => s.file === e.target.value);
      const d = box.querySelector('#ng-scen-desc');
      d.hidden = !sc; if (sc) d.querySelector('span').textContent = sc.description;
    };
    box.querySelector('#ng-start').onclick = async () => {
      const opts = {
        mode: box.querySelector('#ng-mode').value,
        ai: box.querySelector('#ng-ai').value,
        seed: box.querySelector('#ng-seed').value.trim(),
        scenario: box.querySelector('#ng-scenario').value,
        armies: box.querySelector('#ng-armies').value,
        points: parseInt(box.querySelector('#ng-points').value, 10) || 100,
        max_year: box.querySelector('#ng-year').value,
        historical: box.querySelector('#ng-hist').checked,
      };
      this.closeModal();
      if (opts.armies === 'custom') {
        const humans = opts.mode === 'hotseat' ? ['player1', 'player2'] : opts.mode === 'vs_ai' ? ['player1'] : [];
        for (const p of humans) {
          const picked = await this.showArmyBuilder(p, opts);
          if (!picked) return;           // cancelled
          opts[p === 'player1' ? 'p1_units' : 'p2_units'] = picked;
        }
      }
      this.h.onNewGame(opts);
    };
  }

  // ------------------------------------------------------------- army builder
  async showArmyBuilder(player, opts) {
    let units = [];
    try { units = await this.h.listUnits(); } catch (e) { this.toast('Could not load unit list', true); return null; }
    const side = player === 'player1' ? 'allies' : 'axis';
    const maxYear = opts.max_year ? parseInt(opts.max_year, 10) : 9999;
    const pool = units.filter(u => u.side === side && (u.year || 0) <= maxYear && u.unit_type !== 'Aircraft');
    const nations = [...new Set(pool.map(u => u.nation))].sort();
    const budget = opts.points || 100;
    const picked = [];
    return new Promise(resolve => {
      const box = this._modal(`<h2>${this.playerName(player)} — build your army</h2>
        <div class="row"><label>Nation</label><select id="ab-nation"><option value="">all ${side}</option>${nations.map(n => `<option>${esc(n)}</option>`).join('')}</select>
          <label style="width:auto">Type</label><select id="ab-type"><option value="">all</option><option>Soldier</option><option>Vehicle</option></select>
          <input id="ab-search" placeholder="search…" style="flex:1"></div>
        <div style="display:flex;gap:10px;height:360px">
          <div id="ab-list" style="flex:1;overflow:auto;border:1px solid var(--line);border-radius:6px"></div>
          <div style="width:260px;display:flex;flex-direction:column">
            <div id="ab-total" style="font-weight:700;margin-bottom:6px"></div>
            <div id="ab-picked" style="flex:1;overflow:auto;border:1px solid var(--line);border-radius:6px"></div>
          </div></div>
        <div class="actions"><button class="btn" id="ab-cancel">Cancel</button><button class="btn primary" id="ab-done">Done</button></div>`);
      box.className = 'modal-box';
      box.style.maxWidth = '900px';
      const list = box.querySelector('#ab-list'), pickedEl = box.querySelector('#ab-picked'), total = box.querySelector('#ab-total');
      const cost = () => picked.reduce((s, u) => s + (u.cost || 0), 0);
      const renderPicked = () => {
        const c = cost();
        total.textContent = `${c} / ${budget} points · ${picked.length} units`;
        total.style.color = c > budget ? 'var(--bad)' : 'var(--text)';
        pickedEl.innerHTML = picked.map((u, i) => `<div class="unit-item" data-i="${i}"><span class="nm">${esc(u.name)}</span><span class="st">${u.cost}</span><span class="tag">✕</span></div>`).join('') || '<div style="padding:8px;color:var(--muted)">Click units on the left to add them.</div>';
        pickedEl.querySelectorAll('.unit-item').forEach(el => el.onclick = () => { picked.splice(+el.dataset.i, 1); renderPicked(); });
        box.querySelector('#ab-done').disabled = picked.length === 0 || c > budget;
      };
      const renderList = () => {
        const n = box.querySelector('#ab-nation').value, t = box.querySelector('#ab-type').value, q = box.querySelector('#ab-search').value.toLowerCase();
        const rows = pool.filter(u => (!n || u.nation === n) && (!t || (u.unit_type || '').startsWith(t)) && (!q || u.name.toLowerCase().includes(q)));
        list.innerHTML = rows.map(u => `<div class="unit-item" title="${esc((u.abilities || []).join(', '))}" data-name="${esc(u.name)}">
          <span class="nm">${esc(u.name)}</span><span class="st">${esc(u.nation)} · ${u.year} · ${esc(u.unit_type)} · def ${u.defense_front}${u.defense_rear !== u.defense_front ? '/' + u.defense_rear : ''} · spd ${u.speed}</span><span class="tag">${u.cost}</span></div>`).join('');
        list.querySelectorAll('.unit-item').forEach(el => el.onclick = () => { picked.push(rows.find(u => u.name === el.dataset.name)); renderPicked(); });
      };
      ['#ab-nation', '#ab-type'].forEach(sel => box.querySelector(sel).onchange = renderList);
      box.querySelector('#ab-search').oninput = renderList;
      renderList(); renderPicked();
      box.querySelector('#ab-cancel').onclick = () => { this.closeModal(); resolve(null); };
      box.querySelector('#ab-done').onclick = () => { this.closeModal(); resolve(picked.map(u => u.name)); };
    });
  }
}
