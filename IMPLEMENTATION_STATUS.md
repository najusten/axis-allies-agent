# Axis & Allies Miniatures — Implementation Status

**Last updated:** 2026-09-11 (evening — after Phases 0–3 of the rebuild)

Tracks what the engine implements, what it doesn't, known bugs, and the roadmap.
Rules scope is Axis & Allies Miniatures only.

Legend: ✅ implemented · ⚠️ partial / caveat · ❌ missing

---

## Architecture

| Module | Role |
|---|---|
| `board.py` | Hex grid (axial coords, flat-top), terrain, edge obstacles |
| `units.py`, `game_setup.py` | Unit data from CSV, army building, board/unit placement |
| `game_state.py` | `GameState`, `UnitState` (70+ per-unit flags), phases, clone, victory |
| `movement.py` | Reachable hexes, terrain costs, line of sight |
| `facing.py` | Vehicle facing, front/rear arcs |
| `dice.py` | `DiceSystem`: attack rolls, cover saves, movement rolls, damage resolution |
| `abilities.py` | `AbilitySystem`: 212 abilities parsed from CSV; modifiers and checks |
| `action.py` | Action dataclasses (Move, Attack, UseAbility, Board/Dismount, Deploy, PlaceAircraft, Pass, EndPhase) |
| `action_generator.py` | Legal action enumeration per phase |
| `action_executor.py` | Action execution; attack resolution; movement rolls; ability effects |
| `defensive_fire.py` | Defensive fire opportunities and resolution during moves |
| `casualty.py` | Pending hit counters, casualty-phase resolution |
| `initiative.py` | 2d6 initiative with commander/recon/organization bonuses |
| `transport.py` | Transport capacity/loading rules |
| `evaluation.py` | `GameStateEvaluator` heuristic (material, position, status, threat, objective) |
| `turn_controller.py` | **Sequence of play** (initiative → movement → flight → assault → airstrike → casualty → victory), human or AI per player, structured event log, snapshot/restore |
| `scenario.py` | YAML scenario loader + `ScriptedDice`; `build_action()` shared with the server |
| `simulate.py` | AI-vs-AI fuzzer/benchmark with invariant checks |
| `game_runner.py` | AI-vs-AI runner (thin wrapper over TurnController); `RandomAgent`, `AggressiveRandomAgent`, `GreedyAgent` |
| `mcts.py` | `MCTSAgent` (UCB1) — not yet usable in play, see Known Issues |
| `visualization.py`, `game_visualizer.py` | Legacy HTML/SVG page generator (only `game_runner --visualize`; candidate for removal) |
| `server.py` | Flask JSON API (`/api/state`, `/api/action`, `/api/new_game`, …) serving `static/` |
| `static/` | Frontend: `js/renderer.js` (swappable SVG board), `js/ui.js`, `js/app.js`, `js/hex.js`, `js/api.js` |

Subsystem construction pattern (all systems need `AbilitySystem` + `MovementSystem`):

```python
ability_system = AbilitySystem('Axis and Allies Unit Data for Analysis - Special_Abilities.csv')
movement_system = MovementSystem(ability_system)
action_generator = ActionGenerator(movement_system, None, ability_system)
action_executor = ActionExecutor(movement_system, None, ability_system)
initiative_system = InitiativeSystem(ability_system, movement_system)
```

---

## Rules coverage

### Board & terrain
- ✅ Axial hex grid, neighbors, distance (`board.py`)
- ✅ Terrain: open, forest, building, water, road, hill, town, marsh, ruins (`Board.TERRAIN_TYPES`)
- ✅ Edge obstacles: barbed wire, destroyed bridge (`Board.add_edge_obstacle`)
- ✅ Objective hex with control check (`GameState.check_objective_control`)
- ⚠️ `'stream'` and `'impassable'` are referenced by movement/setup code but are not valid `TERRAIN_TYPES`
- ❌ Half-hexes (impassable map-edge hexes)
- ❌ Hex-side terrain: streams, hedges, bluffs (only edge *obstacles* exist)
- ⚠️ Board is a rectangle in axial (q, r) → renders as a parallelogram

