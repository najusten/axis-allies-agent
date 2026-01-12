"""
Action Executor for Axis & Allies Miniatures

Validates and executes actions on game states.
This is the core game loop - it takes actions and applies them to the game state.
"""

from typing import Tuple, Optional
import random

from game_state import GameState, GamePhase
from action import (
    Action, MoveAction, AttackAction, MoveAndAttackAction,
    UseAbilityAction, PassAction, EndPhaseAction,
    ActionValidator, ActionValidation
)
from movement import MovementSystem
from combat import CombatSystem
from abilities import AbilitySystem


class ActionResult:
    """Result of executing an action"""
    def __init__(self, success: bool, message: str = "", 
                 damage_dealt: int = 0, unit_destroyed: str = None):
        self.success = success
        self.message = message
        self.damage_dealt = damage_dealt
        self.unit_destroyed = unit_destroyed
    
    def __str__(self):
        return f"{'✓' if self.success else '✗'} {self.message}"
    
    def __bool__(self):
        return self.success


class ActionExecutor:
    """
    Validates and executes actions on game states.
    This is the bridge between player decisions and game state changes.
    """
    
    def __init__(self, movement_system: MovementSystem,
                 combat_system: CombatSystem,
                 ability_system: AbilitySystem):
        self.movement_system = movement_system
        self.combat_system = combat_system
        self.ability_system = ability_system
        self.validator = ActionValidator(
            None, movement_system, combat_system, ability_system
        )
    
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
        """Execute a movement action"""
        unit_state = game_state.get_unit_state(action.unit_id)
        if not unit_state:
            return ActionResult(False, f"Unit {action.unit_id} not found")
        
        unit = unit_state.unit
        
        # Validate the move
        validation = self.validator.validate_move(action, unit, game_state)
        if not validation:
            return ActionResult(False, f"Invalid move: {validation.reason}")
        
        # Execute the move
        success = game_state.move_unit(action.unit_id, action.to_q, action.to_r)
        
        if success:
            game_state.apply_action(action)
            return ActionResult(
                True, 
                f"{unit.name} moved to ({action.to_q}, {action.to_r})"
            )
        else:
            return ActionResult(False, "Move failed")
    
    def _execute_attack(self, game_state: GameState, action: AttackAction) -> ActionResult:
        """Execute an attack action"""
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
        
        # Execute the attack using combat system
        hit, damage = self._resolve_attack(
            attacker, target, action.range_category, action.distance
        )
        
        message = f"{attacker.name} attacks {target.name} at {action.range_category} range"
        
        if hit:
            # Apply damage
            unit_destroyed = game_state.damage_unit(action.target_id, damage)
            game_state.mark_unit_attacked(action.unit_id)
            game_state.apply_action(action)
            
            if unit_destroyed:
                message += f" - HIT! {damage} damage. {target.name} DESTROYED!"
                return ActionResult(True, message, damage, action.target_id)
            else:
                remaining_hp = target_state.current_health
                message += f" - HIT! {damage} damage ({remaining_hp} HP remaining)"
                return ActionResult(True, message, damage)
        else:
            game_state.mark_unit_attacked(action.unit_id)
            game_state.apply_action(action)
            message += " - MISS!"
            return ActionResult(True, message, 0)
    
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
            attack_result.damage_dealt,
            attack_result.unit_destroyed
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
        
        # Ability effects would be applied here
        # This would depend on the specific ability
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
    
    def _resolve_attack(self, attacker, target, range_category: str, 
                       distance: int) -> Tuple[bool, int]:
        """
        Resolve an attack using the combat system.
        Returns (hit: bool, damage: int)
        """
        # Get attack value for range
        attack_value = self.combat_system.get_attack_value(attacker, range_category)
        
        if attack_value == 0:
            return False, 0  # Can't attack at this range
        
        # Roll attack (simplified - using random for now)
        # Real implementation would use proper dice rolling
        attack_roll = random.randint(1, 6)
        
        # Check if hit
        hit = attack_roll <= attack_value
        
        if hit:
            # Calculate damage (simplified)
            # Real implementation would consider armor, cover, etc.
            damage = 1  # Base damage
            
            # Apply modifiers from abilities
            damage_mods = self.ability_system.get_damage_modifiers(
                attacker, target, range_category, distance
            )
            damage += damage_mods.get('bonus_damage', 0)
            
            return True, max(1, damage)
        
        return False, 0
    
    def execute_action_sequence(self, game_state: GameState, 
                               actions: list[Action]) -> list[ActionResult]:
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
        Useful for AI planning and action filtering.
        """
        self.validator.board = game_state.board
        
        if isinstance(action, MoveAction):
            unit_state = game_state.get_unit_state(action.unit_id)
            if not unit_state:
                return ActionValidation(False, "Unit not found")
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
        
        # Pass and EndPhase are always valid
        return ActionValidation(True)


# Demo/Test function
def demo_action_execution():
    """Demonstrate action execution"""
    from game_state import create_test_game_state
    from action_generator import ActionGenerator
    
    # Create test game
    game_state = create_test_game_state()
    
    # Create systems
    from abilities import AbilitySystem
    ability_system = AbilitySystem('Axis and Allies Unit Data for Analysis - Special_Abilities.csv')
    movement_system = MovementSystem(ability_system)
    combat_system = CombatSystem(ability_system)
    
    # Create executor and generator
    executor = ActionExecutor(movement_system, combat_system, ability_system)
    generator = ActionGenerator(movement_system, combat_system, ability_system)
    
    print("=== ACTION EXECUTION DEMO ===\n")
    print(game_state)
    print()
    
    # Get legal actions
    actions = generator.get_all_legal_actions(game_state, "player1")
    print(f"Player 1 has {len(actions)} legal actions")
    print()
    
    # Execute a few actions
    print("Executing sample actions:\n")
    
    for i, action in enumerate(actions[:3]):
        print(f"{i+1}. Attempting: {action}")
        result = executor.execute_action(game_state, action)
        print(f"   {result}")
        print()
    
    print("\nUpdated game state:")
    print(game_state)


if __name__ == "__main__":
    demo_action_execution()