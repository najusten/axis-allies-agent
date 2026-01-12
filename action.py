"""
Game Action Definitions for Axis & Allies Miniatures

Defines all possible actions a unit can take during a game.
Each action type has validation logic and execution methods.
"""

from typing import Tuple, Optional
from dataclasses import dataclass

@dataclass
class Action:
    """Base class for all game actions"""
    unit_id: str  # Unique identifier for the unit performing the action
    action_type: str  # Type of action (move, attack, ability, etc.)
    
    def __str__(self):
        return f"{self.action_type} by {self.unit_id}"


@dataclass
class MoveAction(Action):
    """Represents a unit movement action"""
    from_q: int
    from_r: int
    to_q: int
    to_r: int
    path: list[Tuple[int, int]]  # Full path including intermediate hexes
    movement_cost: int
    
    def __init__(self, unit_id: str, from_q: int, from_r: int, to_q: int, to_r: int, 
                 path: list[Tuple[int, int]] = None, movement_cost: int = 0):
        super().__init__(unit_id, "move")
        self.from_q = from_q
        self.from_r = from_r
        self.to_q = to_q
        self.to_r = to_r
        self.path = path or [(from_q, from_r), (to_q, to_r)]
        self.movement_cost = movement_cost
    
    def __str__(self):
        return f"Move {self.unit_id}: ({self.from_q},{self.from_r}) → ({self.to_q},{self.to_r}) [cost: {self.movement_cost}]"


@dataclass
class AttackAction(Action):
    """Represents a unit attacking another unit"""
    attacker_q: int
    attacker_r: int
    target_id: str
    target_q: int
    target_r: int
    range_category: str  # 'short', 'medium', 'long'
    distance: int
    has_los: bool
    
    def __init__(self, unit_id: str, attacker_q: int, attacker_r: int,
                 target_id: str, target_q: int, target_r: int,
                 range_category: str, distance: int, has_los: bool = True):
        super().__init__(unit_id, "attack")
        self.attacker_q = attacker_q
        self.attacker_r = attacker_r
        self.target_id = target_id
        self.target_q = target_q
        self.target_r = target_r
        self.range_category = range_category
        self.distance = distance
        self.has_los = has_los
    
    def __str__(self):
        return f"Attack {self.unit_id} → {self.target_id} at range {self.distance} ({self.range_category})"


@dataclass
class MoveAndAttackAction(Action):
    """Represents moving then attacking in the same turn (for units with special abilities)"""
    move_action: MoveAction
    attack_action: AttackAction
    
    def __init__(self, unit_id: str, move_action: MoveAction, attack_action: AttackAction):
        super().__init__(unit_id, "move_and_attack")
        self.move_action = move_action
        self.attack_action = attack_action
    
    def __str__(self):
        return f"MoveAttack {self.unit_id}: {self.move_action.to_q},{self.move_action.to_r} then attack {self.attack_action.target_id}"


@dataclass
class UseAbilityAction(Action):
    """Represents using a special ability"""
    ability_name: str
    target_id: Optional[str] = None
    target_q: Optional[int] = None
    target_r: Optional[int] = None
    parameters: dict = None  # For abilities with custom parameters
    
    def __init__(self, unit_id: str, ability_name: str, 
                 target_id: str = None, target_q: int = None, target_r: int = None,
                 parameters: dict = None):
        super().__init__(unit_id, "ability")
        self.ability_name = ability_name
        self.target_id = target_id
        self.target_q = target_q
        self.target_r = target_r
        self.parameters = parameters or {}
    
    def __str__(self):
        target_str = f" on {self.target_id}" if self.target_id else ""
        return f"Ability {self.unit_id}: {self.ability_name}{target_str}"


@dataclass
class PassAction(Action):
    """Represents passing/doing nothing this turn"""
    
    def __init__(self, unit_id: str = "player"):
        super().__init__(unit_id, "pass")
    
    def __str__(self):
        return f"Pass turn"


@dataclass
class EndPhaseAction(Action):
    """Represents ending the current phase"""
    phase_name: str
    
    def __init__(self, phase_name: str):
        super().__init__("player", "end_phase")
        self.phase_name = phase_name
    
    def __str__(self):
        return f"End {self.phase_name} phase"


# Action validation results
@dataclass
class ActionValidation:
    """Result of validating an action"""
    is_valid: bool
    reason: str = ""  # Explanation if invalid
    
    def __bool__(self):
        return self.is_valid


