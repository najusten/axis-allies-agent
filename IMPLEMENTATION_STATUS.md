# Axis & Allies Miniatures — Implementation Status

**Last updated:** 2026-09-11

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
| `game_runner.py` | AI-vs-AI runner; `RandomAgent`, `AggressiveRandomAgent`, `GreedyAgent` |
| `mcts.py` | `MCTSAgent` (UCB1) — not yet usable in play, see Known Issues |
| `visualization.py`, `game_visualizer.py` | Legacy HTML/SVG page generator (used by runner `--visualize`) |
| `server.py` | Flask server: Human vs AI in the browser (port 8080) |

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

### Turn structure
- ✅ Initiative: 2d6 + commander + recon, Organization reroll, tie-break (`initiative.py`)
- ✅ Movement (first, second) · Assault (first, second) · Casualty — both `game_runner.py` and `server.py`
- ✅ Flight / Airstrike phases for aircraft — `game_runner.py` only
- ✅ Objective control victory at turn ≥ 7, turn-limit points tiebreak — `game_runner.py` only
- ⚠️ `server.py` has its own phase sequencer: no flight/airstrike, no objective/turn-limit victory (elimination only), AI cannot voluntarily pass
- ⚠️ `game_runner.py` resets pending hits per player assault phase; `server.py` never does — one of them is wrong (hits should persist until casualty phase)

### Abilities
- ✅ 212 abilities loaded; passive modifiers (attack, defense, movement, LOS) applied automatically
- ✅ Manual activation via `UseAbilityAction` for many (Smoke Screen, Demolitions, change facing, …), exposed in the server UI
- ❌ Activation missing for: Aggression (move-then-attack), Gliderborne / Partisan (special deployment), Vanguard (pre-game phase; runner only), AVRE (explicit obstacle destruction), Improved Indirect Fire (US commander target designation)

### AI
- ✅ `RandomAgent`, `AggressiveRandomAgent`, `GreedyAgent` (one-ply via `GameStateEvaluator`)
- ⚠️ `MCTSAgent` exists but is not playable: tree ignores phase transitions, simulations mutate executor-held state, branching factor (one `MoveAction` per reachable hex) is too high — see roadmap

### Server / UI (`server.py`)
- ✅ Human (player1) vs AI (player2): select unit → click highlighted hex to move/attack/board/dismount; ability panel; facing picker; undo/redo (blocked after any dice roll); zoom/pan; stat cards; event log
- ❌ Hot-seat human vs human; AI choice in-game; MCTS option
- ⚠️ Rendering is full-page HTML regenerated in Python and reloaded after every action (`location.reload()`); JS lives in Python string literals — being replaced by a JSON API + static frontend (roadmap Phase 3)

---

## Known bugs

1. `action_executor.py:415,441` — `getattr(hex, 'has_road', False)` reads a *method* → always truthy → forest bog and Weak Suspension rolls never trigger.
2. Three divergent cover-terrain lists (see Combat above).
3. `'stream'` / `'impassable'` not in `Board.TERRAIN_TYPES` → `set_terrain` raises for them.
4. `server.py:572` reads `pending.get('total')` but `casualty.get_pending_hits_summary` returns `{disrupted, damaged, destroyed}` → pending-hit badge always 0.
5. `server.py` drops `parameters={'new_facing': N}` from change-facing ability actions → all facing buttons send no direction.
6. `game_runner.py:107-109` — `AggressiveRandomAgent` ability filter compares against `type`, always False (no-op).
7. `game_runner.py:670` — Vanguard phase calls `action_executor.execute` (does not exist; should be `execute_action`).
8. `GameState.clone()` copies `UnitState` from a hand-maintained field list; new fields are silently dropped.
9. Dice use the global `random` module (`DiceSystem`, `initiative.py:81,91`, `casualty.py:234`); no injectable RNG → games are not replayable and tests cannot script dice.
10. `CasualtySystem._pending_hits` and `DefensiveFireSystem._units_fired_this_phase` live on the executor, not on `GameState` → any lookahead that executes actions on a cloned state corrupts the live game.

---

## Tests

- `test_game_engine.py` — smoke tests (action generation/execution, cloning, turn progression); print-based, no asserts, unseeded
- `test_abilities.py` — 25 ability/obstacle/transport/combat tests; print-based, unseeded
- Run with `python3 test_abilities.py` / `python3 test_game_engine.py` (require the CSVs in cwd; CSVs are gitignored)
- No pytest setup yet; no deterministic scenario tests

---

## Roadmap

See `.claude/plans/` (session plan) for detail. Summary:

- **Phase 1 — Engine foundations**: injectable RNG + `ScriptedDice`; move pending-hits / defensive-fire state into `GameState`; one `TurnController` shared by server and runner (adds flight/airstrike/objective victory to the server); structured `events` on `ActionResult`; `GameState.to_dict()`; YAML scenario loader (`scenario.py`). Fix bugs 1–10.
- **Phase 2 — Rules verification**: pytest + `scenarios/*.yaml` regression suite (one per mechanic, plus forum/FAQ rulings); `simulate.py` AI-vs-AI fuzzer with invariant checks.
- **Phase 3 — Frontend**: `GET /api/state`, `POST /api/action` → events; static `static/` SPA (vanilla JS + SVG, no build step) with a swappable renderer; hot-seat and vs-AI modes; rectangular board.
- **Phase 4 — Agents**: fast `HeuristicAgent` with per-unit candidate actions; fix `MCTSAgent` to use `TurnController` and candidate actions; tournament benchmarking.
- **Phase 5 — Visuals**: decide 2D art vs 3D after 3 & 4; only the renderer module changes.

Deferred: script sweep (remove unneeded modules once gameplay is complete), unit-card privacy in hot-seat, path-aware movement, remaining ability activations.
