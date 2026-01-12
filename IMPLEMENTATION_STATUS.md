# Axis & Allies Miniatures - Implementation Status

**Last Updated:** 2025-01-11 (Post-rulebook review)

This document tracks the implementation status of all game systems **based solely on Axis & Allies Miniatures rules**. No features from other games included.

---

## Legend
- ✅ **FULLY IMPLEMENTED** - Production ready, tested
- ⚠️ **SIMPLIFIED/PLACEHOLDER** - Working but missing features or using simplified logic
- ❌ **NOT IMPLEMENTED** - Needs to be built
- 🔧 **IN PROGRESS** - Currently being worked on

---

## Core Systems

### Board & Hex Grid
- ✅ Axial coordinate system
- ✅ Hex neighbor calculation
- ✅ Hex distance calculation
- ✅ Terrain types (open, forest, road, hill, building, water)
- ✅ Hex coordinate conversion
- ✅ Board initialization and management
- ❌ **Half-hexes** (impassable hexes on map edges)
- ❌ **Hex-side terrain** (streams, bluffs, hedges between hexes)
- ❌ **Fringe terrain** (terrain affecting entry but not interior)

### Movement System
- ✅ BFS pathfinding for reachable hexes
- ✅ Terrain-based movement costs
- ✅ Forest/Hill double-cost for vehicles
- ✅ Infantry terrain advantages
- ✅ Movement range calculation
- ✅ Obstacle/occupied hex detection
- ✅ Assault movement (can move again in assault phase)
- ❌ **Unit facing** (vehicles have facing direction)
- ❌ **Stacking** (multiple units per hex)
- ❌ **Movement roll terrain** (streams require roll to cross)
- ❌ **Defensive fire** (units can fire when enemies move adjacent)

### Line of Sight (LOS)
- ✅ Geometric line calculation (center to center)
- ✅ Interior hex blocking detection
- ✅ Edge graze detection
- ✅ Adjacent hex shared edge handling
- ✅ Forest/building LOS blocking
- ✅ Ability-based LOS modifiers
- ❌ **Hill LOS blocking** (hills block LOS like forests)
- ❌ **Spotters** (units that help indirect fire)

### Combat System
- ✅ Attack value calculation by range (short/medium/long)
- ✅ Range categories (0-1, 2-4, 5-8 hexes)
- ✅ Personnel vs Vehicle attack differentiation
- ✅ Basic hit resolution
- ✅ Damage application
- ✅ Unit destruction
- ⚠️ **Dice rolling (SIMPLIFIED)** - Using `random.randint(1,6)` instead of proper A&A dice mechanics
  - ❌ Roll attack value or less to hit (e.g., attack 5 hits on 1-5)
  - ❌ Multiple dice rolling (tanks can roll 11+ dice)
- ⚠️ **Armor facing (NOT IMPLEMENTED)** - Currently always uses `defense_front`
  - ❌ Unit facing direction not tracked
  - ❌ Front arc vs rear arc (180° each)
  - ❌ `defense_rear` not used (rear armor is weaker)
- ❌ **Cover saving throws** (4+ for infantry, 5+ for vehicles in cover)
- ❌ **Disrupted counters** (first hit disrupts, second destroys)
- ❌ **Damaged counters** (vehicles can be damaged instead of destroyed)
- ❌ **Simultaneous combat resolution** (hits applied after both sides fire)
- ❌ **Defensive fire** (units fire when enemies move adjacent)

### Abilities System
- ✅ Ability data loading from CSV
- ✅ Movement modifiers (speed bonus/penalty, terrain costs)
- ✅ Attack modifiers (range bonuses, damage bonuses)
- ✅ Defense modifiers (armor bonuses)
- ✅ Special movement abilities
- ✅ LOS modifiers
- ✅ Unit categorization (soldier, vehicle, obstacle)
- ⚠️ **Ability execution (PARTIALLY IMPLEMENTED)**
  - ✅ Ability tracking (used/not used per turn)
  - ⚠️ UseAbilityAction exists but effects not fully implemented
  - ❌ Most abilities just tracked, not executed with full effects
- ❌ **Commander abilities** (special bonuses from command units)

### Game State Management
- ✅ Complete game state representation
- ✅ Unit state tracking (position, health, moved/attacked flags)
- ✅ Turn/phase progression
- ✅ Player switching
- ✅ Action history tracking
- ✅ Game state cloning (for AI lookahead)
- ✅ Victory condition checking (elimination)
- ❌ **Unit facing/orientation** (needed for front/rear armor)
- ⚠️ **Status effects (FIELDS EXIST BUT UNUSED)** - Has `is_disrupted`, `is_damaged` but not used
- ❌ **Objective-based victory** (control objectives)
- ❌ **Point-based victory** (destroy more points of enemy)

---

## Game Phases (Axis & Allies Miniatures Actual Sequence)