### Movement
- ✅ BFS reachable hexes with terrain costs; vehicles pay double in forest/hill (`movement.py`)
- ✅ Stacking: 3 units per hex, max 1 vehicle (`GameState.can_stack_at`)
- ✅ Vehicle facing set after move; front/rear arcs (`facing.py`)
- ✅ Movement rolls: forest bog, Weak Suspension on hills, barbed wire, destroyed bridge, tank obstacles (`action_executor._execute_move`) — see bug #1
- ✅ Assault-phase relocation and Strike and Fade
- ✅ Transports: board, move, dismount; capacity; fighting platform (`transport.py`, executor)
- ✅ High Gear road movement
- ❌ Artillery: speed 0 in movement phase / speed 2 in assault phase, move-or-fire

### Line of sight
- ✅ Geometric centre-to-centre LOS, interior blocking, edge grazes (`movement.has_line_of_sight`)
- ✅ Forest, building, hill block; smoke blocks
- ✅ Superior Optics ignores one hill hex (`abilities.check_los_blocked`)
- ✅ Spotters / Indirect Fire (`action_generator`, `action_executor._check_spotter_bonus`)

### Combat
- ✅ Range bands short/medium/long; anti-soldier vs anti-vehicle values
- ✅ Roll N dice, hit on ≤ threshold; hits vs defense (`dice.roll_attack`, `calculate_hits`)
- ✅ Disrupted / Damaged / Destroyed state machine for soldiers and vehicles (`dice.resolve_*_damage`)
- ✅ Cover saves: soldiers 4+, vehicles 5+, −1 if attacker in same hex (`dice.roll_cover_save`)
- ✅ Rear-armor when attacked from rear arc
- ✅ Close Assault dice override
- ✅ Blast (hits every unit in target hex)
- ✅ Defensive fire when moving adjacent: disrupt-only, can stop movement, Double Shot (`defensive_fire.py`)
- ✅ Simultaneous resolution: hits recorded as face-down counters, applied in casualty phase (`casualty.py`)
- ✅ Special attacks: rockets, bombs, salvo, hull cannons, flamethrower/fire hazards, rerolls (Guard Crew, Lead the Way, …)
- ⚠️ Three separate cover-terrain lists (`action_executor.py:58`, `defensive_fire.py:645`, `combat.py:141`) — should be one constant
- ⚠️ `combat.py` `CombatSystem.resolve_attack` is dead code; live path is `action_executor._resolve_attack_full`

### Turn structure (`turn_controller.py`, shared by server and runner)
- ✅ Initiative: 2d6 + commander + recon, Organization reroll, tie-break (`initiative.py`)
- ✅ Vanguard pre-game phase (speed 4) · Movement ×2 · Flight ×2 (if aircraft) · Assault ×2 · Airstrike ×2 (if aircraft) · Casualty
- ✅ Pending hits persist across both assault phases until the casualty phase
- ✅ Elimination, objective control at turn ≥ 7, turn-limit points tiebreak

### Abilities
- ✅ 212 abilities loaded; passive modifiers (attack, defense, movement, LOS) applied automatically
- ✅ Manual activation via `UseAbilityAction` for many (Smoke Screen, Demolitions, change facing, …), exposed in the server UI
- ❌ Activation missing for: Aggression (move-then-attack), Gliderborne / Partisan (special deployment), Vanguard (pre-game phase; runner only), AVRE (explicit obstacle destruction), Improved Indirect Fire (US commander target designation)

### AI
- ✅ `RandomAgent`, `AggressiveRandomAgent`, `GreedyAgent` (one-ply via `GameStateEvaluator`)
- ⚠️ `MCTSAgent` exists but is not playable: tree ignores phase transitions, simulations mutate executor-held state, branching factor (one `MoveAction` per reachable hex) is too high — see roadmap

