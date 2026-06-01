"""
Action Executor for Axis & Allies Miniatures

Validates and executes actions on game states.
Uses the authentic dice mechanics from dice.py for combat resolution.
Integrates defensive fire system for movement.
Integrates facing system for front/rear armor.
Integrates casualty system for simultaneous combat.
"""

from typing import Tuple, Optional, Dict, List
from game_state import GameState, GamePhase, UnitState
from action import (
    Action, MoveAction, AttackAction, MoveAndAttackAction,
    UseAbilityAction, PassAction, EndPhaseAction, DeployAction,
    PlaceAircraftAction, BoardTransportAction, DismountTransportAction,
    ActionValidator, ActionValidation
)
from movement import MovementSystem
from abilities import AbilitySystem
from dice import DiceSystem, UnitStatus, UnitCategory, get_unit_category
from defensive_fire import DefensiveFireSystem, DefensiveFireResult
from facing import (
    FacingSystem, HexDirection, is_front_arc_attack,
    calculate_facing_after_move, get_direction_name
)
from casualty import CasualtySystem


class ActionResult:
    """Result of executing an action"""
    def __init__(self, success: bool, message: str = "", 
                 hits: int = 0, unit_destroyed: str = None,
                 combat_details: Dict = None,
                 defensive_fire_results: List[DefensiveFireResult] = None):
        self.success = success
        self.message = message
        self.hits = hits  # Number of hits scored (0, 1, 2, or 3)
        self.unit_destroyed = unit_destroyed
        self.combat_details = combat_details or {}
        self.defensive_fire_results = defensive_fire_results or []
    
    def __str__(self):
        return f"{'✓' if self.success else '✗'} {self.message}"
    
    def __bool__(self):
        return self.success