### Currently Implemented
- ✅ Phase enumeration (movement, assault)
- ✅ Phase progression
- ✅ Turn counter
- ✅ Active player switching

### Actual A&A Miniatures Sequence of Play
**Per the rulebook, each turn consists of:**

A. **Initiative Phase** (both players)
   - ❌ Roll 2d6, high roller chooses who goes first
   - ❌ Commander units add bonus to initiative

B. **First Player's Movement Phase**
   - ✅ Move units up to their speed
   - ❌ Defensive fire when moving adjacent to enemies

C. **Second Player's Movement Phase**
   - ✅ Move units up to their speed
   - ❌ Defensive fire when moving adjacent to enemies

D. **First Player's Flight Phase** (if aircraft present)
   - ❌ Place aircraft anywhere on board
   - ❌ Aircraft with disruption can't be placed

E. **Second Player's Flight Phase**
   - ❌ Place aircraft anywhere on board

F. **First Player's Airstrike Phase**
   - ❌ Aircraft attack

G. **Second Player's Airstrike Phase**
   - ❌ Aircraft attack

H. **First Player's Assault Phase**
   - ⚠️ Units can attack OR move again (implemented but simplified)
   - ❌ Artillery moves at speed 2 only in assault phase

I. **Second Player's Assault Phase**
   - ⚠️ Units can attack OR move again

J. **Casualty Phase** (both players)
   - ❌ Apply all damage simultaneously
   - ❌ Flip disrupted counters face-down
   - ❌ Remove destroyed units

K. **End of Turn Phase**
   - ❌ Remove aircraft from board
   - ✅ Prepare for next turn

---

## Missing Core Features (Priority Order)

### CRITICAL (Needed for accurate gameplay)

1. ❌ **Proper Dice Rolling System**
   - Roll attack value or less to hit (attack 5 = hit on 1,2,3,4,5)
   - Defender rolls defense value or less for cover save
   - Support for rolling many dice (11+ for some units)
   - **Priority: HIGHEST** - Combat doesn't work correctly without this

2. ❌ **Unit Facing/Orientation**
   - Vehicles face one of 6 hex directions
   - Front arc = 180° (3 hex faces)
   - Rear arc = 180° (3 hex faces)
   - Use `defense_front` for front arc attacks
   - Use `defense_rear` for rear arc attacks
   - **Priority: HIGHEST** - Critical game mechanic

3. ❌ **Disrupted/Damaged Status Effects**
   - First hit on infantry = disrupted
   - Second hit on disrupted infantry = destroyed
   - First hit on vehicle = disrupted OR damaged (cover save determines)
   - Disrupted units have penalties
   - Face-down disrupted counters flip face-up at end of turn
   - **Priority: HIGH** - Units don't die in one hit

