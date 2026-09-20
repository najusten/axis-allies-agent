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
| `mcts.py` | `MCTSAgent` — flat Monte Carlo over the heuristic's top-K candidates: each is played on a simulated `TurnController` (cloned state, heuristic policy for both sides, separate dice RNG) for a few phases and valued by material + objective; UCB1 spends the time budget. Needs `agent.attach(controller)`. |
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
- ✅ `stream` / `impassable` terrain types exist (no rules for streams yet)
- ❌ Half-hexes (impassable map-edge hexes)
- ❌ Hex-side terrain: streams, hedges, bluffs (only edge *obstacles* exist)
- ✅ Rectangular board in even-q offset coordinates (`Board.offset_to_axial`), addressed by axial everywhere

### Movement
- ✅ BFS reachable hexes with terrain costs; vehicles pay double in forest/hill (`movement.py`)
- ✅ Stacking: 3 units per hex, max 1 vehicle (`GameState.can_stack_at`)
- ✅ Vehicle facing set after move; front/rear arcs (`facing.py`)
- ✅ Movement rolls: forest bog, Weak Suspension on hills, barbed wire, destroyed bridge, tank obstacles (`action_executor._execute_move`)
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
- ✅ One cover-terrain constant (`Board.COVER_TERRAIN`)
- ⚠️ `combat.py` `CombatSystem.resolve_attack` is dead code; live path is `action_executor._resolve_attack_full`

### Turn structure (`turn_controller.py`, shared by server and runner)
- ✅ Initiative: 2d6 + commander + recon, Organization reroll, tie-break (`initiative.py`)
- ✅ Vanguard pre-game phase (speed 4) · Movement ×2 · Flight ×2 (if aircraft) · Assault ×2 · Airstrike ×2 (if aircraft) · Casualty
- ✅ Pending hits persist across both assault phases until the casualty phase
- ✅ Elimination, objective control at turn ≥ 7, turn-limit points tiebreak