class ActionValidator:
    """Validates whether actions are legal in the current game state"""
    
    def __init__(self, board, movement_system, combat_system, ability_system):
        self.board = board
        self.movement_system = movement_system
        self.combat_system = combat_system
        self.ability_system = ability_system
    
    def validate_move(self, action: MoveAction, unit, game_state) -> ActionValidation:
        """Validate a move action"""
        # Check if unit has already moved this turn
        if game_state.has_unit_moved(unit.id):
            return ActionValidation(False, "Unit has already moved this turn")
        
        # Check if destination is reachable
        reachable = self.movement_system.get_reachable_hexes(
            self.board, action.from_q, action.from_r, unit
        )
        
        if (action.to_q, action.to_r) not in reachable:
            return ActionValidation(False, f"Hex ({action.to_q},{action.to_r}) is not reachable")
        
        # Check if destination is occupied
        dest_hex = self.board.get_hex(action.to_q, action.to_r)
        if dest_hex and dest_hex.unit is not None:
            return ActionValidation(False, "Destination hex is occupied")
        
        return ActionValidation(True)
    
    def validate_attack(self, action: AttackAction, unit, target, game_state) -> ActionValidation:
        """Validate an attack action"""
        # Check if unit has already attacked this turn
        if game_state.has_unit_attacked(unit.id):
            return ActionValidation(False, "Unit has already attacked this turn")
        
        # Check range
        if action.distance > unit.range:
            return ActionValidation(False, f"Target out of range (distance: {action.distance}, max: {unit.range})")
        
        # Check LOS
        if not action.has_los:
            return ActionValidation(False, "No line of sight to target")
        
        # Check if target is valid (not friendly)
        if game_state.get_unit_owner(unit.id) == game_state.get_unit_owner(target.id):
            return ActionValidation(False, "Cannot attack friendly units")
        
        return ActionValidation(True)
    
    def validate_move_and_attack(self, action: MoveAndAttackAction, unit, game_state) -> ActionValidation:
        """Validate a move-and-attack action"""
        # Check if unit has the ability to move and attack
        if not self.movement_system.can_unit_move_and_attack(unit):
            return ActionValidation(False, "Unit cannot move and attack in same turn")
        
        # Validate the move component
        move_valid = self.validate_move(action.move_action, unit, game_state)
        if not move_valid:
            return ActionValidation(False, f"Invalid move: {move_valid.reason}")
        
        # Validate the attack component (from new position)
        # Note: This assumes the move has been applied to calculate attack validity
        attack_valid = self.validate_attack(action.attack_action, unit, 
                                           game_state.get_unit(action.attack_action.target_id),
                                           game_state)
        if not attack_valid:
            return ActionValidation(False, f"Invalid attack: {attack_valid.reason}")
        
        return ActionValidation(True)
    
    def validate_ability(self, action: UseAbilityAction, unit, game_state) -> ActionValidation:
        """Validate an ability use action"""
        # Check if unit has the ability
        if action.ability_name not in unit.abilities:
            return ActionValidation(False, f"Unit does not have ability '{action.ability_name}'")
        
        # Check if ability has already been used this turn
        if game_state.has_ability_been_used(unit.id, action.ability_name):
            return ActionValidation(False, f"Ability '{action.ability_name}' already used this turn")
        
        # Additional ability-specific validation would go here
        # This would check things like range, targets, prerequisites, etc.
        
        return ActionValidation(True)


# Convenience functions for creating actions
def create_move_action(unit, from_pos: Tuple[int, int], to_pos: Tuple[int, int], 
                      path: list = None, cost: int = 0) -> MoveAction:
    """Helper to create a move action"""
    return MoveAction(
        unit_id=unit.id,
        from_q=from_pos[0],
        from_r=from_pos[1],
        to_q=to_pos[0],
        to_r=to_pos[1],
        path=path,
        movement_cost=cost
    )


def create_attack_action(attacker, attacker_pos: Tuple[int, int],
                        target, target_pos: Tuple[int, int],
                        range_cat: str, distance: int, has_los: bool = True) -> AttackAction:
    """Helper to create an attack action"""
    return AttackAction(
        unit_id=attacker.id,
        attacker_q=attacker_pos[0],
        attacker_r=attacker_pos[1],
        target_id=target.id,
        target_q=target_pos[0],
        target_r=target_pos[1],
        range_category=range_cat,
        distance=distance,
        has_los=has_los
    )