class ActionExecutor:
    """
    Validates and executes actions on game states.
    Uses authentic A&A Miniatures dice mechanics.
    Integrates defensive fire during movement.
    """

    # Cover-granting terrain types
    COVER_TERRAIN = ['forest', 'building', 'hill', 'town']

    def __init__(self, movement_system: MovementSystem,
                 combat_system,  # Can be old or new CombatSystem
                 ability_system: AbilitySystem,
                 random_seed: Optional[int] = None,
                 use_simultaneous_combat: bool = True):
        self.movement_system = movement_system
        self.combat_system = combat_system
        self.ability_system = ability_system
        self.dice = DiceSystem(random_seed)
        self.defensive_fire = DefensiveFireSystem(ability_system, random_seed)
        self.casualty_system = CasualtySystem()
        self.use_simultaneous_combat = use_simultaneous_combat
        self.validator = ActionValidator(
            None, movement_system, combat_system, ability_system
        )

    def _has_strike_and_fade(self, unit) -> bool:
        """Check if a unit has the Strike and Fade ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if 'strike and fade' in ability.lower():
                return True
        return False

    def _has_blast(self, unit) -> bool:
        """Check if a unit has the Blast ability (attacks all units in target hex)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'blast':
                return True
        return False

    def _has_all_guns_blazing(self, unit) -> bool:
        """Check if a unit has All Guns Blazing (extra attack vs Soldier after attacking)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'all guns blazing':
                return True
        return False

    def _has_strafe(self, unit) -> bool:
        """Check if a unit has Strafe (attack adjacent Soldier after main attack)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'strafe':
                return True
        return False

    def _has_overrun(self, unit) -> bool:
        """Check if a unit has Overrun (disrupt Soldier when entering hex)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'overrun':
                return True
        return False

    def _has_multiturreted(self, unit) -> bool:
        """Check if a unit has Multiturreted (two attacks, one front, one rear)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if 'multiturreted' in ability.lower():
                return True
        return False

    def _check_coordinated_fire_bonus(self, game_state: GameState, unit,
                                       unit_state: UnitState) -> bool:
        """
        Check if unit with Coordinated Fire is adjacent to a friendly Commander.
        Returns True if bonus applies.
        """
        # Check if unit has Coordinated Fire
        abilities = getattr(unit, 'abilities', []) or []
        has_coordinated_fire = any(a.lower() == 'coordinated fire' for a in abilities)
        if not has_coordinated_fire:
            return False

        # Check for adjacent friendly Commander
        unit_pos = unit_state.position
        friendly_units = game_state.get_units_by_owner(unit_state.owner)

        for friendly_state in friendly_units:
            if not friendly_state.is_alive:
                continue
            if friendly_state.unit.id == unit.id:
                continue  # Skip self

            # Check if this unit is a Commander
            friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []
            is_commander = any('commander abilities' in a.lower() for a in friendly_abilities)
            if not is_commander:
                continue

            # Check if adjacent (distance <= 1)
            friendly_pos = friendly_state.position
            distance = game_state.board.hex_distance(
                unit_pos[0], unit_pos[1], friendly_pos[0], friendly_pos[1]
            )
            if distance <= 1:
                return True

        return False

    def _check_spotter_bonus(self, game_state: GameState, owner: str,
                             target_pos: Tuple[int, int]) -> bool:
        """
        Check if a friendly Spotter provides bonus dice for Aircraft attacks.
        Spotter: If within 8 hexes of target with LOS, Aircraft get +1 attack die.
        """
        friendly_units = game_state.get_units_by_owner(owner)

        for unit_state in friendly_units:
            if not unit_state.is_alive:
                continue

            unit = unit_state.unit
            abilities = getattr(unit, 'abilities', []) or []

            # Check if unit has Spotter ability
            has_spotter = any(a.lower() == 'spotter' for a in abilities)
            if not has_spotter:
                continue

            # Chatting on the Radio: unit only counts as Spotter if it didn't move
            has_chatting = any(a.lower() == 'chatting on the radio' for a in abilities)
            if has_chatting and unit_state.has_moved:
                continue

            # Check distance (within 8 hexes)
            spotter_pos = unit_state.position
            target_q, target_r = target_pos
            distance = game_state.board.hex_distance(
                spotter_pos[0], spotter_pos[1], target_q, target_r
            )
            if distance > 8:
                continue

            # Check line of sight
            has_los = self.movement_system.has_line_of_sight(
                game_state.board, spotter_pos, target_pos
            )
            if has_los:
                return True

        return False

    def _check_bravery_enforcement_bonus(self, game_state: GameState,
                                          attacker_state: UnitState) -> bool:
        """
        Check if a disrupted Soldier is adjacent to a friendly unit with Bravery Enforcement.
        If so, they don't suffer the -1 penalty to attack dice.

        Returns True if the attacker is a Soldier and has adjacent Bravery Enforcement.
        """
        # Only applies to Soldiers
        if attacker_state.unit.unit_type != 'Soldier':
            return False

        # Check for adjacent friendly unit with Bravery Enforcement
        unit_pos = attacker_state.position
        friendly_units = game_state.get_units_by_owner(attacker_state.owner)

        for friendly_state in friendly_units:
            if not friendly_state.is_alive:
                continue
            if friendly_state.unit.id == attacker_state.unit.id:
                continue  # Skip self

            # Check if this unit has Bravery Enforcement
            friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []
            has_bravery_enforcement = any(
                a.lower() == 'bravery enforcement' for a in friendly_abilities
            )
            if not has_bravery_enforcement:
                continue

            # Check if adjacent (distance <= 1)
            friendly_pos = friendly_state.position
            distance = game_state.board.hex_distance(
                unit_pos[0], unit_pos[1], friendly_pos[0], friendly_pos[1]
            )
            if distance <= 1:
                return True

        return False

    def _check_minefield(self, game_state: GameState, hex_pos: Tuple[int, int],
                         unit_state) -> Optional[str]:
        """
        Check if a hex has a Minefield obstacle and roll for disruption.
        Returns a message if the unit was disrupted, None otherwise.

        Minefield: When a unit ends its move or leaves a hex containing
        this Obstacle, roll a die. On 3 or less, that unit is Disrupted.
        """
        q, r = hex_pos
        units_in_hex = game_state.get_units_at_position(q, r)

        for obstacle_state in units_in_hex:
            if obstacle_state.unit.unit_type != 'Obstacle':
                continue
            obstacle_abilities = getattr(obstacle_state.unit, 'abilities', []) or []
            has_minefield = any(a.lower() == 'minefield' for a in obstacle_abilities)
            if has_minefield:
                # Roll the die
                roll = self.dice.roll_single_die()
                if roll <= 3:
                    if not unit_state.is_disrupted:
                        unit_state.is_disrupted = True
                        return f"Minefield! Rolled {roll}, {unit_state.unit.name} DISRUPTED!"
                    else:
                        return f"Minefield! Rolled {roll}, but already disrupted"
                else:
                    return f"Minefield! Rolled {roll}, safe"

        return None

    def reset_defensive_fire_phase(self):
        """
        Reset defensive fire tracking for a new phase.
        Call this at the start of each movement phase.
        """
        self.defensive_fire.reset_phase()
    
    def reset_assault_phase(self):
        """
        Reset casualty system for a new assault phase.
        Call this at the start of each assault phase.
        """
        self.casualty_system.reset_phase()
    
    def resolve_casualty_phase(self, game_state: GameState) -> Dict:
        """
        Resolve the casualty phase - flip counters and remove destroyed units.
        Call this after all assault phases are complete.
        
        Returns results dictionary with destroyed/damaged/disrupted units.
        """
        return self.casualty_system.resolve_casualty_phase(game_state)
    
    def execute_action(self, game_state: GameState, action: Action) -> ActionResult:
        """
        Execute an action on the game state.
        Returns ActionResult indicating success/failure and effects.
        """
        # Update validator's board reference
        self.validator.board = game_state.board
        
        # Route to appropriate handler based on action type
        if isinstance(action, MoveAction):
            return self._execute_move(game_state, action)
        
        elif isinstance(action, AttackAction):
            return self._execute_attack(game_state, action)
        
        elif isinstance(action, MoveAndAttackAction):
            return self._execute_move_and_attack(game_state, action)
        
        elif isinstance(action, UseAbilityAction):
            return self._execute_ability(game_state, action)
        
        elif isinstance(action, PassAction):
            return self._execute_pass(game_state, action)
        
        elif isinstance(action, EndPhaseAction):
            return self._execute_end_phase(game_state, action)

        elif isinstance(action, BoardTransportAction):
            return self._execute_board_transport(game_state, action)

        elif isinstance(action, DismountTransportAction):
            return self._execute_dismount_transport(game_state, action)

        elif isinstance(action, DeployAction):
            return self._execute_deploy(game_state, action)

        elif isinstance(action, PlaceAircraftAction):
            return self._execute_place_aircraft(game_state, action)

        else:
            return ActionResult(False, f"Unknown action type: {type(action)}")
    
    def _execute_move(self, game_state: GameState, action: MoveAction) -> ActionResult:
        """Execute a movement action with defensive fire checks"""
        unit_state = game_state.get_unit_state(action.unit_id)
        if not unit_state:
            return ActionResult(False, f"Unit {action.unit_id} not found")

        # Check if this is a Strike and Fade or Relocate move
        is_strike_and_fade = getattr(action, 'is_strike_and_fade', False)
        is_relocate = getattr(action, 'is_relocate', False)

        unit_abilities = getattr(unit_state.unit, 'abilities', []) or []

        # Dug In: This unit can't move
        has_dug_in = any(a.lower() == 'dug in' for a in unit_abilities)
        if has_dug_in:
            return ActionResult(False, f"{unit_state.unit.name} is Dug In and cannot move")

        # Prone to Breakdown: This unit can't move while damaged
        has_prone_to_breakdown = any(a.lower() == 'prone to breakdown' for a in unit_abilities)
        if has_prone_to_breakdown and unit_state.is_damaged:
            return ActionResult(False, f"{unit_state.unit.name} is damaged and cannot move (Prone to Breakdown)")

        # Check if unit is disrupted (can't move normally)
        # Strike and Fade and Relocate still work when disrupted (debatable, but more fun)
        # Robust: "While disrupted, this unit has speed 1" - allows movement when disrupted
        # Charge: Can move while disrupted if moving closer to an enemy Soldier
        if unit_state.is_disrupted and not (is_strike_and_fade or is_relocate):
            unit_abilities = getattr(unit_state.unit, 'abilities', []) or []
            has_robust = any(a.lower() == 'robust' for a in unit_abilities)
            has_charge = any(a.lower() == 'charge' for a in unit_abilities)

            can_move = False
            if has_robust:
                can_move = True
            elif has_charge:
                # Charge: Can move if moving closer to an enemy Soldier
                enemy_owner = "player2" if unit_state.owner == "player1" else "player1"
                enemy_units = game_state.get_units_by_owner(enemy_owner)
                from_pos = (action.from_q, action.from_r)
                to_pos = (action.to_q, action.to_r)

                for enemy_state in enemy_units:
                    if enemy_state.is_alive and enemy_state.unit.unit_type == 'Soldier':
                        enemy_pos = enemy_state.position
                        dist_from = game_state.board.hex_distance(
                            from_pos[0], from_pos[1], enemy_pos[0], enemy_pos[1])
                        dist_to = game_state.board.hex_distance(
                            to_pos[0], to_pos[1], enemy_pos[0], enemy_pos[1])
                        if dist_to < dist_from:
                            can_move = True
                            break

            if not can_move:
                return ActionResult(False, f"{unit_state.unit.name} is disrupted and cannot move")

        unit = unit_state.unit

        # Validate the move
        validation = self.validator.validate_move(action, unit, game_state)
        if not validation:
            return ActionResult(False, f"Invalid move: {validation.reason}")

        # Weak Suspension: must make movement roll to enter hill hex (except along road)
        to_hex_obj = game_state.board.get_hex(action.to_q, action.to_r)
        if to_hex_obj and to_hex_obj.terrain == 'hill':
            unit_abilities = getattr(unit, 'abilities', []) or []
            has_weak_suspension = any(a.lower() == 'weak suspension' for a in unit_abilities)
            has_road = getattr(to_hex_obj, 'has_road', False)

            if has_weak_suspension and not has_road:
                # Get movement roll bonus from Robust/Mountaineering
                movement_mods = self.ability_system.get_movement_modifiers(unit)
                roll_bonus = movement_mods.get('movement_roll_bonus', 0)

                roll, success = self.dice.roll_movement(roll_bonus)

                # Lead the Way: Once per turn, reroll a movement roll
                if not success:
                    has_lead_the_way = any(a.lower() == 'lead the way' for a in unit_abilities)
                    if has_lead_the_way and not unit_state.lead_the_way_used:
                        old_roll = roll
                        roll, success = self.dice.roll_movement(roll_bonus)
                        unit_state.lead_the_way_used = True
                        # Success will be checked below

                if not success:
                    return ActionResult(
                        False,
                        f"{unit.name} failed movement roll to enter hill (rolled {roll}, needed {4 - roll_bonus}+)"
                    )

        # Check for edge obstacles that require movement rolls
        edge_obstacle = game_state.board.get_edge_obstacle(
            action.from_q, action.from_r, action.to_q, action.to_r
        )
        if edge_obstacle:
            obstacle_lower = edge_obstacle.lower()
            requires_roll = False
            obstacle_name = edge_obstacle

            # Barbed Wire: Soldiers must make movement roll
            if obstacle_lower == 'barbed wire' and unit.unit_type == 'Soldier':
                requires_roll = True
                obstacle_name = "Barbed Wire"

            # Destroyed Bridge: All units must make movement roll (stream crossing)
            elif obstacle_lower == 'destroyed_bridge':
                requires_roll = True
                obstacle_name = "stream (destroyed bridge)"

            if requires_roll:
                movement_mods = self.ability_system.get_movement_modifiers(unit)
                roll_bonus = movement_mods.get('movement_roll_bonus', 0)
                roll, success = self.dice.roll_movement(roll_bonus)

                if not success:
                    return ActionResult(
                        False,
                        f"{unit.name} failed movement roll to cross {obstacle_name} (rolled {roll}, needed {4 - roll_bonus}+)"
                    )

        # AVRE: This unit ignores Obstacles and destroys each Obstacle it crosses/enters
        has_avre = any(a.lower() == 'avre' for a in unit_abilities)
        avre_destroyed_obstacles = []

        # Tank Obstacle: Vehicles must make movement roll to enter hex with Tank Obstacle unit
        # (AVRE units ignore this requirement and destroy the obstacle instead)
        if unit.unit_type == 'Vehicle':
            units_at_dest = game_state.get_units_at_position(action.to_q, action.to_r)
            for dest_unit_state in units_at_dest:
                dest_abilities = getattr(dest_unit_state.unit, 'abilities', []) or []
                has_tank_obstacle = any('tank obstacle' in a.lower() for a in dest_abilities)
                is_obstacle = dest_unit_state.unit.unit_type == 'Obstacle'

                if has_avre and is_obstacle:
                    # AVRE destroys obstacles on entry
                    avre_destroyed_obstacles.append(dest_unit_state)
                elif has_tank_obstacle and not has_avre:
                    movement_mods = self.ability_system.get_movement_modifiers(unit)
                    roll_bonus = movement_mods.get('movement_roll_bonus', 0)
                    roll, success = self.dice.roll_movement(roll_bonus)

                    if not success:
                        return ActionResult(
                            False,
                            f"{unit.name} failed movement roll to cross Tank Obstacle (rolled {roll}, needed {4 - roll_bonus}+)"
                        )
                    break  # Only one roll needed per Tank Obstacle

        # AVRE also destroys edge obstacles when crossing them
        if has_avre:
            edge_obstacle = game_state.board.get_edge_obstacle(
                action.from_q, action.from_r, action.to_q, action.to_r
            )
            if edge_obstacle:
                game_state.board.remove_edge_obstacle(
                    action.from_q, action.from_r, action.to_q, action.to_r
                )
                avre_destroyed_obstacles.append(('edge', edge_obstacle))

        from_hex = (action.from_q, action.from_r)
        to_hex = (action.to_q, action.to_r)
        
        # Check for defensive fire opportunities
        df_opportunities = self.defensive_fire.check_defensive_fire_triggered(
            game_state, action.unit_id, from_hex, to_hex
        )

        # Check if moving unit has Determined Charge (doesn't stop when disrupted)
        has_determined_charge = any(
            a.lower() == 'determined charge' for a in (getattr(unit, 'abilities', []) or [])
        )

        df_results = []
        movement_stopped = False
        final_hex = to_hex

        # Resolve each defensive fire attack
        for opportunity in df_opportunities:
            # AI chooses to attack in destination hex by default (usually better)
            # In a human game, defender would choose
            result = self.defensive_fire.resolve_defensive_fire(
                game_state, opportunity, attack_in_hex=to_hex
            )
            df_results.append(result)

            # Apply the result
            stopped_at = self.defensive_fire.apply_defensive_fire_result(game_state, result)

            # Determined Charge: unit doesn't stop when disrupted (but still takes disruption)
            if result.movement_stopped and not has_determined_charge:
                movement_stopped = True
                final_hex = stopped_at if stopped_at else to_hex
                # Once stopped, no more defensive fire matters
                break
            elif result.movement_stopped and has_determined_charge:
                # Disrupted but keeps moving
                result.message += " [Determined Charge: continues moving]"
        
        # Execute the move (to final hex - may be destination or where stopped)
        if movement_stopped:
            # Move to where unit was stopped
            success = game_state.move_unit(action.unit_id, final_hex[0], final_hex[1])
            message = f"{unit.name} moved to ({final_hex[0]}, {final_hex[1]}) - STOPPED by defensive fire!"
        else:
            success = game_state.move_unit(action.unit_id, action.to_q, action.to_r)
            message = f"{unit.name} moved to ({action.to_q}, {action.to_r})"
        
        if success:
            game_state.apply_action(action)

            # Consume Strike and Fade if this was a fade move
            if is_strike_and_fade:
                unit_state.strike_and_fade_available = False
                message = f"{unit.name} fades to ({action.to_q}, {action.to_r})"
            elif is_relocate:
                message = f"{unit.name} relocates to ({action.to_q}, {action.to_r})"

            # Update facing for vehicles
            if unit.unit_type == 'Vehicle':
                new_facing = calculate_facing_after_move(from_hex, final_hex)
                unit_state.facing = new_facing.value
                facing_str = get_direction_name(new_facing)
                message += f" (facing {facing_str})"

            # Transport: Move carried unit with the transport
            if unit_state.carried_unit_id:
                carried_state = game_state.get_unit_state(unit_state.carried_unit_id)
                if carried_state:
                    carried_state.position = final_hex
                    message += f" [carrying {carried_state.unit.name}]"

            # AVRE: Destroy obstacles in the destination hex after successful move
            if has_avre and avre_destroyed_obstacles:
                for obstacle in avre_destroyed_obstacles:
                    if isinstance(obstacle, tuple) and obstacle[0] == 'edge':
                        message += f" [AVRE destroyed {obstacle[1]}]"
                    else:
                        # Remove the obstacle unit from the game
                        obstacle_name = obstacle.unit.name
                        game_state.remove_unit(obstacle.unit.id)
                        message += f" [AVRE destroyed {obstacle_name}]"

            # Overrun: Once per phase, disrupt one enemy Soldier in any hex along
            # the movement path (including the final hex)
            if self._has_overrun(unit) and not unit_state.overrun_used_this_phase:
                enemy_owner = "player2" if unit_state.owner == "player1" else "player1"
                # Check all hexes along the path for enemy soldiers
                path_hexes = action.path if action.path else [from_hex, final_hex]
                for path_hex in path_hexes:
                    if unit_state.overrun_used_this_phase:
                        break
                    units_in_hex = game_state.get_units_at_position(path_hex[0], path_hex[1])
                    for other_state in units_in_hex:
                        if other_state.owner == enemy_owner and other_state.unit.unit_type == 'Soldier':
                            if not other_state.is_disrupted:
                                other_state.is_disrupted = True
                                unit_state.overrun_used_this_phase = True
                                message += f"\n    💥 Overrun: {other_state.unit.name} DISRUPTED!"
                                break  # Only one Soldier per phase

            # Minefield check: when leaving hex with Minefield
            minefield_leave_msg = self._check_minefield(game_state, from_hex, unit_state)
            if minefield_leave_msg:
                message += f"\n    💣 Leaving: {minefield_leave_msg}"

            # Minefield check: when ending movement in hex with Minefield
            if final_hex != from_hex:
                minefield_enter_msg = self._check_minefield(game_state, final_hex, unit_state)
                if minefield_enter_msg:
                    message += f"\n    💣 Entering: {minefield_enter_msg}"

            # Add defensive fire info to message if any occurred
            for df_result in df_results:
                if df_result.target_disrupted:
                    message += f"\n    ⚔ {df_result.message}"
                elif df_result.dice_rolled > 0:
                    message += f"\n    ⚔ {df_result.message}"

            return ActionResult(
                True,
                message,
                defensive_fire_results=df_results
            )
        else:
            return ActionResult(False, "Move failed")
    
    def _execute_attack(self, game_state: GameState, action: AttackAction) -> ActionResult:
        """Execute an attack action using authentic dice mechanics"""
        attacker_state = game_state.get_unit_state(action.unit_id)
        target_state = game_state.get_unit_state(action.target_id)
        
        if not attacker_state:
            return ActionResult(False, f"Attacker {action.unit_id} not found")
        
        if not target_state:
            return ActionResult(False, f"Target {action.target_id} not found")
        
        attacker = attacker_state.unit
        target = target_state.unit

        # Undermanned: This unit can't attack while disrupted
        has_undermanned = any(
            a.lower() == 'undermanned' for a in (getattr(attacker, 'abilities', []) or [])
        )
        if has_undermanned and attacker_state.is_disrupted:
            return ActionResult(False, f"{attacker.name} can't attack while disrupted (Undermanned)")

        # Turret Lock: This unit can't attack while in a hill hex
        attacker_abilities = getattr(attacker, 'abilities', []) or []
        has_turret_lock = any(a.lower() == 'turret lock' for a in attacker_abilities)
        if has_turret_lock:
            attacker_hex = game_state.board.get_hex(action.attacker_q, action.attacker_r)
            if attacker_hex and attacker_hex.terrain == 'hill':
                return ActionResult(False, f"{attacker.name} can't attack while in hill hex (Turret Lock)")

        # Collapsible Screen: While this unit is in a water hex, it can't attack
        has_collapsible_screen = any(a.lower() == 'collapsible screen' for a in attacker_abilities)
        if has_collapsible_screen:
            attacker_hex = game_state.board.get_hex(action.attacker_q, action.attacker_r)
            if attacker_hex and attacker_hex.terrain == 'water':
                return ActionResult(False, f"{attacker.name} can't attack while in water hex (Collapsible Screen)")

        # Limited Range 2/6: Can only attack at ranges of X or less
        for ability in attacker_abilities:
            if ability.lower() == 'limited range 2':
                if action.distance > 2:
                    return ActionResult(False, f"{attacker.name} can't attack beyond range 2 (Limited Range 2)")
            elif ability.lower() == 'limited range 6':
                if action.distance > 6:
                    return ActionResult(False, f"{attacker.name} can't attack beyond range 6 (Limited Range 6)")

        # Validate the attack
        validation = self.validator.validate_attack(action, attacker, target, game_state)
        if not validation:
            return ActionResult(False, f"Invalid attack: {validation.reason}")
        
        # Get terrain for cover check
        target_hex = game_state.board.get_hex(action.target_q, action.target_r)
        target_terrain = target_hex.terrain if target_hex else 'open'
        
        # Determine if rear attack based on facing
        attacker_pos = (action.attacker_q, action.attacker_r)
        target_pos = (action.target_q, action.target_r)
        
        # Check if same hex (always rear for vehicles)
        attacker_same_hex = (action.attacker_q == action.target_q and 
                           action.attacker_r == action.target_r)
        
        # Determine front/rear based on target facing
        is_rear_attack = False
        if target.unit_type == 'Vehicle' and target_state.facing is not None:
            target_facing = HexDirection(target_state.facing)
            is_front = is_front_arc_attack(attacker_pos, target_pos, target_facing)
            is_rear_attack = not is_front

        # Check Fixed Gun / No Turret / Fixed Howitzer / Fixed Rear Gun restrictions
        attacker_abilities = getattr(attacker, 'abilities', []) or []
        has_fixed_gun = any(a.lower() == 'fixed gun' for a in attacker_abilities)
        has_no_turret = any(a.lower() == 'no turret' for a in attacker_abilities)
        has_fixed_howitzer = any(a.lower() == 'fixed howitzer' for a in attacker_abilities)
        has_fixed_rear_gun = any(a.lower() == 'fixed rear gun' for a in attacker_abilities)

        # Determine if target is in front or behind the attacker
        target_in_front = True  # Default if attacker has no facing
        target_in_rear = False
        if attacker.unit_type == 'Vehicle' and attacker_state.facing is not None:
            attacker_facing = HexDirection(attacker_state.facing)
            target_in_front = is_front_arc_attack(target_pos, attacker_pos, attacker_facing)
            target_in_rear = not target_in_front

        # Fixed Gun / No Turret: Can attack Vehicles only if in front
        if (has_fixed_gun or has_no_turret) and target.unit_type == 'Vehicle':
            if not target_in_front:
                return ActionResult(False, f"{attacker.name} can only attack Vehicles in front (Fixed Gun/No Turret)")

        # Fixed Howitzer: Can attack only units in front
        if has_fixed_howitzer and not target_in_front:
            return ActionResult(False, f"{attacker.name} can only attack units in front (Fixed Howitzer)")

        # Fixed Rear Gun: Can attack Vehicles only if behind
        if has_fixed_rear_gun and target.unit_type == 'Vehicle':
            if not target_in_rear:
                return ActionResult(False, f"{attacker.name} can only attack Vehicles behind (Fixed Rear Gun)")

        # Check for Spotter bonus (Aircraft get +1 die if friendly Spotter sees target)
        spotter_bonus = 0
        if attacker.unit_type == 'Aircraft':
            if self._check_spotter_bonus(
                game_state, attacker_state.owner, (action.target_q, action.target_r)
            ):
                spotter_bonus = 1

        # Check for Coordinated Fire bonus (+1 die when adjacent to friendly Commander)
        coordinated_fire_bonus = 0
        if self._check_coordinated_fire_bonus(game_state, attacker, attacker_state):
            coordinated_fire_bonus = 1

        # Combine bonuses
        total_bonus_dice = spotter_bonus + coordinated_fire_bonus

        # Check for Bombs special attack (once per game, same hex only)
        is_bombs_attack = getattr(action, 'is_bombs', False)
        bombs_dice = 0
        if is_bombs_attack:
            if attacker_state.bombs_used:
                return ActionResult(False, "Bombs already used this game")
            if action.distance != 0:
                return ActionResult(False, "Bombs can only be used in same hex")
            # Mark bombs as used
            attacker_state.bombs_used = True
            # Determine dice: 12 vs Soldier, 8 vs Vehicle
            if target.unit_type == 'Soldier':
                bombs_dice = 12
            else:
                bombs_dice = 8

        # Check for Speed Boost (once per game, attack vs Aircraft resolves immediately)
        is_speed_boost = getattr(action, 'is_speed_boost', False)
        force_immediate_resolve = False
        if is_speed_boost:
            if attacker_state.speed_boost_used:
                return ActionResult(False, "Speed Boost already used this game")
            if target.unit_type != 'Aircraft':
                return ActionResult(False, "Speed Boost can only be used against Aircraft")
            # Mark Speed Boost as used
            attacker_state.speed_boost_used = True
            force_immediate_resolve = True

        # Check for HE Round (once per game, 15 dice vs Soldier)
        is_he_round = getattr(action, 'is_he_round', False)
        he_round_dice = 0
        if is_he_round:
            if attacker_state.he_round_used:
                return ActionResult(False, "HE Round already used this game")
            if target.unit_type != 'Soldier':
                return ActionResult(False, "HE Round can only be used against Soldiers")
            attacker_state.he_round_used = True
            he_round_dice = 15

        # Check for Headshot (once per game, 6 dice, 3+ successes = disrupt Vehicle)
        is_headshot = getattr(action, 'is_headshot', False)
        if is_headshot:
            if attacker_state.headshot_used:
                return ActionResult(False, "Headshot already used this game")
            if target.unit_type != 'Vehicle':
                return ActionResult(False, "Headshot can only be used against Vehicles")
            attacker_state.headshot_used = True

            # Special Headshot resolution: 6 dice, need 3+ successes to disrupt
            attack_mods = self.ability_system.get_attack_modifiers(
                attacker, target, action.distance, target_terrain,
                is_rear_attack, target_state
            )
            threshold = attack_mods.get('hit_threshold', 4)
            rolls = [self.dice.roll_single_die() for _ in range(6)]
            successes = sum(1 for r in rolls if r >= threshold)

            if successes >= 3:
                target_state.is_disrupted = True
                return ActionResult(
                    True,
                    f"{attacker.name} Headshot vs {target.name}: {successes} successes - Vehicle DISRUPTED!",
                    attack_result={'rolls': rolls, 'successes': successes, 'threshold': threshold}
                )
            else:
                return ActionResult(
                    True,
                    f"{attacker.name} Headshot vs {target.name}: {successes} successes (needed 3) - No effect",
                    attack_result={'rolls': rolls, 'successes': successes, 'threshold': threshold}
                )

        # Check for Remote Control (once per game, roll 2 dice vs range, then attack)
        is_remote_control = getattr(action, 'is_remote_control', False)
        if is_remote_control:
            if attacker_state.remote_control_used:
                return ActionResult(False, "Remote Control already used this game")
            if target.unit_type not in ('Soldier', 'Vehicle'):
                return ActionResult(False, "Remote Control can only target Soldiers and Vehicles")
            attacker_state.remote_control_used = True

            # Roll 2 dice - both must be greater than the range
            range_roll_1 = self.dice.roll_single_die()
            range_roll_2 = self.dice.roll_single_die()
            range_to_target = action.distance

            if range_roll_1 > range_to_target and range_roll_2 > range_to_target:
                # Success! Roll attack dice
                attack_dice = 12 if target.unit_type == 'Vehicle' else 8
                attack_mods = self.ability_system.get_attack_modifiers(
                    attacker, target, action.distance, target_terrain,
                    is_rear_attack, target_state
                )
                threshold = attack_mods.get('hit_threshold', 4)
                attack_rolls = [self.dice.roll_single_die() for _ in range(attack_dice)]
                successes = sum(1 for r in attack_rolls if r >= threshold)

                # Get defense and calculate hits
                defense_mods = self.ability_system.get_defense_modifiers(
                    target, attacker, action.distance, target_terrain,
                    is_rear_attack, target_state
                )
                base_defense = defense_mods.get('defense', target.defense_front or 3)
                superior_armor = defense_mods.get('superior_armor', 0)
                hits = self.dice.calculate_hits(successes, base_defense, superior_armor)

                # Apply damage
                result_msg = f"{attacker.name} Remote Control vs {target.name}: range rolls [{range_roll_1}, {range_roll_2}] > {range_to_target}, "
                result_msg += f"{successes} successes from {attack_dice} dice"

                if hits >= 2:
                    target_state.is_destroyed = True
                    target_state.is_alive = False
                    result_msg += " - TARGET DESTROYED!"
                elif hits == 1:
                    if target.unit_type == 'Vehicle' and not target_state.is_damaged:
                        target_state.is_damaged = True
                        result_msg += " - Vehicle DAMAGED"
                    else:
                        target_state.is_disrupted = True
                        result_msg += " - Target DISRUPTED"
                else:
                    result_msg += " - No effect"

                return ActionResult(
                    True,
                    result_msg,
                    attack_result={
                        'range_rolls': [range_roll_1, range_roll_2],
                        'range_to_target': range_to_target,
                        'attack_rolls': attack_rolls,
                        'successes': successes,
                        'threshold': threshold,
                        'hits': hits
                    }
                )
            else:
                # Range check failed
                return ActionResult(
                    True,
                    f"{attacker.name} Remote Control vs {target.name}: range rolls [{range_roll_1}, {range_roll_2}] vs range {range_to_target} - FAILED",
                    attack_result={
                        'range_rolls': [range_roll_1, range_roll_2],
                        'range_to_target': range_to_target,
                        'failed': True
                    }
                )

        # Check for Rocket Salvo (once per game, area attack)
        is_rocket_salvo = getattr(action, 'is_rocket_salvo', False)
        if is_rocket_salvo:
            if attacker_state.rocket_salvo_used:
                return ActionResult(False, "Rocket Salvo already used this game")
            attacker_state.rocket_salvo_used = True

            # Get all units in target hex and adjacent hexes
            target_pos = (action.target_q, action.target_r)
            affected_hexes = [target_pos]
            affected_hexes.extend(game_state.board.get_neighbors(*target_pos))

            # Get all units in affected hexes (including friendlies!)
            all_units = game_state.get_all_alive_units()
            affected_units = []
            for u_state in all_units:
                if u_state.position in affected_hexes:
                    if u_state.unit.unit_type in ('Soldier', 'Vehicle'):
                        affected_units.append(u_state)

            results_messages = [f"{attacker.name} Rocket Salvo centered on ({action.target_q}, {action.target_r}):"]
            salvo_results = []

            for affected_state in affected_units:
                affected_unit = affected_state.unit
                # 10 dice vs Soldier, 5 dice vs Vehicle
                salvo_dice = 10 if affected_unit.unit_type == 'Soldier' else 5

                # Get attack threshold
                affected_hex = game_state.board.get_hex(*affected_state.position)
                affected_terrain = affected_hex.terrain if affected_hex else 'open'
                attack_mods = self.ability_system.get_attack_modifiers(
                    attacker, affected_unit, action.distance, affected_terrain,
                    False, affected_state
                )
                threshold = attack_mods.get('hit_threshold', 4)

                # Roll dice
                rolls = [self.dice.roll_single_die() for _ in range(salvo_dice)]
                successes = sum(1 for r in rolls if r >= threshold)

                # Get defense
                defense_mods = self.ability_system.get_defense_modifiers(
                    affected_unit, attacker, 0, affected_terrain, False, affected_state
                )
                base_defense = defense_mods.get('defense', affected_unit.defense_front or 3)
                superior_armor = defense_mods.get('superior_armor', 0)
                hits = self.dice.calculate_hits(successes, base_defense, superior_armor)

                # Apply damage
                owner_str = "friendly" if affected_state.owner == attacker_state.owner else "enemy"
                unit_msg = f"  {affected_unit.name} ({owner_str} {affected_unit.unit_type}): {salvo_dice} dice, {successes} successes"

                if hits >= 2:
                    affected_state.is_destroyed = True
                    affected_state.is_alive = False
                    unit_msg += " - DESTROYED!"
                elif hits == 1:
                    if affected_unit.unit_type == 'Vehicle' and not affected_state.is_damaged:
                        affected_state.is_damaged = True
                        unit_msg += " - DAMAGED"
                    else:
                        affected_state.is_disrupted = True
                        unit_msg += " - DISRUPTED"
                else:
                    unit_msg += " - No effect"

                results_messages.append(unit_msg)
                salvo_results.append({
                    'unit': affected_unit.name,
                    'owner': owner_str,
                    'dice': salvo_dice,
                    'rolls': rolls,
                    'successes': successes,
                    'hits': hits
                })

            return ActionResult(
                True,
                "\n".join(results_messages),
                attack_result={'salvo_results': salvo_results}
            )

        # Check for Rockets 8 (once per game, 8 dice vs target within 4 hexes)
        is_rockets_8 = getattr(action, 'is_rockets_8', False)
        if is_rockets_8:
            if attacker_state.rockets_8_used:
                return ActionResult(False, "Rockets 8 already used this game")
            if target.unit_type not in ('Soldier', 'Vehicle'):
                return ActionResult(False, "Rockets 8 can only target Soldiers and Vehicles")
            if action.distance > 4:
                return ActionResult(False, "Rockets 8 can only target units within 4 hexes")
            attacker_state.rockets_8_used = True

            # Roll 8 attack dice
            attack_mods = self.ability_system.get_attack_modifiers(
                attacker, target, action.distance, target_terrain,
                is_rear_attack, target_state
            )
            threshold = attack_mods.get('hit_threshold', 4)
            attack_rolls = [self.dice.roll_single_die() for _ in range(8)]
            successes = sum(1 for r in attack_rolls if r >= threshold)

            # Get defense and calculate hits
            defense_mods = self.ability_system.get_defense_modifiers(
                target, attacker, action.distance, target_terrain,
                is_rear_attack, target_state
            )
            base_defense = defense_mods.get('defense', target.defense_front or 3)
            superior_armor = defense_mods.get('superior_armor', 0)
            hits = self.dice.calculate_hits(successes, base_defense, superior_armor)

            # Apply damage
            result_msg = f"{attacker.name} Rockets 8 vs {target.name}: {successes} successes from 8 dice"

            if hits >= 2:
                target_state.is_destroyed = True
                target_state.is_alive = False
                result_msg += " - TARGET DESTROYED!"
            elif hits == 1:
                if target.unit_type == 'Vehicle' and not target_state.is_damaged:
                    target_state.is_damaged = True
                    result_msg += " - Vehicle DAMAGED"
                else:
                    target_state.is_disrupted = True
                    result_msg += " - Target DISRUPTED"
            else:
                result_msg += " - No effect"

            return ActionResult(
                True,
                result_msg,
                attack_result={
                    'attack_rolls': attack_rolls,
                    'successes': successes,
                    'threshold': threshold,
                    'hits': hits
                }
            )

        # Check for Top-Mounted Rockets (once per game, area attack - 8 vs Soldier, 4 vs Vehicle)
        is_top_mounted_rockets = getattr(action, 'is_top_mounted_rockets', False)
        if is_top_mounted_rockets:
            if attacker_state.top_mounted_rockets_used:
                return ActionResult(False, "Top-Mounted Rockets already used this game")
            attacker_state.top_mounted_rockets_used = True

            # Get all units in target hex and adjacent hexes
            target_pos = (action.target_q, action.target_r)
            affected_hexes = [target_pos]
            affected_hexes.extend(game_state.board.get_neighbors(*target_pos))

            # Get all units in affected hexes (including friendlies!)
            all_units = game_state.get_all_alive_units()
            affected_units = []
            for u_state in all_units:
                if u_state.position in affected_hexes:
                    if u_state.unit.unit_type in ('Soldier', 'Vehicle'):
                        affected_units.append(u_state)

            results_messages = [f"{attacker.name} Top-Mounted Rockets centered on ({action.target_q}, {action.target_r}):"]
            rocket_results = []

            for affected_state in affected_units:
                affected_unit = affected_state.unit
                # 8 dice vs Soldier, 4 dice vs Vehicle
                rocket_dice = 8 if affected_unit.unit_type == 'Soldier' else 4

                # Get attack threshold (Top-Mounted Rockets ignores cover - already in ability_system)
                affected_hex = game_state.board.get_hex(*affected_state.position)
                affected_terrain = affected_hex.terrain if affected_hex else 'open'
                attack_mods = self.ability_system.get_attack_modifiers(
                    attacker, affected_unit, action.distance, affected_terrain,
                    False, affected_state
                )
                threshold = attack_mods.get('hit_threshold', 4)

                # Roll dice
                rolls = [self.dice.roll_single_die() for _ in range(rocket_dice)]
                successes = sum(1 for r in rolls if r >= threshold)

                # Get defense (no cover due to Top-Mounted Rockets)
                defense_mods = self.ability_system.get_defense_modifiers(
                    affected_unit, attacker, 0, affected_terrain, False, affected_state
                )
                base_defense = defense_mods.get('defense', affected_unit.defense_front or 3)
                superior_armor = defense_mods.get('superior_armor', 0)
                hits = self.dice.calculate_hits(successes, base_defense, superior_armor)

                # Apply damage
                owner_str = "friendly" if affected_state.owner == attacker_state.owner else "enemy"
                unit_msg = f"  {affected_unit.name} ({owner_str} {affected_unit.unit_type}): {rocket_dice} dice, {successes} successes"

                if hits >= 2:
                    affected_state.is_destroyed = True
                    affected_state.is_alive = False
                    unit_msg += " - DESTROYED!"
                elif hits == 1:
                    if affected_unit.unit_type == 'Vehicle' and not affected_state.is_damaged:
                        affected_state.is_damaged = True
                        unit_msg += " - DAMAGED"
                    else:
                        affected_state.is_disrupted = True
                        unit_msg += " - DISRUPTED"
                else:
                    unit_msg += " - No effect"

                results_messages.append(unit_msg)
                rocket_results.append({
                    'unit': affected_unit.name,
                    'owner': owner_str,
                    'dice': rocket_dice,
                    'rolls': rolls,
                    'successes': successes,
                    'hits': hits
                })

            return ActionResult(
                True,
                "\n".join(results_messages),
                attack_result={'rocket_results': rocket_results}
            )

        # Check for Additional Hull-Mounted Cannon (once per turn, 12/10/8 vs Vehicle in front)
        is_additional_hull_cannon = getattr(action, 'is_additional_hull_cannon', False)
        if is_additional_hull_cannon:
            if attacker_state.additional_hull_cannon_used:
                return ActionResult(False, "Additional Hull-Mounted Cannon already used this turn")
            if target.unit_type != 'Vehicle':
                return ActionResult(False, "Additional Hull-Mounted Cannon can only target Vehicles")
            attacker_state.additional_hull_cannon_used = True

            # Attack values: 12 (short), 10 (medium), 8 (long)
            if action.distance <= 2:
                hull_dice = 12
            elif action.distance <= 4:
                hull_dice = 10
            else:
                hull_dice = 8

            attack_mods = self.ability_system.get_attack_modifiers(
                attacker, target, action.distance, target_terrain,
                is_rear_attack, target_state
            )
            threshold = attack_mods.get('hit_threshold', 4)
            attack_rolls = [self.dice.roll_single_die() for _ in range(hull_dice)]
            successes = sum(1 for r in attack_rolls if r >= threshold)

            defense_mods = self.ability_system.get_defense_modifiers(
                target, attacker, action.distance, target_terrain,
                is_rear_attack, target_state
            )
            base_defense = defense_mods.get('defense', target.defense_front or 3)
            superior_armor = defense_mods.get('superior_armor', 0)
            hits = self.dice.calculate_hits(successes, base_defense, superior_armor)

            result_msg = f"{attacker.name} Additional Hull-Mounted Cannon vs {target.name}: {successes} successes from {hull_dice} dice"
            if hits >= 2:
                target_state.is_destroyed = True
                target_state.is_alive = False
                result_msg += " - TARGET DESTROYED!"
            elif hits == 1:
                if not target_state.is_damaged:
                    target_state.is_damaged = True
                    result_msg += " - Vehicle DAMAGED"
                else:
                    target_state.is_disrupted = True
                    result_msg += " - Vehicle DISRUPTED"
            else:
                result_msg += " - No effect"

            return ActionResult(True, result_msg, attack_result={
                'attack_rolls': attack_rolls, 'successes': successes,
                'threshold': threshold, 'hits': hits
            })

        # Check for Extra Hull-Mounted Cannon (once per turn, 8/7/5 vs Vehicle/Soldier in front)
        is_extra_hull_cannon = getattr(action, 'is_extra_hull_cannon', False)
        if is_extra_hull_cannon:
            if attacker_state.extra_hull_cannon_used:
                return ActionResult(False, "Extra Hull-Mounted Cannon already used this turn")
            if target.unit_type not in ('Soldier', 'Vehicle'):
                return ActionResult(False, "Extra Hull-Mounted Cannon can only target Soldiers and Vehicles")
            attacker_state.extra_hull_cannon_used = True

            # Attack values: 8 (short), 7 (medium), 5 (long)
            if action.distance <= 2:
                hull_dice = 8
            elif action.distance <= 4:
                hull_dice = 7
            else:
                hull_dice = 5

            attack_mods = self.ability_system.get_attack_modifiers(
                attacker, target, action.distance, target_terrain,
                is_rear_attack, target_state
            )
            threshold = attack_mods.get('hit_threshold', 4)
            attack_rolls = [self.dice.roll_single_die() for _ in range(hull_dice)]
            successes = sum(1 for r in attack_rolls if r >= threshold)

            defense_mods = self.ability_system.get_defense_modifiers(
                target, attacker, action.distance, target_terrain,
                is_rear_attack, target_state
            )
            base_defense = defense_mods.get('defense', target.defense_front or 3)
            superior_armor = defense_mods.get('superior_armor', 0)
            hits = self.dice.calculate_hits(successes, base_defense, superior_armor)

            result_msg = f"{attacker.name} Extra Hull-Mounted Cannon vs {target.name}: {successes} successes from {hull_dice} dice"
            if hits >= 2:
                target_state.is_destroyed = True
                target_state.is_alive = False
                result_msg += " - TARGET DESTROYED!"
            elif hits == 1:
                if target.unit_type == 'Vehicle' and not target_state.is_damaged:
                    target_state.is_damaged = True
                    result_msg += " - Vehicle DAMAGED"
                else:
                    target_state.is_disrupted = True
                    result_msg += " - Target DISRUPTED"
            else:
                result_msg += " - No effect"

            return ActionResult(True, result_msg, attack_result={
                'attack_rolls': attack_rolls, 'successes': successes,
                'threshold': threshold, 'hits': hits
            })

        # Check for Angriff or Banzai Charge attack bonus (+1 on each die)
        charge_attack_bonus = 0
        is_angriff = getattr(action, 'is_angriff', False)
        is_banzai_charge = getattr(action, 'is_banzai_charge', False)
        if is_angriff:
            charge_attack_bonus = -1  # -1 to threshold = +1 on each die
        elif is_banzai_charge:
            charge_attack_bonus = -1  # -1 to threshold = +1 on each die

        # Check for Armor-Piercing Rounds (once per game, declared before attack)
        is_armor_piercing = getattr(action, 'is_armor_piercing', False)

        # Resolve the attack
        # Check for Improvisation (using destroyed unit's attack values)
        improvised_attack = getattr(action, 'improvised_attack', None)

        result = self._resolve_attack_full(
            attacker, target,
            attacker_state, target_state,
            action.distance,
            target_terrain,
            is_rear_attack,
            attacker_same_hex,
            total_bonus_dice,
            bombs_dice,
            game_state,
            he_round_dice,
            charge_attack_bonus,
            is_armor_piercing,
            improvised_attack
        )

        # Check for Blast - attack all other units in target hex
        blast_results = []
        if self._has_blast(attacker):
            other_units = game_state.get_units_at_position(action.target_q, action.target_r)
            for other_state in other_units:
                if other_state.unit.id == target.id:
                    continue  # Skip the primary target
                if not other_state.is_alive:
                    continue
                if other_state.unit.unit_type == 'Aircraft':
                    continue  # Blast doesn't affect Aircraft

                # Resolve attack against this unit too
                other_result = self._resolve_attack_full(
                    attacker, other_state.unit,
                    attacker_state, other_state,
                    action.distance,
                    target_terrain,
                    is_rear_attack,
                    attacker_same_hex,
                    0,  # bonus_dice
                    0,  # bombs_dice
                    game_state
                )
                blast_results.append((other_state, other_result))
                result['notes'].append(f"Blast: {other_state.unit.name} - {other_result['outcome']}")

        # Apply results to game state
        game_state.mark_unit_attacked(action.unit_id, action.target_id)
        game_state.apply_action(action)

        # Enable Strike and Fade if attacker has the ability
        if self._has_strike_and_fade(attacker):
            attacker_state.strike_and_fade_available = True

        # Enable All Guns Blazing if attacker has the ability (only on first attack)
        is_all_guns_blazing_attack = getattr(action, 'is_all_guns_blazing', False)
        if is_all_guns_blazing_attack:
            # Consuming the All Guns Blazing bonus attack
            attacker_state.all_guns_blazing_available = False
        elif self._has_all_guns_blazing(attacker):
            # First attack enables the bonus attack
            attacker_state.all_guns_blazing_available = True

        # Enable Strafe if attacker has the ability (only on first attack)
        is_strafe_attack = getattr(action, 'is_strafe', False)
        if is_strafe_attack:
            # Consuming the Strafe bonus attack
            attacker_state.strafe_available = False
            attacker_state.strafe_target_hex = None
        elif self._has_strafe(attacker):
            # First attack enables the strafe attack, record target hex
            attacker_state.strafe_available = True
            attacker_state.strafe_target_hex = (action.target_q, action.target_r)

        # Extra Machine Guns: Mark as used when the extra attack is made
        is_extra_mg_attack = getattr(action, 'is_extra_mg', False)
        if is_extra_mg_attack:
            attacker_state.extra_mg_used = True

        # Firepower: Mark as used when the extra attack is made
        is_firepower_attack = getattr(action, 'is_firepower', False)
        if is_firepower_attack:
            attacker_state.firepower_used = True

        # Rapid Fire: Mark as used, check for jam (any 1s rolled causes sticky disruption)
        is_rapid_fire_attack = getattr(action, 'is_rapid_fire', False)
        if is_rapid_fire_attack:
            attacker_state.rapid_fire_used = True
            # Check if any 1s were rolled
            rolls = result.get('attack_rolls', [])
            if 1 in rolls:
                attacker_state.is_disrupted = True
                attacker_state.rapid_fire_jammed = True
                result['notes'].append("Rapid Fire: Rolled 1 - JAMMED! (sticky disruption)")

        # Overheat: If 3+ 1s rolled, unit is disrupted (sticky)
        attacker_abilities = getattr(attacker, 'abilities', []) or []
        has_overheat = any(a.lower() == 'overheat' for a in attacker_abilities)
        if has_overheat:
            rolls = result.get('attack_rolls', [])
            ones_count = sum(1 for r in rolls if r == 1)
            if ones_count >= 3:
                attacker_state.is_disrupted = True
                attacker_state.overheat_jammed = True
                result['notes'].append(f"Overheat: Rolled {ones_count} 1s - OVERHEATED! (sticky disruption)")

        # Unreliable: Any disruption on this unit is sticky
        has_unreliable = any(a.lower() == 'unreliable' for a in attacker_abilities)
        if has_unreliable and attacker_state.is_disrupted:
            attacker_state.unreliable_disrupted = True

        # Multiturreted: Track which arc was used for attack
        if self._has_multiturreted(attacker):
            # Determine if this was a front or rear arc attack based on target position
            if is_rear_attack:
                attacker_state.multiturreted_rear_used = True
            else:
                attacker_state.multiturreted_front_used = True

        # Command Quick Reactions: Vehicles within 2 hexes can change facing after attack
        if attacker.unit_type == 'Vehicle' and game_state:
            attacker_pos = attacker_state.position
            friendly_units = game_state.get_units_by_owner(attacker_state.owner)
            for friendly_state in friendly_units:
                if not friendly_state.is_alive or friendly_state.unit.id == attacker.id:
                    continue
                friendly_pos = friendly_state.position
                dist = game_state.board.hex_distance(
                    attacker_pos[0], attacker_pos[1], friendly_pos[0], friendly_pos[1]
                )
                if dist <= 2:
                    friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []
                    if any(a.lower() == 'command quick reactions' for a in friendly_abilities):
                        attacker_state.quick_reactions_available = True
                        result['notes'].append("Command Quick Reactions: Can change facing")
                        break

        # Covering Fire: Mark soldier targets as unable to make defensive fire this turn
        if result.get('covering_fire_applied', False):
            target_state.covering_fire_target = True

        # Apply Blast damage to other units in the target hex
        blast_destroyed_units = []
        for other_state, other_result in blast_results:
            if self.use_simultaneous_combat and other_result['hits'] > 0:
                # Record hits as pending
                self.casualty_system.record_hits(
                    other_state.unit.id,
                    other_state.unit.unit_type,
                    other_result['hits']
                )
            elif other_result['target_destroyed']:
                # Immediate mode: remove destroyed unit
                game_state.remove_unit(other_state.unit.id)
                blast_destroyed_units.append(other_state.unit.id)
            elif other_result.get('target_new_status'):
                # Immediate mode: apply status effects
                self._apply_status_to_unit_state(other_state, other_result['target_new_status'])

        # Store blast results for return
        result['blast_results'] = blast_results
        result['blast_destroyed'] = blast_destroyed_units

        # Handle damage based on simultaneous combat setting
        # Speed Boost bypasses simultaneous combat (resolves immediately)
        use_simultaneous = self.use_simultaneous_combat and not force_immediate_resolve
        if force_immediate_resolve:
            result['notes'].append("Speed Boost: Attack resolves immediately")

        if use_simultaneous and result['hits'] > 0:
            # Record hits as face-down counters (don't apply yet)
            counter_types = self.casualty_system.record_hits(
                action.target_id,
                target.unit_type,
                result['hits']
            )
            
            # Check if unit will be destroyed (for message purposes)
            will_destroy = self.casualty_system.unit_has_pending_destroyed(action.target_id)
            
            if will_destroy:
                message = f"{attacker.name} attacks {target.name} - {result['outcome'].upper()}! (pending)"
                result['pending_destroyed'] = True
            else:
                message = f"{attacker.name} attacks {target.name} - {result['outcome'].upper()} (pending)"
                result['pending_counters'] = [ct.value for ct in counter_types]
            
            return ActionResult(True, message, result['hits'], None, result)
        
        elif result['target_destroyed']:
            # Immediate mode: apply damage now
            game_state.remove_unit(action.target_id)
            message = f"{attacker.name} attacks {target.name} - {result['outcome'].upper()}!"
            return ActionResult(True, message, result['hits'], action.target_id, result)
        else:
            # Immediate mode: Update status flags on target
            new_status = result.get('target_new_status')
            if new_status:
                self._apply_status_to_unit_state(target_state, new_status)
            
            message = f"{attacker.name} attacks {target.name} - {result['outcome'].upper()}"
            return ActionResult(True, message, result['hits'], None, result)
    
    def _resolve_attack_full(self, attacker, target,
                            attacker_state: UnitState, target_state: UnitState,
                            distance: int, target_terrain: str,
                            is_rear_attack: bool, attacker_same_hex: bool,
                            bonus_dice: int = 0, bombs_dice: int = 0,
                            game_state: GameState = None,
                            he_round_dice: int = 0,
                            charge_attack_bonus: int = 0,
                            is_armor_piercing: bool = False,
                            improvised_attack: Dict = None) -> Dict:
        """
        Resolve an attack using authentic A&A Miniatures dice mechanics.
        
        Returns dictionary with full combat resolution details.
        """
        result = {
            'attacker': attacker.name,
            'target': target.name,
            'distance': distance,
            'outcome': 'miss',
            'hits': 0,
            'target_new_status': None,
            'target_destroyed': False,
            'attack_dice': 0,
            'attack_rolls': [],
            'successes': 0,
            'defense': 0,
            'cover_rolled': False,
            'cover_success': False,
            'notes': [],
            'is_armor_piercing_attack': is_armor_piercing
        }
        
        # Get attack modifiers from abilities
        attack_mods = self.ability_system.get_attack_modifiers(
            attacker, target, distance, target_terrain, is_rear_attack, target_state
        )
        
        if not attack_mods.get('can_attack', True):
            result['notes'].extend(attack_mods.get('notes', []))
            return result

        # Fixed Howitzer: Can only attack units in front
        if attack_mods.get('fixed_howitzer', False):
            # Check if attacker has facing (vehicles) and target is in front arc
            if attacker_state.facing is not None:
                attacker_facing = HexDirection(attacker_state.facing)
                attacker_pos = attacker_state.position
                target_pos = target_state.position
                is_front = is_front_arc_attack(target_pos, attacker_pos, attacker_facing)
                if not is_front:
                    result['notes'].append("Fixed Howitzer: Target not in front arc")
                    return result

        # Covering Fire: Track if this attack prevents defensive fire
        if attack_mods.get('prevents_defensive_fire', False):
            result['covering_fire_applied'] = True
        
        # Determine attack dice
        if he_round_dice > 0:
            # HE Round overrides normal attack dice (once per game vs Soldiers)
            attack_dice = he_round_dice
            result['notes'].append(f"HE Round: Using {he_round_dice} dice vs Soldier")
        elif bombs_dice > 0:
            # Bombs override normal attack dice
            attack_dice = bombs_dice
            result['notes'].append(f"Bombs: Using {bombs_dice} dice for special attack")
        else:
            attack_dice = self._get_attack_dice(
                attacker, target, distance, attack_mods, game_state, attacker_state,
                improvised_attack=improvised_attack
            )

            # Note Improvisation if used
            if improvised_attack:
                result['notes'].append(f"Improvisation: Using attack values from {improvised_attack.get('name', 'destroyed unit')}")

            # Apply Spotter bonus (passed from _execute_attack)
            if bonus_dice > 0:
                attack_dice += bonus_dice
                result['notes'].append(f"Bonus: +{bonus_dice} attack dice (Spotter/Coordinated Fire)")

            # Fury 2: +2 dice if enemy was destroyed last turn
            attacker_abilities_list = getattr(attacker, 'abilities', []) or []
            has_fury_2 = any(a.lower() == 'fury 2' for a in attacker_abilities_list)
            if has_fury_2 and game_state:
                enemy_owner = "player2" if attacker_state.owner == "player1" else "player1"
                if game_state.units_destroyed_last_turn.get(enemy_owner, []):
                    attack_dice += 2
                    result['notes'].append("Fury 2: +2 attack dice (enemy destroyed last turn)")

            # Check for aura abilities from adjacent friendly units
            if game_state and attacker.unit_type == 'Soldier':
                friendly_units = game_state.get_units_by_owner(attacker_state.owner)
                attacker_pos = attacker_state.position

                for friendly_state in friendly_units:
                    if not friendly_state.is_alive or friendly_state.unit.id == attacker.id:
                        continue
                    friendly_pos = friendly_state.position
                    dist_to_friendly = game_state.board.hex_distance(
                        attacker_pos[0], attacker_pos[1], friendly_pos[0], friendly_pos[1]
                    )
                    if dist_to_friendly > 1:
                        continue  # Not adjacent

                    friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []

                    # Tenacity: Adjacent Soldiers +1 die when attacking in same hex
                    if any(a.lower() == 'tenacity' for a in friendly_abilities):
                        if distance == 0:  # Same hex as target
                            attack_dice += 1
                            result['notes'].append("Tenacity: +1 attack die (attacking in same hex)")
                            break  # Only apply once

                    # Tides of War: Adjacent Soldiers +1 die if enemy destroyed last turn
                    if any(a.lower() == 'tides of war' for a in friendly_abilities):
                        enemy_owner = "player2" if attacker_state.owner == "player1" else "player1"
                        if game_state.units_destroyed_last_turn.get(enemy_owner, []):
                            attack_dice += 1
                            result['notes'].append("Tides of War: +1 attack die (enemy destroyed last turn)")
                            break  # Only apply once

                    # Paratrooper Commander: Adjacent Paratroopers +1 die
                    if any(a.lower() == 'paratrooper commander' for a in friendly_abilities):
                        attacker_is_paratrooper = any(
                            a.lower() == 'paratrooper' for a in attacker_abilities_list
                        )
                        if attacker_is_paratrooper:
                            attack_dice += 1
                            result['notes'].append("Paratrooper Commander: +1 attack die")
                            break  # Only apply once

                    # Pinpointer: Adjacent Soldiers give target -1 on cover rolls
                    if any(a.lower() == 'pinpointer' for a in friendly_abilities):
                        attack_mods['target_cover_penalty'] = attack_mods.get('target_cover_penalty', 0) + 1
                        result['notes'].append("Pinpointer: Target gets -1 on cover rolls")
                        break  # Only apply once

                    # Coordinated Fire (C. A.): +1 die when attacking units this commander already attacked
                    if any(a.lower() == 'coordinated fire (c. a.)' for a in friendly_abilities):
                        if target.id in friendly_state.targets_attacked_this_turn:
                            attack_dice += 1
                            result['notes'].append("Coordinated Fire (C.A.): +1 attack die (target already attacked)")
                            break  # Only apply once

            # Check for Command aura abilities affecting Vehicles
            if game_state and attacker.unit_type == 'Vehicle':
                friendly_units = game_state.get_units_by_owner(attacker_state.owner)
                attacker_pos = attacker_state.position
                command_leadership_applied = False
                command_ruthless_applied = False

                for friendly_state in friendly_units:
                    if not friendly_state.is_alive or friendly_state.unit.id == attacker.id:
                        continue
                    friendly_pos = friendly_state.position
                    dist_to_friendly = game_state.board.hex_distance(
                        attacker_pos[0], attacker_pos[1], friendly_pos[0], friendly_pos[1]
                    )
                    if dist_to_friendly > 2:
                        continue  # Must be within 2 hexes

                    friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []

                    # Command Leadership: Vehicles within 2 hexes get Well Led (+1 attack die)
                    if not command_leadership_applied:
                        if any(a.lower() == 'command leadership' for a in friendly_abilities):
                            attack_dice += 1
                            result['notes'].append("Command Leadership: +1 attack die (Well Led)")
                            command_leadership_applied = True

                    # Command Ruthless: Vehicles within 2 hexes get Ruthless (+1 on each die vs disrupted/damaged)
                    if not command_ruthless_applied:
                        if any(a.lower() == 'command ruthless' for a in friendly_abilities):
                            if target_state.is_disrupted or target_state.is_damaged:
                                # Ruthless gives +1 on each attack die = -1 to threshold
                                attack_mods['hit_modifier'] = attack_mods.get('hit_modifier', 0) - 1
                                result['notes'].append("Command Ruthless: +1 on each die vs disrupted/damaged")
                                command_ruthless_applied = True

        result['attack_dice'] = attack_dice
        
        if attack_dice <= 0:
            result['notes'].append("No attack value at this range")
            return result
        
        # Get attacker status
        attacker_disrupted = attacker_state.is_disrupted
        attacker_damaged = attacker_state.is_damaged
        
        # Get target status
        target_disrupted = target_state.is_disrupted
        target_damaged = target_state.is_damaged
        
        # Determine current target status
        if target_disrupted and target_damaged:
            current_status = UnitStatus.DISRUPTED_AND_DAMAGED
        elif target_damaged:
            current_status = UnitStatus.DAMAGED
        elif target_disrupted:
            current_status = UnitStatus.DISRUPTED
        else:
            current_status = UnitStatus.HEALTHY
        
        # Get target category
        target_category = get_unit_category(target)
        
        # Calculate defense
        if is_rear_attack:
            base_defense = getattr(target, 'defense_rear', getattr(target, 'defense_front', 3))
        else:
            base_defense = getattr(target, 'defense_front', 3)
        
        # Check if target has Fanatic (ignores disrupted counters)
        target_has_fanatic = any(
            a.lower() == 'fanatic' for a in (getattr(target, 'abilities', []) or [])
        )

        # Apply disrupted/damaged defense penalty (Fanatic ignores disrupted)
        effective_disrupted = target_disrupted and not target_has_fanatic
        if effective_disrupted or target_damaged:
            base_defense = max(1, base_defense - 1)
        if target_has_fanatic and target_disrupted:
            result['notes'].append("Fanatic: Ignores Disrupted penalty")

        # Get defense modifiers early (needed for cover rolls and defense bonuses)
        defense_mods = self.ability_system.get_defense_modifiers(
            target, target_terrain, is_rear_attack, attacker, distance,
            game_state=game_state, unit_state=target_state
        )

        # Entrenched: +1/+1 defense until unit moves
        if defense_mods.get('entrenched', False) and not target_state.has_moved:
            base_defense += 1
            result['notes'].append("Entrenched: +1/+1 defense (unit hasn't moved)")

        # Apply general defense bonuses from abilities
        base_defense += defense_mods.get('defense_bonus', 0)

        # Check for defensive aura abilities from adjacent friendly units
        if game_state:
            friendly_units = game_state.get_units_by_owner(target_state.owner)
            target_pos = target_state.position

            for friendly_state in friendly_units:
                if not friendly_state.is_alive or friendly_state.unit.id == target.id:
                    continue
                friendly_pos = friendly_state.position
                dist_to_friendly = game_state.board.hex_distance(
                    target_pos[0], target_pos[1], friendly_pos[0], friendly_pos[1]
                )
                if dist_to_friendly > 1:
                    continue  # Not adjacent

                friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []

                # Terrain Expert: Adjacent Soldiers +1/+1 defense vs long range
                if any(a.lower() == 'terrain expert' for a in friendly_abilities):
                    if target.unit_type == 'Soldier' and distance >= 5:
                        base_defense += 1
                        result['notes'].append("Terrain Expert: +1/+1 defense vs long range")
                        break  # Only apply once

                # Defensive Preparation: Adjacent Artillery +1/+1 defense
                if any(a.lower() == 'defensive preparation' for a in friendly_abilities):
                    if target.unit_type == 'Soldier':
                        target_abilities_check = getattr(target, 'abilities', []) or []
                        is_artillery = any('artillery' in a.lower() for a in target_abilities_check)
                        if is_artillery:
                            base_defense += 1
                            result['notes'].append("Defensive Preparation: +1/+1 defense (Artillery)")
                            break  # Only apply once

                # Dispersal: Adjacent Soldiers +1/+1 defense vs Blast
                if any(a.lower() == 'dispersal' for a in friendly_abilities):
                    if target.unit_type == 'Soldier':
                        attacker_abilities_check = getattr(attacker, 'abilities', []) or []
                        attacker_has_blast = any(a.lower() == 'blast' for a in attacker_abilities_check)
                        if attacker_has_blast:
                            base_defense += 1
                            result['notes'].append("Dispersal: +1/+1 defense vs Blast")
                            break  # Only apply once

        result['defense'] = base_defense

        # Check cover
        has_cover = target_terrain in self.COVER_TERRAIN
        ignore_cover = attack_mods.get('ignore_cover', False)

        # Low Silhouette: gets cover in clear terrain (succeeds on 6)
        if defense_mods.get('low_silhouette', False) and not has_cover:
            has_cover = True
            result['notes'].append("Low Silhouette: Gets cover roll in clear terrain")

        if ignore_cover:
            has_cover = False
            result['notes'].append("Attacker ignores cover")

        # Roll cover save if applicable
        cover_success = False
        if has_cover:
            # Check if target auto-fails cover (Tall Silhouette)
            if defense_mods.get('fails_cover_rolls', False):
                result['cover_rolled'] = True
                result['cover_success'] = False
                result['notes'].append("Tall Silhouette: Fails all cover rolls")
            # Backblast: If this unit attacks, it fails all cover rolls for the rest of the turn
            elif any(a.lower() == 'backblast' for a in (getattr(target, 'abilities', []) or [])) and target_state.has_attacked:
                result['cover_rolled'] = True
                result['cover_success'] = False
                result['notes'].append("Backblast: Fails cover rolls (unit has attacked this turn)")
            # Forest Camouflage: Auto-succeed cover vs long range attacks in forest
            elif defense_mods.get('auto_cover_success', False):
                result['cover_rolled'] = True
                result['cover_success'] = True
                cover_success = True
                result['notes'].append("Forest Camouflage: Auto-succeed cover roll")
            else:
                result['cover_rolled'] = True
                # Calculate cover modifier: defender's cover_bonus - attacker's target_cover_penalty
                cover_mod = defense_mods.get('cover_bonus', 0) - attack_mods.get('target_cover_penalty', 0)
                cover_result = self.dice.roll_cover_save(
                    target_category, attacker_same_hex, cover_mod
                )
                cover_success = cover_result.success
                result['cover_success'] = cover_success
                result['cover_roll'] = cover_result.roll
                result['cover_threshold'] = cover_result.threshold
                if cover_mod != 0:
                    result['notes'].append(f"Cover modifier: {cover_mod:+d}")

        # Roll attack (check if attacker has Fanatic - ignores disrupted)
        attacker_has_fanatic = any(
            a.lower() == 'fanatic' for a in (getattr(attacker, 'abilities', []) or [])
        )
        # Bravery Enforcement: Friendly disrupted Soldiers adjacent don't suffer -1 penalty
        has_bravery_enforcement = (
            attacker_disrupted and
            self._check_bravery_enforcement_bonus(game_state, attacker_state)
        )
        effective_attacker_disrupted = attacker_disrupted and not attacker_has_fanatic and not has_bravery_enforcement
        if attacker_has_fanatic and attacker_disrupted:
            result['notes'].append("Attacker Fanatic: Ignores Disrupted penalty")
        if has_bravery_enforcement:
            result['notes'].append("Bravery Enforcement: Adjacent unit ignores Disrupted penalty")

        attack_ability_mod = attack_mods.get('hit_modifier', 0)

        # Apply charge attack bonus (Angriff, Banzai Charge: +1 on each die)
        if charge_attack_bonus != 0:
            attack_ability_mod += charge_attack_bonus
            if charge_attack_bonus < 0:
                result['notes'].append("Charge Attack: +1 on each attack die")

        # Slow: Enemy Aircraft get +1 on each attack die (easier to hit)
        if defense_mods.get('slow_vs_aircraft', False):
            attack_ability_mod -= 1  # -1 means easier to hit (3+ instead of 4+)
            result['notes'].append("Slow: Attacker (Aircraft) gets +1 on attack dice")

        # Open Crew: If within 2 hexes of an enemy Soldier, -1 on each attack die
        attacker_abilities = getattr(attacker, 'abilities', []) or []
        has_open_crew = any(a.lower() == 'open crew' for a in attacker_abilities)
        if has_open_crew and game_state:
            enemy_owner = "player2" if attacker_state.owner == "player1" else "player1"
            enemy_units = game_state.get_units_by_owner(enemy_owner)
            attacker_pos = attacker_state.position
            for enemy_state in enemy_units:
                if not enemy_state.is_alive:
                    continue
                if enemy_state.unit.unit_type != 'Soldier':
                    continue
                enemy_pos = enemy_state.position
                dist = game_state.board.hex_distance(
                    attacker_pos[0], attacker_pos[1], enemy_pos[0], enemy_pos[1]
                )
                if dist <= 2:
                    attack_ability_mod += 1  # +1 means harder to hit
                    result['notes'].append("Open Crew: -1 on attack dice (enemy Soldier within 2 hexes)")
                    break

        # Shock Troop: For first attack of the game vs Soldier or Vehicle, +1 on each die
        attacker_abilities_list = getattr(attacker, 'abilities', []) or []
        has_shock_troop = any(a.lower() == 'shock troop' for a in attacker_abilities_list)
        if has_shock_troop and not attacker_state.shock_troop_used:
            if target.unit_type in ('Soldier', 'Vehicle'):
                attack_ability_mod -= 1  # -1 means easier to hit
                result['notes'].append("Shock Troop: +1 on each attack die (first attack)")
                attacker_state.shock_troop_used = True  # Mark as used

        attack_result = self.dice.roll_attack(
            attack_dice, effective_attacker_disrupted, attacker_damaged, attack_ability_mod
        )

        # Guard Crew: After rolling, may reroll a single die result of 1
        attacker_abilities = getattr(attacker, 'abilities', []) or []
        has_guard_crew = any(a.lower() == 'guard crew' for a in attacker_abilities)
        if has_guard_crew and 1 in attack_result.rolls:
            # Reroll one 1
            old_rolls = list(attack_result.rolls)
            first_one_idx = old_rolls.index(1)
            new_roll = self.dice.dice_system.roll_d6() if hasattr(self.dice, 'dice_system') else self.dice.roll_d6()
            old_rolls[first_one_idx] = new_roll
            # Recalculate successes
            threshold = attack_result.hit_threshold
            new_successes = sum(1 for r in old_rolls if r >= threshold)
            attack_result.rolls = old_rolls
            attack_result.successes = new_successes
            result['notes'].append(f"Guard Crew: Rerolled 1 → {new_roll}")

        # Veteran Guard: May reroll up to two die results of 1
        has_veteran_guard = any(a.lower() == 'veteran guard' for a in attacker_abilities)
        if has_veteran_guard and 1 in attack_result.rolls:
            old_rolls = list(attack_result.rolls)
            rerolled = 0
            for i, roll in enumerate(old_rolls):
                if roll == 1 and rerolled < 2:
                    new_roll = self.dice.roll_single_die()
                    old_rolls[i] = new_roll
                    rerolled += 1
            if rerolled > 0:
                threshold = attack_result.hit_threshold
                new_successes = sum(1 for r in old_rolls if r >= threshold)
                attack_result.rolls = old_rolls
                attack_result.successes = new_successes
                result['notes'].append(f"Veteran Guard: Rerolled {rerolled} ones")

        # Hardened Veteran: Reroll one attack die (any die, pick lowest)
        has_hardened_veteran = any(a.lower() == 'hardened veteran' for a in attacker_abilities)
        if has_hardened_veteran and attack_result.rolls:
            old_rolls = list(attack_result.rolls)
            # Find the lowest non-success die to reroll
            threshold = attack_result.hit_threshold
            lowest_idx = None
            lowest_val = 7
            for i, roll in enumerate(old_rolls):
                if roll < threshold and roll < lowest_val:
                    lowest_val = roll
                    lowest_idx = i
            if lowest_idx is not None:
                new_roll = self.dice.roll_single_die()
                old_val = old_rolls[lowest_idx]
                old_rolls[lowest_idx] = new_roll
                new_successes = sum(1 for r in old_rolls if r >= threshold)
                attack_result.rolls = old_rolls
                attack_result.successes = new_successes
                result['notes'].append(f"Hardened Veteran: Rerolled {old_val} → {new_roll}")

        # Lead the Way: Once per turn, reroll a single attack die (pick lowest non-success)
        has_lead_the_way = any(a.lower() == 'lead the way' for a in attacker_abilities)
        if has_lead_the_way and not attacker_state.lead_the_way_used and attack_result.rolls:
            old_rolls = list(attack_result.rolls)
            threshold = attack_result.hit_threshold
            # Find lowest non-success die to reroll
            non_success_indices = [(i, r) for i, r in enumerate(old_rolls) if r < threshold]
            if non_success_indices:
                lowest_idx, old_val = min(non_success_indices, key=lambda x: x[1])
                new_roll = self.dice.roll_single_die()
                old_rolls[lowest_idx] = new_roll
                new_successes = sum(1 for r in old_rolls if r >= threshold)
                attack_result.rolls = old_rolls
                attack_result.successes = new_successes
                attacker_state.lead_the_way_used = True
                result['notes'].append(f"Lead the Way: Rerolled {old_val} → {new_roll}")

        # Plentiful Ammo: Friendly unit with this ability allows reroll of 1s
        # Check if there's a friendly unit with Plentiful Ammo
        if game_state and attacker.unit_type in ('Soldier', 'Vehicle') and 1 in attack_result.rolls:
            friendly_units = game_state.get_units_by_owner(attacker_state.owner)
            has_plentiful_ammo_nearby = False
            for friendly_state in friendly_units:
                if not friendly_state.is_alive:
                    continue
                friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []
                if any(a.lower() == 'plentiful ammo' for a in friendly_abilities):
                    has_plentiful_ammo_nearby = True
                    break
            if has_plentiful_ammo_nearby:
                # Reroll all 1s (each die only once)
                old_rolls = list(attack_result.rolls)
                rerolled_count = 0
                for i, roll in enumerate(old_rolls):
                    if roll == 1:
                        new_roll = self.dice.roll_single_die()
                        old_rolls[i] = new_roll
                        rerolled_count += 1
                if rerolled_count > 0:
                    threshold = attack_result.hit_threshold
                    new_successes = sum(1 for r in old_rolls if r >= threshold)
                    attack_result.rolls = old_rolls
                    attack_result.successes = new_successes
                    result['notes'].append(f"Plentiful Ammo: Rerolled {rerolled_count} ones")

        result['attack_rolls'] = attack_result.rolls
        result['successes'] = attack_result.successes
        result['hit_threshold'] = attack_result.hit_threshold

        # Apply Shrapnel (double successes vs Soldiers)
        effective_successes = attack_result.successes
        if attack_mods.get('shrapnel', False):
            effective_successes = attack_result.successes * 2
            result['notes'].append(f"Shrapnel: {attack_result.successes} → {effective_successes} successes")
            result['successes'] = effective_successes

        # Jet: Each 6 rolled against Aircraft counts as two successes
        if attack_mods.get('jet_vs_aircraft', False):
            sixes_rolled = sum(1 for r in attack_result.rolls if r == 6)
            if sixes_rolled > 0:
                # Each 6 counts as 2 successes instead of 1
                extra_successes = sixes_rolled  # Already counted once, add one more per 6
                effective_successes += extra_successes
                result['notes'].append(f"Jet: {sixes_rolled} sixes = +{extra_successes} extra successes vs Aircraft")
                result['successes'] = effective_successes

        # Vast Experience: Each 6 counts as two successes
        attacker_abilities = getattr(attacker, 'abilities', []) or []
        has_vast_experience = any(a.lower() == 'vast experience' for a in attacker_abilities)
        if has_vast_experience:
            sixes_rolled = sum(1 for r in attack_result.rolls if r == 6)
            if sixes_rolled > 0:
                # Each 6 counts as 2 successes instead of 1
                extra_successes = sixes_rolled  # Already counted once, add one more per 6
                effective_successes += extra_successes
                result['notes'].append(f"Vast Experience: {sixes_rolled} sixes = +{extra_successes} extra successes")
                result['successes'] = effective_successes

        # Sirens: +1 success vs Soldiers (only counts for disruption, not destruction)
        has_sirens = any(a.lower() == 'sirens' for a in attacker_abilities)
        sirens_bonus = 0
        if has_sirens and target.unit_type == 'Soldier':
            sirens_bonus = 1
            result['sirens_bonus'] = sirens_bonus
            result['notes'].append("Sirens: +1 success vs Soldier (disruption only)")

        # Overheat: If 3+ 1s rolled, attacker gets disrupted (not removed at next casualty phase)
        has_overheat = any(a.lower() == 'overheat' for a in attacker_abilities)
        if has_overheat:
            ones_rolled = sum(1 for r in attack_result.rolls if r == 1)
            if ones_rolled >= 3:
                attacker_state.is_disrupted = True
                attacker_state.overheat_jammed = True  # Special flag - not removed at casualty phase
                result['notes'].append(f"Overheat: {ones_rolled} ones rolled - ATTACKER DISRUPTED!")

        # Flamethrower: If 3+ 6s on short-range attack, target destroyed immediately
        if attack_mods.get('flamethrower', False):
            sixes_rolled = sum(1 for r in attack_result.rolls if r == 6)
            if sixes_rolled >= 3 and target.unit_type not in ('Aircraft', 'Obstacle'):
                result['notes'].append(f"Flamethrower: {sixes_rolled} sixes rolled - TARGET DESTROYED!")
                result['hits'] = 99  # Instant kill
                result['target_destroyed'] = True
                result['outcome'] = 'destroyed'
                return result

        # defense_mods already fetched earlier for cover rolls
        superior_armor = defense_mods.get('superior_armor', 0)
        if superior_armor > 0:
            result['notes'].append(f"Target has Superior Armor/Steely Resolve {superior_armor}")

        # Calculate hits (accounting for Superior Armor)
        hits = self.dice.calculate_hits(effective_successes, base_defense, superior_armor)

        # Armor-Piercing Rounds: If declared and 2 hits scored vs Vehicle, score additional hit
        # This is checked via the is_armor_piercing_attack flag passed from action
        is_ap_attack = result.get('is_armor_piercing_attack', False)
        if is_ap_attack and target.unit_type == 'Vehicle':
            if hits >= 2:
                hits += 1
                result['notes'].append("Armor-Piercing Rounds: +1 hit (scored 2 hits vs Vehicle)")
            attacker_state.armor_piercing_used = True

        result['hits'] = hits

        # Sirens special handling: If hits would be 0 but Sirens bonus applies, check for disruption
        sirens_bonus = result.get('sirens_bonus', 0)
        if hits == 0 and sirens_bonus > 0:
            # Check if successes + sirens would reach defense threshold
            total_with_sirens = effective_successes + sirens_bonus
            if total_with_sirens >= base_defense:
                # Sirens grants 1 "hit" but only for disruption
                result['notes'].append("Sirens: Extra success causes Disruption only")
                # Manually set the outcome to disruption
                target_state.is_disrupted = True
                result['outcome'] = 'disrupted'
                result['target_new_status'] = UnitStatus.DISRUPTED
                return result

        if hits == 0:
            result['outcome'] = 'miss'
            result['notes'].append(f"Scored {attack_result.successes} successes, needed {base_defense}")
            return result

        # Resolve damage
        damage_result = self.dice.resolve_damage(
            hits, target_category, current_status, cover_success
        )

        # Heavy Armor: Ignore the first Damaged counter this unit receives each game
        final_new_status = damage_result.new_status
        target_has_heavy_armor = any(
            a.lower() == 'heavy armor' for a in (getattr(target, 'abilities', []) or [])
        )
        if target_has_heavy_armor and not target_state.heavy_armor_used:
            # Check if Damaged counter would be placed
            if damage_result.new_status == UnitStatus.DAMAGED:
                # Ignore the Damaged counter entirely
                final_new_status = UnitStatus.HEALTHY
                result['notes'].append("Heavy Armor: Ignored first Damaged counter this game")
                target_state.heavy_armor_used = True
            elif damage_result.new_status == UnitStatus.DISRUPTED_AND_DAMAGED:
                # Reduce to just Disrupted
                final_new_status = UnitStatus.DISRUPTED
                result['notes'].append("Heavy Armor: Ignored Damaged counter, still Disrupted")
                target_state.heavy_armor_used = True
            elif damage_result.new_status == UnitStatus.DESTROYED and target_category == UnitCategory.VEHICLE:
                # For vehicles, destroyed = Damaged + Damaged. Heavy Armor blocks first, so reduce to just Damaged
                final_new_status = UnitStatus.DAMAGED
                result['notes'].append("Heavy Armor: Reduced destruction to Damaged")
                target_state.heavy_armor_used = True

        # Fire Hazard: When receiving a Damaged counter, roll a die. On 5+, destroy.
        target_has_fire_hazard = any(
            a.lower() == 'fire hazard' for a in (getattr(target, 'abilities', []) or [])
        )
        if target_has_fire_hazard and final_new_status != UnitStatus.DESTROYED:
            # Check if this attack would place a Damaged counter
            would_be_damaged = final_new_status in [UnitStatus.DAMAGED, UnitStatus.DISRUPTED_AND_DAMAGED]
            was_already_damaged = target_state.is_damaged
            if would_be_damaged and not was_already_damaged:
                fire_hazard_roll = self.dice.roll_single_die()
                result['notes'].append(f"Fire Hazard check: rolled {fire_hazard_roll}")
                if fire_hazard_roll >= 5:
                    final_new_status = UnitStatus.DESTROYED
                    result['notes'].append("Fire Hazard: Unit destroyed on receiving Damaged counter!")

        # Highly Flammable: When receiving a Damaged counter, roll a die. On 3+, destroy.
        target_has_highly_flammable = any(
            a.lower() == 'highly flammable' for a in (getattr(target, 'abilities', []) or [])
        )
        if target_has_highly_flammable and final_new_status != UnitStatus.DESTROYED:
            would_be_damaged = final_new_status in [UnitStatus.DAMAGED, UnitStatus.DISRUPTED_AND_DAMAGED]
            was_already_damaged = target_state.is_damaged
            if would_be_damaged and not was_already_damaged:
                highly_flammable_roll = self.dice.roll_single_die()
                result['notes'].append(f"Highly Flammable check: rolled {highly_flammable_roll}")
                if highly_flammable_roll >= 3:
                    final_new_status = UnitStatus.DESTROYED
                    result['notes'].append("Highly Flammable: Unit destroyed on receiving Damaged counter!")

        # Flammable: Whenever attacked, roll a die. On 5+, destroy immediately.
        target_has_flammable = any(
            a.lower() == 'flammable' for a in (getattr(target, 'abilities', []) or [])
        )
        if target_has_flammable and final_new_status != UnitStatus.DESTROYED:
            flammable_roll = self.dice.roll_single_die()
            result['notes'].append(f"Flammable check: rolled {flammable_roll}")
            if flammable_roll >= 5:
                final_new_status = UnitStatus.DESTROYED
                result['notes'].append("Flammable: Unit destroyed!")

        # Endurance: "When this unit receives a face-up Destroyed counter, roll 5+ to ignore"
        target_has_endurance = any(
            a.lower() == 'endurance' for a in (getattr(target, 'abilities', []) or [])
        )
        if target_has_endurance and final_new_status == UnitStatus.DESTROYED:
            endurance_roll = self.dice.roll_single_die()
            result['notes'].append(f"Endurance check: rolled {endurance_roll}")
            if endurance_roll >= 5:
                # Ignore the Destroyed counter - revert to previous worst state
                if target_state.is_damaged:
                    final_new_status = UnitStatus.DAMAGED
                elif target_state.is_disrupted:
                    final_new_status = UnitStatus.DISRUPTED
                else:
                    final_new_status = UnitStatus.HEALTHY
                result['notes'].append("Endurance: Ignored Destroyed counter!")

        # Lack of Determination: If this unit gets a face-up Disrupted counter, destroy immediately
        target_abilities = getattr(target, 'abilities', []) or []
        has_lack_of_determination = any(
            a.lower() == 'lack of determination' for a in target_abilities
        )
        if has_lack_of_determination and final_new_status in [
            UnitStatus.DISRUPTED, UnitStatus.DISRUPTED_AND_DAMAGED
        ]:
            final_new_status = UnitStatus.DESTROYED
            result['notes'].append("Lack of Determination: Unit destroyed upon disruption!")

        result['target_new_status'] = final_new_status
        result['target_destroyed'] = final_new_status == UnitStatus.DESTROYED
        result['status_change'] = damage_result.status_change
        result['counters_placed'] = damage_result.counters_placed

        # Set outcome
        if final_new_status == UnitStatus.DESTROYED:
            result['outcome'] = 'destroyed'
        elif final_new_status == UnitStatus.DISRUPTED_AND_DAMAGED:
            result['outcome'] = 'disrupted_and_damaged'
        elif final_new_status == UnitStatus.DAMAGED:
            result['outcome'] = 'damaged'
        elif final_new_status == UnitStatus.DISRUPTED:
            result['outcome'] = 'disrupted'
        else:
            result['outcome'] = 'no_effect'
        
        return result
    
    def _get_attack_dice(self, attacker, target, distance: int,
                        ability_mods: Dict = None,
                        game_state: GameState = None,
                        attacker_state: UnitState = None,
                        improvised_attack: Dict = None) -> int:
        """Get number of attack dice based on target type and range.

        If improvised_attack is provided, use those attack values instead of the attacker's.
        This is used for the Improvisation ability.
        """
        ability_mods = ability_mods or {}

        # Use improvised attack values if provided
        attack_source = attacker
        if improvised_attack:
            # Create a pseudo-attacker object with the improvised values
            class ImprovisedAttacker:
                pass
            attack_source = ImprovisedAttacker()
            attack_source.unit_type = improvised_attack.get('unit_type', 'Soldier')
            attack_source.veh_short = improvised_attack.get('attack_versus_vehicle', 0) or improvised_attack.get('attack_close', 0)
            attack_source.veh_medium = improvised_attack.get('attack_versus_vehicle', 0) or improvised_attack.get('attack_medium', 0)
            attack_source.veh_long = improvised_attack.get('attack_versus_vehicle', 0) or improvised_attack.get('attack_long', 0)
            attack_source.inf_short = improvised_attack.get('attack_versus_soldier', 0) or improvised_attack.get('attack_close', 0)
            attack_source.inf_medium = improvised_attack.get('attack_versus_soldier', 0) or improvised_attack.get('attack_medium', 0)
            attack_source.inf_long = improvised_attack.get('attack_versus_soldier', 0) or improvised_attack.get('attack_long', 0)

        # Close Assault override (vs Vehicles in same hex)
        if ability_mods.get('close_assault_dice') and distance == 0:
            return ability_mods['close_assault_dice']

        # Hand to Hand override (vs Soldiers in same hex)
        if ability_mods.get('hand_to_hand_dice') and distance == 0:
            return ability_mods['hand_to_hand_dice']

        # Determine range category
        from movement import MovementSystem

        # Check target type
        target_type = getattr(target, 'unit_type', 'Soldier')

        # Check for Improved Accuracy aura (adjacent Soldiers have medium range 2-6)
        has_improved_accuracy = False
        if game_state and attacker_state and attacker.unit_type == 'Soldier':
            attacker_pos = attacker_state.position
            friendly_units = game_state.get_units_by_owner(attacker_state.owner)
            for friendly_state in friendly_units:
                if not friendly_state.is_alive or friendly_state.unit.id == attacker.id:
                    continue
                friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []
                if any(a.lower() == 'improved accuracy' for a in friendly_abilities):
                    friendly_pos = friendly_state.position
                    dist = game_state.board.hex_distance(
                        attacker_pos[0], attacker_pos[1], friendly_pos[0], friendly_pos[1]
                    )
                    if dist <= 1:  # Adjacent
                        has_improved_accuracy = True
                        break

        # Agility: Modified range categories vs Aircraft (short 0-2, medium 3-5, long 6+)
        if ability_mods.get('agility_range', False) and target_type == 'Aircraft':
            if distance <= 2:
                range_category = 'short'
            elif distance <= 5:
                range_category = 'medium'
            else:
                range_category = 'long'
        elif has_improved_accuracy:
            # Improved Accuracy: medium range is 2-6 instead of 2-4
            if distance <= 1:
                range_category = 'short'
            elif distance <= 6:
                range_category = 'medium'
            else:
                range_category = 'long'
        else:
            range_category = MovementSystem.get_range_category(distance)
        is_vehicle = target_type == 'Vehicle'

        # Dismounted Attack: short range vs Vehicles is 0-2 hexes when no adjacent enemy Soldiers
        if ability_mods.get('dismounted_attack', False) and is_vehicle and distance <= 2:
            # Check for adjacent enemy Soldiers
            has_adjacent_enemy_soldier = False
            if game_state and attacker_state:
                enemy_owner = "player2" if attacker_state.owner == "player1" else "player1"
                enemy_units = game_state.get_units_by_owner(enemy_owner)
                attacker_pos = attacker_state.position
                for enemy_state in enemy_units:
                    if not enemy_state.is_alive or enemy_state.unit.unit_type != 'Soldier':
                        continue
                    dist_to_enemy = game_state.board.hex_distance(
                        attacker_pos[0], attacker_pos[1],
                        enemy_state.position[0], enemy_state.position[1]
                    )
                    if dist_to_enemy <= 1:
                        has_adjacent_enemy_soldier = True
                        break
            if not has_adjacent_enemy_soldier:
                range_category = 'short'

        # Open Back: use anti-Soldier values when attacking rear of Open Back vehicle
        use_soldier_values = ability_mods.get('use_anti_soldier_values', False)

        # Extended Range X: long range vs Vehicles is 5-X hexes
        # Enhanced Range X: long range (all targets) is 5-X hexes
        extended_range_vehicles = ability_mods.get('extended_range_vehicles', 0)
        enhanced_range = ability_mods.get('enhanced_range', 0)

        if is_vehicle and not use_soldier_values:
            if range_category == 'short':
                dice = getattr(attack_source, 'veh_short', 0)
            elif range_category == 'medium':
                dice = getattr(attack_source, 'veh_medium', 0)
            else:
                # Long range - check Extended Range limit for vehicles
                if extended_range_vehicles > 0 and distance > extended_range_vehicles:
                    dice = 0  # Beyond extended range
                elif enhanced_range > 0 and distance > enhanced_range:
                    dice = 0  # Beyond enhanced range
                else:
                    dice = getattr(attack_source, 'veh_long', 0)
        else:
            if range_category == 'short':
                dice = getattr(attack_source, 'inf_short', getattr(attack_source, 'per_short', 0))
            elif range_category == 'medium':
                dice = getattr(attack_source, 'inf_medium', getattr(attack_source, 'per_medium', 0))
            else:
                # Long range - check Enhanced Range limit
                if enhanced_range > 0 and distance > enhanced_range:
                    dice = 0  # Beyond enhanced range
                else:
                    dice = getattr(attack_source, 'inf_long', getattr(attack_source, 'per_long', 0))
        
        # Apply bonus dice from abilities
        dice += ability_mods.get('bonus_dice', 0)
        
        return max(0, dice)
    
    def _apply_status_to_unit_state(self, unit_state: UnitState, new_status: UnitStatus):
        """Apply a UnitStatus to a UnitState object"""
        if new_status == UnitStatus.DISRUPTED:
            unit_state.is_disrupted = True
        elif new_status == UnitStatus.DAMAGED:
            unit_state.is_damaged = True
        elif new_status == UnitStatus.DISRUPTED_AND_DAMAGED:
            unit_state.is_disrupted = True
            unit_state.is_damaged = True
        elif new_status == UnitStatus.DESTROYED:
            unit_state.current_health = 0
    
    def _execute_move_and_attack(self, game_state: GameState, 
                                 action: MoveAndAttackAction) -> ActionResult:
        """Execute a combined move-and-attack action"""
        unit_state = game_state.get_unit_state(action.unit_id)
        if not unit_state:
            return ActionResult(False, f"Unit {action.unit_id} not found")
        
        unit = unit_state.unit
        
        # Validate the combined action
        validation = self.validator.validate_move_and_attack(action, unit, game_state)
        if not validation:
            return ActionResult(False, f"Invalid move-and-attack: {validation.reason}")
        
        # Execute move first
        move_result = self._execute_move(game_state, action.move_action)
        if not move_result:
            return move_result
        
        # Then execute attack from new position
        attack_result = self._execute_attack(game_state, action.attack_action)
        
        combined_message = f"{move_result.message}, then {attack_result.message}"
        return ActionResult(
            attack_result.success,
            combined_message,
            attack_result.hits,
            attack_result.unit_destroyed,
            attack_result.combat_details
        )
    
    def _execute_ability(self, game_state: GameState,
                        action: UseAbilityAction) -> ActionResult:
        """Execute an ability use action"""
        unit_state = game_state.get_unit_state(action.unit_id)
        if not unit_state:
            return ActionResult(False, f"Unit {action.unit_id} not found")

        unit = unit_state.unit

        # Handle Smoke Screen ability
        if action.ability_name.lower() == 'smoke screen':
            # Check once per game
            if unit_state.smoke_screen_used:
                return ActionResult(False, "Smoke Screen already used this game")
            # Place smoke screen in unit's hex
            q, r = unit_state.position
            game_state.add_smoke(q, r)
            unit_state.smoke_screen_used = True
            game_state.mark_ability_used(action.unit_id, action.ability_name)
            game_state.apply_action(action)
            return ActionResult(
                True,
                f"{unit.name} deploys Smoke Screen at ({q},{r})"
            )

        # Handle Demolitions ability
        if action.ability_name.lower() == 'demolitions':
            if not action.target_id:
                return ActionResult(False, "Demolitions requires a target obstacle")
            target_state = game_state.get_unit_state(action.target_id)
            if not target_state:
                return ActionResult(False, "Target obstacle not found")
            if target_state.unit.unit_type != 'Obstacle':
                return ActionResult(False, "Demolitions can only target Obstacles")

            # Roll a die - on 4+, destroy the obstacle
            roll = self.dice.roll_single_die()
            if roll >= 4:
                # Destroy the obstacle
                game_state.remove_unit(action.target_id)
                game_state.mark_ability_used(action.unit_id, action.ability_name)
                game_state.apply_action(action)
                return ActionResult(
                    True,
                    f"{unit.name} uses Demolitions on {target_state.unit.name}: rolled {roll}, DESTROYED!"
                )
            else:
                game_state.mark_ability_used(action.unit_id, action.ability_name)
                game_state.apply_action(action)
                return ActionResult(
                    True,
                    f"{unit.name} uses Demolitions on {target_state.unit.name}: rolled {roll}, failed (need 4+)"
                )

        # Handle Command Demolition ability (granted by adjacent commander)
        if action.ability_name.lower() == 'command_demolition':
            if not action.target_id:
                return ActionResult(False, "Command Demolition requires a target obstacle")
            target_state = game_state.get_unit_state(action.target_id)
            if not target_state:
                return ActionResult(False, "Target obstacle not found")
            if target_state.unit.unit_type != 'Obstacle':
                return ActionResult(False, "Command Demolition can only target Obstacles")
            if unit.unit_type != 'Soldier':
                return ActionResult(False, "Only Soldiers can use Command Demolition")

            # Roll a die - on 4+, destroy the obstacle
            roll = self.dice.roll_single_die()
            # Mark that this unit has used its assault action (can't attack this turn)
            game_state.mark_unit_attacked(action.unit_id)
            if roll >= 4:
                # Destroy the obstacle
                game_state.remove_unit(action.target_id)
                game_state.apply_action(action)
                return ActionResult(
                    True,
                    f"{unit.name} uses Command Demolition on {target_state.unit.name}: rolled {roll}, DESTROYED!"
                )
            else:
                game_state.apply_action(action)
                return ActionResult(
                    True,
                    f"{unit.name} uses Command Demolition on {target_state.unit.name}: rolled {roll}, failed (need 4+)"
                )

        # Handle Bridge Demolition ability
        if action.ability_name.lower() == 'bridge_demolition':
            # Check if unit has Bridge Demolition ability
            unit_abilities = getattr(unit, 'abilities', []) or []
            has_bridge_demo = any('bridge demolition' in a.lower() for a in unit_abilities)
            if not has_bridge_demo:
                return ActionResult(False, "Unit does not have Bridge Demolition ability")

            # Check for edge obstacle demolition
            if action.parameters and action.parameters.get('edge_obstacle'):
                edge_target = action.parameters['edge_obstacle']
                q, r = unit_state.position

                # Roll for demolition
                roll = self.dice.roll_single_die()
                game_state.mark_unit_attacked(action.unit_id)

                if roll >= 4:
                    obstacle_type = action.parameters.get('obstacle_type', 'obstacle')
                    # Replace the bridge/obstacle with a stream crossing requirement
                    # (units now need movement roll to cross)
                    game_state.board.remove_edge_obstacle(q, r, edge_target[0], edge_target[1])
                    game_state.board.add_edge_obstacle(q, r, edge_target[0], edge_target[1], 'destroyed_bridge')
                    game_state.apply_action(action)
                    return ActionResult(
                        True,
                        f"{unit.name} uses Bridge Demolition: rolled {roll}, {obstacle_type} DESTROYED! (units now need movement roll to cross)"
                    )
                else:
                    game_state.apply_action(action)
                    return ActionResult(
                        True,
                        f"{unit.name} uses Bridge Demolition: rolled {roll}, failed (need 4+)"
                    )
            else:
                # Standard obstacle demolition (unit in hex)
                if not action.target_id:
                    return ActionResult(False, "Bridge Demolition requires a target obstacle")

                target_state = game_state.get_unit_state(action.target_id)
                if not target_state:
                    return ActionResult(False, "Target obstacle not found")
                if target_state.unit.unit_type != 'Obstacle':
                    return ActionResult(False, "Bridge Demolition can only target Obstacles")

                # Roll for demolition
                roll = self.dice.roll_single_die()
                game_state.mark_unit_attacked(action.unit_id)

                if roll >= 4:
                    # Destroy the obstacle
                    game_state.remove_unit(action.target_id)
                    game_state.apply_action(action)
                    return ActionResult(
                        True,
                        f"{unit.name} uses Bridge Demolition on {target_state.unit.name}: rolled {roll}, DESTROYED!"
                    )
                else:
                    game_state.apply_action(action)
                    return ActionResult(
                        True,
                        f"{unit.name} uses Bridge Demolition on {target_state.unit.name}: rolled {roll}, failed (need 4+)"
                    )

        # Handle Change Facing ability (Command Quick Reactions)
        if action.ability_name.lower() == 'change_facing':
            if unit.unit_type != 'Vehicle':
                return ActionResult(False, "Only Vehicles can change facing")
            if not unit_state.quick_reactions_available:
                return ActionResult(False, "Change facing not available (requires Command Quick Reactions)")

            new_facing = action.parameters.get('new_facing')
            if new_facing is None or not (0 <= new_facing <= 5):
                return ActionResult(False, "Invalid facing direction (must be 0-5)")

            old_facing = unit_state.facing
            unit_state.facing = new_facing
            unit_state.quick_reactions_available = False  # Can only use once after attack

            game_state.apply_action(action)
            return ActionResult(
                True,
                f"{unit.name} changes facing from {old_facing} to {new_facing} (Command Quick Reactions)"
            )

        # Validate ability use
        validation = self.validator.validate_ability(action, unit, game_state)
        if not validation:
            return ActionResult(False, f"Invalid ability: {validation.reason}")

        # Mark ability as used
        game_state.mark_ability_used(action.unit_id, action.ability_name)
        game_state.apply_action(action)

        return ActionResult(
            True,
            f"{unit.name} uses {action.ability_name}"
        )
    
    def _execute_pass(self, game_state: GameState, action: PassAction) -> ActionResult:
        """Execute a pass action (do nothing)"""
        game_state.apply_action(action)
        return ActionResult(True, "Passed turn")
    
    def _execute_end_phase(self, game_state: GameState, 
                          action: EndPhaseAction) -> ActionResult:
        """Execute ending the current phase"""
        old_phase = game_state.current_phase
        game_state.next_phase()
        game_state.apply_action(action)
        
        if game_state.current_phase == GamePhase.MOVEMENT:
            # New turn started
            return ActionResult(
                True,
                f"Turn {game_state.turn_number} - {game_state.active_player}'s turn"
            )
        else:
            return ActionResult(
                True,
                f"Advanced to {game_state.current_phase} phase"
            )

    def _execute_board_transport(self, game_state: GameState,
                                  action: BoardTransportAction) -> ActionResult:
        """Execute a soldier boarding a transport"""
        soldier_state = game_state.get_unit_state(action.unit_id)
        transport_state = game_state.get_unit_state(action.transport_id)

        if not soldier_state:
            return ActionResult(False, f"Soldier {action.unit_id} not found")
        if not transport_state:
            return ActionResult(False, f"Transport {action.transport_id} not found")

        # Verify they're in the same hex
        if soldier_state.position != transport_state.position:
            return ActionResult(False, "Soldier and transport must be in same hex")

        # Verify transport can carry
        if transport_state.carried_unit_id is not None:
            return ActionResult(False, "Transport is already carrying a unit")

        # Verify soldier is not already being carried
        if soldier_state.carried_by_id is not None:
            return ActionResult(False, "Soldier is already in a transport")

        # Dug In units can't be transported
        soldier_abilities = getattr(soldier_state.unit, 'abilities', []) or []
        has_dug_in = any(a.lower() == 'dug in' for a in soldier_abilities)
        if has_dug_in:
            return ActionResult(False, "Dug In unit cannot board transport")

        # Board the transport
        soldier_state.carried_by_id = action.transport_id
        transport_state.carried_unit_id = action.unit_id
        soldier_state.has_moved = True  # Boarding counts as movement

        game_state.apply_action(action)
        return ActionResult(
            True,
            f"{soldier_state.unit.name} boarded {transport_state.unit.name}"
        )

    def _execute_dismount_transport(self, game_state: GameState,
                                     action: DismountTransportAction) -> ActionResult:
        """Execute a soldier dismounting from a transport"""
        soldier_state = game_state.get_unit_state(action.unit_id)
        transport_state = game_state.get_unit_state(action.transport_id)

        if not soldier_state:
            return ActionResult(False, f"Soldier {action.unit_id} not found")
        if not transport_state:
            return ActionResult(False, f"Transport {action.transport_id} not found")

        # Verify soldier is being carried by this transport
        if soldier_state.carried_by_id != action.transport_id:
            return ActionResult(False, "Soldier is not in this transport")

        # Dismount
        soldier_state.carried_by_id = None
        transport_state.carried_unit_id = None
        soldier_state.position = (action.to_q, action.to_r)
        soldier_state.has_moved = True  # Dismounting counts as movement

        game_state.apply_action(action)
        return ActionResult(
            True,
            f"{soldier_state.unit.name} dismounted from {transport_state.unit.name} to ({action.to_q},{action.to_r})"
        )

    def _execute_deploy(self, game_state: GameState,
                        action: DeployAction) -> ActionResult:
        """Execute a Paratrooper deployment action"""
        unit_state = game_state.get_unit_state(action.unit_id)
        if not unit_state:
            return ActionResult(False, f"Unit {action.unit_id} not found")

        # Check if already deployed
        if unit_state.is_deployed:
            return ActionResult(False, "Unit is already deployed")

        # Check if destination hex is valid
        dest_hex = game_state.board.get_hex(action.to_q, action.to_r)
        if not dest_hex:
            return ActionResult(False, "Invalid deployment location")
        if dest_hex.terrain == 'impassable':
            return ActionResult(False, "Cannot deploy in impassable terrain")
        if dest_hex.unit is not None:
            return ActionResult(False, "Deployment hex is occupied")

        # Check adjacency to enemies
        enemy_owner = "player2" if unit_state.owner == "player1" else "player1"
        for enemy_state in game_state.get_units_by_owner(enemy_owner):
            if not enemy_state.is_alive:
                continue
            eq, er = enemy_state.position
            dist = game_state.board.hex_distance(action.to_q, action.to_r, eq, er)
            if dist <= 1:
                return ActionResult(False, "Cannot deploy adjacent to enemy units")

        # Deploy the unit
        unit_state.is_deployed = True
        unit_state.position = (action.to_q, action.to_r)
        unit_state.has_moved = True  # Can't move on the turn deployed
        dest_hex.unit = unit_state.unit

        game_state.apply_action(action)
        return ActionResult(
            True,
            f"{unit_state.unit.name} (Paratrooper) deployed at ({action.to_q},{action.to_r})"
        )

    def _execute_place_aircraft(self, game_state: GameState,
                                action: PlaceAircraftAction) -> ActionResult:
        """Execute placing an Aircraft on the map during Flight phase"""
        unit_state = game_state.get_unit_state(action.unit_id)
        if not unit_state:
            return ActionResult(False, f"Aircraft {action.unit_id} not found")

        if unit_state.unit.unit_type != 'Aircraft':
            return ActionResult(False, "Only Aircraft can be placed during Flight phase")

        if unit_state.is_aircraft_on_map:
            return ActionResult(False, "Aircraft is already on the map")

        # Place the Aircraft
        unit_state.is_aircraft_on_map = True
        unit_state.position = (action.to_q, action.to_r)
        # Note: Aircraft don't occupy hexes like ground units - they fly over

        # Check for Ace/Antiair defensive fire opportunities
        df_results = []
        enemy_owner = "player2" if unit_state.owner == "player1" else "player1"
        enemy_units = game_state.get_units_by_owner(enemy_owner)
        aircraft_pos = (action.to_q, action.to_r)

        for enemy_state in enemy_units:
            if not enemy_state.is_alive or enemy_state.is_disrupted:
                continue

            enemy_abilities = getattr(enemy_state.unit, 'abilities', []) or []
            has_ace = any(a.lower() == 'ace' for a in enemy_abilities)
            has_antiair = any('antiair' in a.lower() for a in enemy_abilities)

            if not has_ace and not has_antiair:
                continue

            # Calculate distance
            enemy_pos = enemy_state.position
            distance = game_state.board.hex_distance(
                enemy_pos[0], enemy_pos[1], aircraft_pos[0], aircraft_pos[1]
            )

            # Ace: within 4 hexes, Antiair: adjacent (within 1 hex)
            can_fire = False
            if has_ace and distance <= 4:
                can_fire = True
            elif has_antiair and distance <= 1:
                can_fire = True

            if can_fire:
                # Create defensive fire opportunity and resolve
                from defensive_fire import DefensiveFireOpportunity
                opportunity = DefensiveFireOpportunity(
                    defender_id=enemy_state.unit.id,
                    defender_state=enemy_state,
                    target_id=action.unit_id,
                    target_state=unit_state,
                    from_hex=aircraft_pos,  # Aircraft appears here
                    to_hex=aircraft_pos,
                    defender_pos=enemy_pos
                )
                result = self.defensive_fire.resolve_defensive_fire(
                    game_state, opportunity, attack_in_hex=aircraft_pos
                )
                self.defensive_fire.apply_defensive_fire_result(game_state, result)
                df_results.append(result)

        game_state.apply_action(action)

        message = f"{unit_state.unit.name} placed at ({action.to_q},{action.to_r})"
        if df_results:
            message += f" - {len(df_results)} Ace/Antiair attack(s)"

        return ActionResult(
            True, message, defensive_fire_results=df_results
        )

    def execute_action_sequence(self, game_state: GameState,
                               actions: list) -> list:
        """
        Execute a sequence of actions.
        Stops on first failure unless action is PassAction or EndPhaseAction.
        """
        results = []
        
        for action in actions:
            result = self.execute_action(game_state, action)
            results.append(result)
            
            # Stop on failure for most actions
            if not result.success and not isinstance(action, (PassAction, EndPhaseAction)):
                break
        
        return results
    
    def can_execute(self, game_state: GameState, action: Action) -> ActionValidation:
        """
        Check if an action can be executed without actually executing it.
        """
        self.validator.board = game_state.board
        
        if isinstance(action, MoveAction):
            unit_state = game_state.get_unit_state(action.unit_id)
            if not unit_state:
                return ActionValidation(False, "Unit not found")
            # Check disruption (unless unit can move while disrupted)
            if unit_state.is_disrupted:
                unit_abilities = getattr(unit_state.unit, 'abilities', []) or []
                # Check for abilities that allow movement while disrupted
                can_move_disrupted = any(a.lower() in (
                    'robust', 'hardened veteran', 'ss determination', 'courage',
                    'veteran crew', 'veteran guard', 'charge',
                    'canadian hero', 'german hero', 'italian hero', 'japanese hero',
                    'soviet hero', 'u.k. hero', 'u.s. hero', 'hero'
                ) for a in unit_abilities)
                if not can_move_disrupted:
                    return ActionValidation(False, "Unit is disrupted and cannot move")
            return self.validator.validate_move(action, unit_state.unit, game_state)
        
        elif isinstance(action, AttackAction):
            attacker_state = game_state.get_unit_state(action.unit_id)
            target_state = game_state.get_unit_state(action.target_id)
            if not attacker_state or not target_state:
                return ActionValidation(False, "Unit not found")
            return self.validator.validate_attack(
                action, attacker_state.unit, target_state.unit, game_state
            )
        
        elif isinstance(action, MoveAndAttackAction):
            unit_state = game_state.get_unit_state(action.unit_id)
            if not unit_state:
                return ActionValidation(False, "Unit not found")
            return self.validator.validate_move_and_attack(
                action, unit_state.unit, game_state
            )
        
        elif isinstance(action, UseAbilityAction):
            unit_state = game_state.get_unit_state(action.unit_id)
            if not unit_state:
                return ActionValidation(False, "Unit not found")
            return self.validator.validate_ability(
                action, unit_state.unit, game_state
            )
        
        return ActionValidation(True)