### Abilities
- ✅ 212 abilities loaded; passive modifiers (attack, defense, movement, LOS) applied automatically
- ✅ Manual activation via `UseAbilityAction` for many (Smoke Screen, Demolitions, change facing, …), exposed in the server UI
- ✅ Special deployment through the UI: Partisan (any edge hex), Gliderborne (anywhere outside the enemy zone), Paratrooper (movement phase, not adjacent to an enemy, can't move that phase), Hero (movement phase, with a friendly Soldier of its nation). The AI uses them too.
- ✅ Antiair / Ace reaction shots when an enemy Aircraft is placed (adjacent / within 4); a human defender is asked, as for defensive fire. Flamethrower instant kill (3+ sixes at short range). Covering Fire, Suppressive Fire, Multiturreted (one front-arc + one non-front-arc target) verified by scenarios.
- ✅ Distinct token silhouettes per unit class (soldier, MG, artillery/mortar, commander ★, sniper, anti-tank, tank, tank destroyer, assault gun, armoured car, half-track, transport, SP artillery, aircraft) with a short name label under each token — still the plain 2D renderer; real art remains Phase 5.
- ✅ Per-unit undo: the unit card offers "Undo this unit's move" — the session rewinds to before that unit's action and replays the other units' actions (only while no dice have been rolled, like global undo).
- ✅ AI assist: the 💡 Suggest button highlights the heuristic's best action for the human (pulsing yellow hex); clicking it performs that action, ignoring it costs nothing.
- ✅ Aggression X in the UI: assault-phase hexes within X are shown with a red ring (⚔+) and keep the attack; plain green hexes are the full-speed move that gives it up.
- ✅ AVRE (crosses/destroys obstacles without rolls) and Improved Indirect Fire (US commander within 4 as spotter) are passive and scenario-tested; the latter never worked before (wrong nationality attribute).
- ✅ Vanguard pre-game phase (speed-4 move before turn 1) runs through the controller and UI.

### AI
- ✅ `agents.HeuristicAgent` — **server default**. Static scoring of every legal action: attacks by expected damage (binomial over dice, cover roll, target value, focus fire), moves by objective pressure (phased by turn), cover, expected damage dealt/taken from the destination, rear exposure, route risk (forest/stream/hedge rolls and defensive-fire exposure along the path), spreading. Chooses to go second on initiative until turn 6; deploys with a back/front/cover policy. ~10 ms/decision. Beats AggressiveRandom 80–94%, Greedy 100%.
- ✅ `agents.LookaheadAgent` — heuristic top-K pruning + one-ply simulation scored by `GameStateEvaluator`. Currently slightly *weaker* than pure heuristic (the evaluator is the weak link).
- ✅ Legacy: `RandomAgent`, `AggressiveRandomAgent`, `GreedyAgent` (`game_runner.py`)
- ✅ `MCTSAgent` (`mcts.py`) — heuristic top-K candidates + Monte Carlo rollouts through a simulated `TurnController` (3 phases, ~70 ms each after the LOS memo), UCB1 over candidates, ~1–1.5 s per decision. Selectable in New Game ("Monte Carlo"). Benchmark 2026-09-19: `python3 simulate.py -n 6 --deploy --p1 mcts --p2 heuristic --mcts-time 1.0` → MCTS 6–0 as player 1 (~1.04 s/decision, 2–8k rollouts per game); as player 2 at 0.7 s it went 2–2 (`--p1 heuristic --p2 mcts --seed 600`). Small samples; clearly at least as strong as the heuristic, not yet dominant.
- Benchmark: `python3 simulate.py -n 16 --p1 heuristic --p2 aggressive --seed 2000`
- `MovementSystem.has_line_of_sight` is memoised on the board's terrain signature (`Board.terrain_signature()`, survives cloning): heuristic play went from ~25 to ~10 ms/action.

### Server / UI (`server.py` + `static/`)
- ✅ JSON API; frontend updates in place (no reload); moves animate; dice popups for attacks, cover rolls, movement rolls, defensive fire; LOS line; casualty fades; initiative banner; click to skip
- ✅ Modes: Human vs AI, hot-seat Human vs Human (handoff screen, opponent's card hidden), AI vs AI (step)
- ✅ New Game dialog: mode, AI type, seed, or load a scenario file
- ✅ Select unit → highlighted hexes (move/attack/board/dismount), ability panel with per-target buttons, facing picker, undo/redo (blocked after any dice roll), zoom (fit/±/ctrl-wheel) and drag-pan, stat cards with ability descriptions, event log, coords toggle
- ✅ Rectangular board (even-q offset), landscape default 18×12
- ✅ AI choice in New Game: Heuristic (default), Lookahead, Aggressive, Greedy, Random
- ❌ Path-aware movement (choose route), aircraft placement UI, deployment phase UI

---

## Rules source
The official **Advanced Rulebook** is in `document.pdf` (local only, gitignored). Everything below marked "rulebook" was checked against it on 2026-09-12. The engine follows these original rules, not the 2008 Expanded Rules.

## Known issues / open rules questions

- Special attacks (rockets, hull cannons, remote control, bombs) roll their own dice outside `_resolve_attack_full`: no cover roll, no facing, no rerolls. They now at least record pending counters correctly. Should be unified.
- Bluffs/cliffs (fringe terrain), shell holes, half-hexes, and "road through forest" (roads are their own terrain type here) are not modelled.
- Defensive fire: human defenders decide per shot (hex or hold); AI defenders use the automatic best-hex choice.

### Implemented straight from the rulebook (Sep 2026)
- Sequence of play; assault phase = each unit moves (as in the movement phase) **or** attacks; a unit may move in both phases.
- How to Win: objective control at the end of turn 7 and every turn after; from turn 10 the higher point total; ties keep playing.
- Movement rolls (4+) to enter forest (Vehicles); failure stops the unit in the hex it was leaving, facing the hex it tried to enter; a failed roll never provokes defensive fire. Vehicles pay 2 per forest/hill hex; can't enter marsh/water. Road bonus: first road hex per phase free for Vehicles. Vehicles may change facing as a zero-hex move; disrupted units can't change facing.
- Stacking: 2 friendly units per hex, 1 Vehicle; can't be forced to stop overstacked (retrace).
- LOS: towns, hills, forests block; attacker's and target's hexes never block; edge-graze rules.
- Facing: front arc = 3 front hex sides; side and same-hex attacks use rear defense.
- Cover: forest/hill/town for all, marsh for Soldiers; soldiers 4+, vehicles 5+, −1 in the same hex; success ⇒ one face-down Disrupted counter (never a second); cover save **negates** defensive fire.
- Counters: 1st = Disrupted, 2nd = Damaged (Vehicle) / Destroyed (Soldier, Aircraft), 3rd = Destroyed; placed face-down, applied in the casualty phase; a damaged Vehicle receiving another Damaged counter is destroyed.
- Disrupted: −1 attack die, −1 defense, can't move, no defensive fire. Damaged: −1 attack die, −1 defense, −1 speed; both at once = penalties applied once.
- Defensive fire: disrupt-only, immediate, once per unit per phase; Soldiers don't provoke it from Vehicles.
- Historical Army Limits and year restriction (New Game options); army builder with points budget.
- Setup: coin flip, winner chooses deployment order, each side deploys within five hexes of its edge.
- Initiative: winner chooses to go first or second.
- Hex-side terrain: streams (roll 4+, roads bridge them), hedges (roll 5+, block LOS, cover when shot through).
- Speed-1 Vehicle minimum movement; Heavy Armor ignores the first Damaged counter; a damaged Vehicle receiving another Damaged counter is destroyed.
- Defensive fire is optional (hold-fire order) and the defender fires into the better of the two hexes.
- Units may move through/into enemy hexes (stacking per army at the destination).
- Aircraft: placement in the flight phase and airstrike attacks in the UI; Aircraft don't count toward stacking (one per hex).
- A destroyed transport destroys its passenger.
- Only Aggression X grants move-then-attack in the assault phase (a keyword rule had extended it to ~20 other abilities).
- Ability scenarios (`scenarios/abilities/`): Close Assault, Hand to Hand, Superior Armor, No Turret, Inaccurate, Crack Shot, Limited/Extended Range, Open Back, Sideskirts, Tall Silhouette, Shrapnel, Strike and Fade, Large, commanders (initiative bonus, Tally-Ho!, Command Leadership, Coordinated Fire), Blast, Double Shot defensive fire, transports, Amphibious, Excellent Suspension, Robust, Bombardment, Paratrooper/Hero/Partisan/Gliderborne deployment, Antiair/Ace reactions, Flamethrower, Covering Fire, Suppressive Fire, Multiturreted, Smoke Screen.
- Route preview on hover (dashed path + dice markers where rolls happen); routes prefer fewer rolls at equal cost. Players can pick their own route: shift-click green hexes as waypoints, then click the destination; the server validates the route with `MovementSystem.path_cost()` (same rules as reachability) and the executor walks exactly that path.
- Spotter Q&A ([aamcardbase](http://www.aamcardbase.com/special_abilities_aam.aspx)): a Spotter that moves in the assault phase doesn't count.
- Artillery assault-only movement, half-hexes, hex-side terrain: not implemented.
- Log/coordinates are axial (q, r); the UI's coords toggle shows the same. Fine for debugging, may want offset (col,row) for players.

### Fixed in the Sep 2026 rebuild
(2026-09-20) **Vehicles with a subtype ("Vehicle Tank" — i.e. almost all of them) were destroyed by two hits instead of damaged, made cover rolls at 4+ instead of 5+, were skipped by area attacks (rocket salvo) and got no starting facing in fixed setups** (`unit_type == 'Vehicle'` exact matches in casualty/dice/executor/setup) · dismounting could overstack a hex · a unit disrupted by defensive fire / Ace reactions / scenario setup never recovered — recovery only looked at counters flipped in a casualty phase; now every face-up Disrupted counter recovers at the next casualty phase (sticky ones excepted) · two Aircraft could share a hex · spotter bonus for Aircraft crashed on use ·
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
- 🔧 **Phase 4 — Agents**: `HeuristicAgent` + `LookaheadAgent` done and benchmarked; MCTS with heuristic rollouts via `TurnController` still open.
- **Phase 5 — Visuals**: decide 2D art vs 3D; only `static/js/renderer.js` changes.

Deferred: script sweep (remove unneeded modules once gameplay is complete), unit-card privacy in hot-seat, path-aware movement, remaining ability activations.