4. ❌ **Cover System**
   - Cover terrain: forests, hills, buildings, towns
   - Infantry in cover: save on 4+ (roll 4,5,6 = reduced to disrupted)
   - Vehicles in cover: save on 5+ (roll 5,6 = reduced to disrupted/damaged)
   - Defensive fire only disrupts (can't destroy)
   - **Priority: HIGH** - Major survival mechanic

5. ❌ **Defensive Fire**
   - Units fire when enemy moves into adjacent hex
   - Defensive fire can only disrupt, not destroy
   - Cover saves apply
   - Movement can be stopped by defensive fire
   - **Priority: HIGH** - Core tactical mechanic

### HIGH PRIORITY (Needed for complete game)

6. ❌ **Initiative System**
   - Both players roll 2d6
   - Commander units add initiative bonus
   - Winner chooses who goes first this turn
   - **Priority: MEDIUM** - Affects turn order

7. ❌ **Stacking**
   - Multiple units can occupy same hex
   - All units in hex can be attacked
   - **Priority: MEDIUM** - Tactical positioning

8. ❌ **Simultaneous Combat Resolution**
   - All attacks resolved simultaneously
   - Units can kill each other
   - Apply damage in Casualty Phase
   - **Priority: MEDIUM** - Fair combat

9. ❌ **Deployment Phase**
   - Deploy within 5 hexes of your map edge
   - No deployment in impassable terrain
   - Special units (Partisans) can deploy anywhere
   - **Priority: MEDIUM** - Game setup

10. ❌ **Artillery Special Movement**
    - Artillery has speed 0 in movement phase
    - Artillery has speed 2 in assault phase only
    - Must choose: move OR fire
    - **Priority: LOW** - Specific unit type

### MEDIUM PRIORITY (Expand gameplay)

11. ❌ **Aircraft System**
    - Aircraft don't occupy hexes
    - Placed during Flight Phase
    - Attack during Airstrike Phase
    - Removed at end of turn
    - **Priority: LOW** - Optional expansion

12. ❌ **Commander Abilities**
    - Initiative bonuses
    - Special command abilities
    - Affect nearby units
    - **Priority: LOW** - Enhancement

13. ❌ **Advanced Terrain**
    - Hex-side terrain (streams, bluffs, hedges)
    - Fringe terrain
    - Movement roll terrain
    - Half-hexes
    - **Priority: LOW** - Map variety

14. ❌ **Spotters & Indirect Fire**
    - Spotter units
    - Mortars can attack without LOS if spotter present
    - **Priority: LOW** - Special mechanic

15. ❌ **Special Unit Types**
    - Snipers
    - Paratroopers  
    - Partisans (deploy anywhere)
    - Obstacles
    - **Priority: LOW** - Unit variety

---

## Known Bugs & Issues

### Current Issues
1. ⚠️ **Always uses `defense_front`** - Rear armor not implemented (see CRITICAL #2)
2. ⚠️ **Wrong dice mechanics** - Should roll ≤ attack value to hit (see CRITICAL #1)
3. ⚠️ **No disruption/damage** - Units die in one hit (see CRITICAL #3)
4. ⚠️ **No cover saves** - Units always take full damage (see CRITICAL #4)
5. ⚠️ **Ability actions generated but not executed** - UseAbilityAction exists but most abilities don't do anything
6. ⚠️ **Status effect fields exist but unused** - `is_disrupted`, `is_damaged` in UnitState not used

### Technical Debt
1. Path calculation in MoveAction is simplified (just start→end, not full path)
2. Movement cost calculation doesn't account for all terrain types
3. No casualty phase - damage applied immediately
4. No initiative rolls

---

## Testing Status

### Tested Systems
- ✅ Movement system with terrain costs
- ✅ LOS geometric detection
- ✅ Action generation
- ✅ Action execution
- ✅ Game state cloning
- ✅ Turn progression

### Untested Systems
- ❌ Combat with correct dice (roll ≤ attack to hit)
- ❌ Cover saves
- ❌ Disruption/damage system
- ❌ Defensive fire
- ❌ Front/rear armor
- ❌ Stacking
- ❌ Initiative system

---

## Next Steps (Recommended Order)

1. **Fix test suite** - Get `test_game_engine.py` passing with `defense_front` ✅ (in progress)
2. **Implement proper dice system** - Roll ≤ attack value to hit
3. **Add disruption/damage mechanics** - First hit disrupts, second destroys
4. **Implement cover saves** - 4+ for infantry, 5+ for vehicles
5. **Add unit facing** - Track orientation for front/rear armor
6. **Implement defensive fire** - Units fire when enemies move adjacent
7. **Add simultaneous combat** - Casualty phase applies all damage at once
8. **Build initiative system** - 2d6 roll to determine turn order
9. **Enable stacking** - Multiple units per hex
10. **Build simple AI agent** - Random or heuristic for testing

---

## File Inventory

### Core Files (Production)
- `board.py` - ✅ Board and hex grid system
- `units.py` - ✅ Unit data loading
- `movement.py` - ✅ Movement and LOS system
- `combat.py` - ⚠️ Combat system (wrong dice mechanics, no facing)
- `abilities.py` - ✅ Ability system
- `game_state.py` - 🔧 Game state management (fixing defense attribute)
- `action.py` - ✅ Action definitions
- `action_generator.py` - ✅ Legal action generation
- `action_executor.py` - ⚠️ Action execution (simplified combat)

### Test Files
- `test_game_engine.py` - 🔧 Integration tests (currently failing on defense attribute)
- `movement.py` (test section) - ✅ Movement/LOS unit tests

### Data Files
- `Axis and Allies Unit Data for Analysis - Units.csv` - ✅ Unit database
- `Axis and Allies Unit Data for Analysis - Special_Abilities.csv` - ✅ Abilities database

### Missing Files (To Be Created)
- `dice.py` - Proper A&A dice rolling system
- `facing.py` - Unit orientation system
- `status_effects.py` - Disrupted/Damaged mechanics
- `cover.py` - Cover save system
- `defensive_fire.py` - Defensive fire mechanics
- `initiative.py` - Initiative roll system
- `agent.py` - AI agent base class
- `evaluation.py` - Position evaluation for AI

### Tracking Document
- `IMPLEMENTATION_STATUS.md` - ✅ This file

---

## Questions to Resolve

1. **Dice mechanics**: Confirm attack value = max die roll to hit (e.g., attack 5 hits on 1-5)?
2. **Disruption**: First hit always disrupts, or only with cover save?
3. **Facing**: Exactly 180° front arc (3 hex faces) and 180° rear arc (3 hex faces)?
4. **Assault movement**: Can units that didn't move in movement phase move in assault phase?
5. **Cover from defensive fire**: Defensive fire can only disrupt, never destroy?
6. **Artillery movement**: Speed 0 in movement, speed 2 in assault, must choose move OR fire?

---

**This document will be updated as:**
- Features are implemented
- Bugs are discovered
- Rules are clarified
- Design decisions are made