# Demo/Test function
def demo_action_execution():
    """Demonstrate action execution with new dice system"""
    import os
    
    # Handle both filename formats
    ability_file = 'Axis_and_Allies_Unit_Data_for_Analysis_-_Special_Abilities.csv'
    if not os.path.exists(ability_file):
        ability_file = 'Axis and Allies Unit Data for Analysis - Special_Abilities.csv'
    
    from abilities import AbilitySystem
    ability_system = AbilitySystem(ability_file)
    
    from movement import MovementSystem
    movement_system = MovementSystem(ability_system)
    
    # Use a dummy combat system (executor has its own dice now)
    combat_system = None
    
    executor = ActionExecutor(movement_system, combat_system, ability_system)
    
    print("=" * 70)
    print("ACTION EXECUTOR - DICE SYSTEM INTEGRATION TEST")
    print("=" * 70)
    
    # Create a simple test scenario
    from board import Board
    from game_state import GameState, UnitState as GSUnitState
    from units import Unit
    import csv
    
    # Load units
    unit_file = 'Axis_and_Allies_Unit_Data_for_Analysis_-_Unit_Stats.csv'
    if not os.path.exists(unit_file):
        unit_file = 'Axis and Allies Unit Data for Analysis - Unit_Stats.csv'
    
    units = []
    with open(unit_file, 'r') as file:
        reader = csv.DictReader(file)
        for row in reader:
            unit = Unit(
                name=row['Unit Name'],
                nation=row['Nation'],
                unit_type=row['Type'],
                year=row['Year'],
                cost=row['Cost'],
                defense=row['Def'],
                speed=row['Speed'],
                veh_short=row['Veh S'],
                veh_medium=row['Veh M'],
                veh_long=row['Veh L'],
                per_short=row['Per S'],
                per_medium=row['Per M'],
                per_long=row['Per L'],
                abilities=row['Abilities']
            )
            units.append(unit)
    
    # Get test units
    soldiers = [u for u in units if u.unit_type == 'Soldier' and u.per_short > 0][:2]
    vehicles = [u for u in units if u.unit_type == 'Vehicle' and u.veh_short > 0][:1]
    
    if len(soldiers) >= 2:
        # Create board
        board = Board(15, 15)
        board.set_terrain(5, 5, 'forest')
        
        # Create unit states
        from copy import deepcopy
        
        inf1 = deepcopy(soldiers[0])
        inf1.id = "p1_inf"
        inf2 = deepcopy(soldiers[1])
        inf2.id = "p2_inf"
        
        p1_unit = GSUnitState(inf1, (3, 3), "player1", inf1.defense_front)
        p2_unit = GSUnitState(inf2, (4, 3), "player2", inf2.defense_front)
        
        game_state = GameState(board, [p1_unit], [p2_unit])
        game_state.current_phase = GamePhase.ASSAULT
        
        print(f"\nTest Scenario:")
        print(f"  Attacker: {inf1.name} at (3,3)")
        print(f"  Target: {inf2.name} at (4,3)")
        print(f"  Distance: 1 hex")
        print(f"  Terrain: open")
        
        # Create attack action
        from action import AttackAction
        attack = AttackAction(
            unit_id="p1_inf",
            attacker_q=3, attacker_r=3,
            target_id="p2_inf",
            target_q=4, target_r=3,
            range_category="short",
            distance=1,
            has_los=True
        )
        
        print("\n--- Executing Attack ---")
        result = executor.execute_action(game_state, attack)
        print(f"\nResult: {result}")
        
        if result.combat_details:
            cd = result.combat_details
            print(f"\nCombat Details:")
            print(f"  Attack dice: {cd.get('attack_dice', 0)}")
            print(f"  Rolls: {cd.get('attack_rolls', [])}")
            print(f"  Hit threshold: {cd.get('hit_threshold', 4)}+")
            print(f"  Successes: {cd.get('successes', 0)}")
            print(f"  Defense: {cd.get('defense', 0)}")
            print(f"  Hits: {cd.get('hits', 0)}")
            print(f"  Outcome: {cd.get('outcome', 'unknown')}")
            if cd.get('cover_rolled'):
                print(f"  Cover: roll {cd.get('cover_roll')} vs {cd.get('cover_threshold')}+ = {'SUCCESS' if cd.get('cover_success') else 'FAILED'}")
        
        # Check target status
        target_state = game_state.get_unit_state("p2_inf")
        if target_state:
            print(f"\nTarget status after attack:")
            print(f"  Disrupted: {target_state.is_disrupted}")
            print(f"  Damaged: {target_state.is_damaged}")
            print(f"  Alive: {target_state.is_alive}")
        else:
            print(f"\nTarget was DESTROYED!")
    
    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    demo_action_execution()