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
    UseAbilityAction, PassAction, EndPhaseAction,
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
        
        else:
            return ActionResult(False, f"Unknown action type: {type(action)}")
    
    def _execute_move(self, game_state: GameState, action: MoveAction) -> ActionResult:
        """Execute a movement action with defensive fire checks"""
        unit_state = game_state.get_unit_state(action.unit_id)
        if not unit_state:
            return ActionResult(False, f"Unit {action.unit_id} not found")
        
        # Check if unit is disrupted (can't move)
        if unit_state.is_disrupted:
            return ActionResult(False, f"{unit_state.unit.name} is disrupted and cannot move")
        
        unit = unit_state.unit
        
        # Validate the move
        validation = self.validator.validate_move(action, unit, game_state)
        if not validation:
            return ActionResult(False, f"Invalid move: {validation.reason}")
        
        from_hex = (action.from_q, action.from_r)
        to_hex = (action.to_q, action.to_r)
        
        # Check for defensive fire opportunities
        df_opportunities = self.defensive_fire.check_defensive_fire_triggered(
            game_state, action.unit_id, from_hex, to_hex
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
            
            if result.movement_stopped:
                movement_stopped = True
                final_hex = stopped_at if stopped_at else to_hex
                # Once stopped, no more defensive fire matters
                break
        
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
            
            # Update facing for vehicles
            if unit.unit_type == 'Vehicle':
                new_facing = calculate_facing_after_move(from_hex, final_hex)
                unit_state.facing = new_facing.value
                facing_str = get_direction_name(new_facing)
                message += f" (facing {facing_str})"
            
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
        
        # Resolve the attack
        result = self._resolve_attack_full(
            attacker, target,
            attacker_state, target_state,
            action.distance,
            target_terrain,
            is_rear_attack,
            attacker_same_hex
        )
        
        # Apply results to game state
        game_state.mark_unit_attacked(action.unit_id)
        game_state.apply_action(action)
        
        # Handle damage based on simultaneous combat setting
        if self.use_simultaneous_combat and result['hits'] > 0:
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
                            is_rear_attack: bool, attacker_same_hex: bool) -> Dict:
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
            'notes': []
        }
        
        # Get attack modifiers from abilities
        attack_mods = self.ability_system.get_attack_modifiers(
            attacker, target, distance, target_terrain
        )
        
        if not attack_mods.get('can_attack', True):
            result['notes'].extend(attack_mods.get('notes', []))
            return result
        
        # Determine attack dice
        attack_dice = self._get_attack_dice(attacker, target, distance, attack_mods)
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
        
        # Apply disrupted/damaged defense penalty
        if target_disrupted or target_damaged:
            base_defense = max(1, base_defense - 1)
        
        result['defense'] = base_defense
        
        # Check cover
        has_cover = target_terrain in self.COVER_TERRAIN
        ignore_cover = attack_mods.get('ignore_cover', False)
        
        if ignore_cover:
            has_cover = False
            result['notes'].append("Attacker ignores cover")
        
        # Roll cover save if applicable
        cover_success = False
        if has_cover:
            result['cover_rolled'] = True
            cover_result = self.dice.roll_cover_save(
                target_category, attacker_same_hex
            )
            cover_success = cover_result.success
            result['cover_success'] = cover_success
            result['cover_roll'] = cover_result.roll
            result['cover_threshold'] = cover_result.threshold
        
        # Roll attack
        attack_ability_mod = attack_mods.get('hit_modifier', 0)
        attack_result = self.dice.roll_attack(
            attack_dice, attacker_disrupted, attacker_damaged, attack_ability_mod
        )
        result['attack_rolls'] = attack_result.rolls
        result['successes'] = attack_result.successes
        result['hit_threshold'] = attack_result.hit_threshold
        
        # Calculate hits
        hits = self.dice.calculate_hits(attack_result.successes, base_defense)
        result['hits'] = hits
        
        if hits == 0:
            result['outcome'] = 'miss'
            result['notes'].append(f"Scored {attack_result.successes} successes, needed {base_defense}")
            return result
        
        # Resolve damage
        damage_result = self.dice.resolve_damage(
            hits, target_category, current_status, cover_success
        )
        
        result['target_new_status'] = damage_result.new_status
        result['target_destroyed'] = damage_result.new_status == UnitStatus.DESTROYED
        result['status_change'] = damage_result.status_change
        result['counters_placed'] = damage_result.counters_placed
        
        # Set outcome
        if damage_result.new_status == UnitStatus.DESTROYED:
            result['outcome'] = 'destroyed'
        elif damage_result.new_status == UnitStatus.DISRUPTED_AND_DAMAGED:
            result['outcome'] = 'disrupted_and_damaged'
        elif damage_result.new_status == UnitStatus.DAMAGED:
            result['outcome'] = 'damaged'
        elif damage_result.new_status == UnitStatus.DISRUPTED:
            result['outcome'] = 'disrupted'
        else:
            result['outcome'] = 'no_effect'
        
        return result
    
    def _get_attack_dice(self, attacker, target, distance: int, 
                        ability_mods: Dict = None) -> int:
        """Get number of attack dice based on target type and range"""
        ability_mods = ability_mods or {}
        
        # Close Assault override
        if ability_mods.get('close_assault_dice') and distance == 0:
            return ability_mods['close_assault_dice']
        
        # Determine range category
        from movement import MovementSystem
        range_category = MovementSystem.get_range_category(distance)
        
        # Check target type
        target_type = getattr(target, 'unit_type', 'Soldier')
        is_vehicle = target_type == 'Vehicle'
        
        if is_vehicle:
            if range_category == 'short':
                dice = getattr(attacker, 'veh_short', 0)
            elif range_category == 'medium':
                dice = getattr(attacker, 'veh_medium', 0)
            else:
                dice = getattr(attacker, 'veh_long', 0)
        else:
            if range_category == 'short':
                dice = getattr(attacker, 'per_short', 0)
            elif range_category == 'medium':
                dice = getattr(attacker, 'per_medium', 0)
            else:
                dice = getattr(attacker, 'per_long', 0)
        
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
            # Check disruption
            if unit_state.is_disrupted:
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