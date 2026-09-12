// DOM panels around the board: top bar, sidebar lists, unit card, log,
// ability panel, modals (new game, hot-seat handoff, game over), toasts.
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
        if (u.has_moved && u.has_attacked) tags.push('<span class="tag done">done</span>');
        else if (u.has_moved) tags.push('<span class="tag done">moved</span>');
        else if (u.has_attacked) tags.push('<span class="tag done">fired</span>');
        li.innerHTML = `<span class="nm">${esc(u.card.name)}</span>${tags.join('')}<span class="st">${u.position[0]},${u.position[1]}</span>`;
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
      return `<div class="${cls}">${esc(l)}</div>`;
    }).join('');
    log.scrollTop = log.scrollHeight;
  }

  // ------------------------------------------------------------- overlays
  toast(msg, isErr = false, ms = 2500) {
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
        <option value="aggressive">Aggressive (random-ish)</option>
        <option value="greedy">Greedy (1-ply lookahead)</option>
        <option value="random">Random</option></select></div>
      <div class="row"><label>Seed</label><input id="ng-seed" placeholder="random" inputmode="numeric"></div>
      <div class="row"><label>Scenario</label><select id="ng-scenario"><option value="">Showcase armies (default)</option>
        ${scenarios.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('')}</select></div>
      <div class="actions"><button class="btn" id="ng-cancel">Cancel</button><button class="btn primary" id="ng-start">Start</button></div>`);
    box.className = 'modal-box';
    box.querySelector('#ng-cancel').onclick = () => this.closeModal();
    box.querySelector('#ng-start').onclick = () => {
      const opts = {
        mode: box.querySelector('#ng-mode').value,
        ai: box.querySelector('#ng-ai').value,
        seed: box.querySelector('#ng-seed').value.trim(),
        scenario: box.querySelector('#ng-scenario').value,
      };
      this.closeModal();
      this.h.onNewGame(opts);
    };
  }
}