### Server / UI (`server.py` + `static/`)
- ✅ JSON API; frontend updates in place (no reload); moves animate; dice popups for attacks, cover rolls, movement rolls, defensive fire; LOS line; casualty fades; initiative banner; click to skip
- ✅ Modes: Human vs AI, hot-seat Human vs Human (handoff screen, opponent's card hidden), AI vs AI (step)
- ✅ New Game dialog: mode, AI type, seed, or load a scenario file
- ✅ Select unit → highlighted hexes (move/attack/board/dismount), ability panel with per-target buttons, facing picker, undo/redo (blocked after any dice roll), zoom (fit/±/ctrl-wheel) and drag-pan, stat cards with ability descriptions, event log, coords toggle
- ✅ Rectangular board (even-q offset), landscape default 18×12
- ❌ MCTS / heuristic AI selectable (Phase 4)
- ❌ Path-aware movement (choose route), aircraft placement UI, deployment phase UI

---

## Known issues / open rules questions

- Special attacks (rockets, hull cannons, remote control, bombs) roll their own dice outside `_resolve_attack_full`: no cover roll, no facing, no rerolls. They now at least record pending counters correctly. Should be unified.
- `dice.resolve_soldier_damage`: a cover-saved hit on an *already disrupted* soldier does nothing. Rulebook says a second Disrupted result destroys — verify with a scenario.
- Cover terrain = forest, building, hill, town, ruins (marsh excluded) — verify ruins/marsh.
- Artillery assault-only movement, half-hexes, hex-side terrain: not implemented.
- Ability activation UI missing for Aggression, Gliderborne, Partisan, AVRE, Improved Indirect Fire.
- Log/coordinates are axial (q, r); the UI's coords toggle shows the same. Fine for debugging, may want offset (col,row) for players.

### Fixed in the Sep 2026 rebuild
forest bog / Weak Suspension rolls never triggered (`has_road` method read as attribute) · cover saves ignored in simultaneous combat (raw hits recorded as counters) · cover roll made before the attack roll · three divergent cover lists · High Gear moves rejected by the validator · six special attacks crashed on use (`attack_result` kwarg, `is_alive` assignment, tuple≠Hex) · once-per-game "instead of attack" abilities offered after attacking · Vanguard phase called a nonexistent method · `clone()` dropped ~15 UnitState fields · pending hits/defensive-fire tracking lived on the executor (lookahead corrupted the live game) · global-RNG dice · `AggressiveRandomAgent` no-op filter · missing `get_all_alive_units`.

---

## Tests

- `python3 -m pytest` — `tests/`: every `scenarios/*.yaml` (scripted dice, expectations), clone completeness, determinism, lookahead isolation
- `python3 scenario.py scenarios/x.yaml` — run one scenario and print each step
- `python3 simulate.py -n 30 --p1 aggressive --p2 greedy --seed 1` — fuzz + benchmark (invariants, crashes, generator/executor mismatches)
- Legacy: `python3 test_abilities.py` (25 pass), `python3 test_game_engine.py` (6/7 — fixture units out of range)
- Dev deps: `pip3 install -r requirements-dev.txt`

---

## Roadmap

See `.claude/plans/` (session plan) for detail. Summary:

- ✅ **Phase 1 — Engine foundations** (done): injectable RNG + `ScriptedDice`; game bookkeeping on `GameState`; `TurnController`; structured events; `to_dict()`; scenario loader.
- 🔧 **Phase 2 — Rules verification** (infrastructure done, scenarios ongoing): 8 scenarios so far; add one per mechanic and per forum/FAQ ruling.
- ✅ **Phase 3 — Frontend** (done, plain 2D): JSON API + static SVG frontend; hot-seat and vs-AI.
- **Phase 4 — Agents**: fast `HeuristicAgent` with per-unit candidate actions; fix `MCTSAgent` (phases via `TurnController`, candidate actions, open-loop chance handling); tournament benchmarking with `simulate.py`.
- **Phase 5 — Visuals**: decide 2D art vs 3D; only `static/js/renderer.js` changes.

Deferred: script sweep (remove unneeded modules once gameplay is complete), unit-card privacy in hot-seat, path-aware movement, remaining ability activations.
