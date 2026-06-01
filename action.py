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
    is_strike_and_fade: bool  # True if this is a Strike and Fade move (after attacking)
    is_relocate: bool  # True if this is a Relocate move (assault phase movement)

    def __init__(self, unit_id: str, from_q: int, from_r: int, to_q: int, to_r: int,
                 path: list[Tuple[int, int]] = None, movement_cost: int = 0,
                 is_strike_and_fade: bool = False, is_relocate: bool = False):
        super().__init__(unit_id, "move")
        self.from_q = from_q
        self.from_r = from_r
        self.to_q = to_q
        self.to_r = to_r
        self.path = path or [(from_q, from_r), (to_q, to_r)]
        self.movement_cost = movement_cost
        self.is_strike_and_fade = is_strike_and_fade
        self.is_relocate = is_relocate

    def __str__(self):
        suffix = ""
        if self.is_strike_and_fade:
            suffix = " [Strike&Fade]"
        elif self.is_relocate:
            suffix = " [Relocate]"
        return f"Move {self.unit_id}: ({self.from_q},{self.from_r}) → ({self.to_q},{self.to_r}){suffix}"


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
    improvised_attack: Optional[dict] = None  # For Improvisation ability: {attack_close, attack_medium, attack_long, etc.}

    def __init__(self, unit_id: str, attacker_q: int, attacker_r: int,
                 target_id: str, target_q: int, target_r: int,
                 range_category: str, distance: int, has_los: bool = True,
                 improvised_attack: Optional[dict] = None):
        super().__init__(unit_id, "attack")
        self.attacker_q = attacker_q
        self.attacker_r = attacker_r
        self.target_id = target_id
        self.target_q = target_q
        self.target_r = target_r
        self.range_category = range_category
        self.distance = distance
        self.has_los = has_los
        self.improvised_attack = improvised_attack

    def __str__(self):
        suffix = " [Improvised]" if self.improvised_attack else ""
        return f"Attack {self.unit_id} → {self.target_id} at range {self.distance} ({self.range_category}){suffix}"


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
class BoardTransportAction(Action):
    """Represents a soldier boarding a transport"""
    transport_id: str
    position_q: int
    position_r: int

    def __init__(self, unit_id: str, transport_id: str, position_q: int, position_r: int):
        super().__init__(unit_id, "board_transport")
        self.transport_id = transport_id
        self.position_q = position_q
        self.position_r = position_r

    def __str__(self):
        return f"Board {self.unit_id} → Transport {self.transport_id}"


@dataclass
class DismountTransportAction(Action):
    """Represents a soldier dismounting from a transport"""
    transport_id: str
    to_q: int
    to_r: int

    def __init__(self, unit_id: str, transport_id: str, to_q: int, to_r: int):
        super().__init__(unit_id, "dismount_transport")
        self.transport_id = transport_id
        self.to_q = to_q
        self.to_r = to_r

    def __str__(self):
        return f"Dismount {self.unit_id} from {self.transport_id} → ({self.to_q},{self.to_r})"


@dataclass
class DeployAction(Action):
    """Represents deploying a Paratrooper unit onto the map"""
    to_q: int
    to_r: int

    def __init__(self, unit_id: str, to_q: int, to_r: int):
        super().__init__(unit_id, "deploy")
        self.to_q = to_q
        self.to_r = to_r

    def __str__(self):
        return f"Deploy {self.unit_id} at ({self.to_q},{self.to_r})"


@dataclass
class PlaceAircraftAction(Action):
    """Represents placing an Aircraft on the map during Flight phase"""
    to_q: int
    to_r: int

    def __init__(self, unit_id: str, to_q: int, to_r: int):
        super().__init__(unit_id, "place_aircraft")
        self.to_q = to_q
        self.to_r = to_r

    def __str__(self):
        return f"Place Aircraft {self.unit_id} at ({self.to_q},{self.to_r})"


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
        # Strike and Fade moves have different validation
        is_strike_and_fade = getattr(action, 'is_strike_and_fade', False)
        is_relocate = getattr(action, 'is_relocate', False)

        if is_strike_and_fade:
            # Strike and Fade: check if the unit has the ability enabled
            unit_state = game_state.get_unit_state(unit.id)
            if not unit_state or not unit_state.strike_and_fade_available:
                return ActionValidation(False, "Strike and Fade not available")
        elif is_relocate:
            # Relocate: allowed during assault phase even if moved in movement phase
            # But can only relocate once per turn
            unit_state = game_state.get_unit_state(unit.id)
            if not unit_state:
                return ActionValidation(False, "Unit not found")
            # Check if already moved this turn (prevents multiple relocates)
            if unit_state.has_moved:
                return ActionValidation(False, "Unit has already moved/relocated this turn")
        else:
            # Normal move: check if unit has already moved this turn
            if game_state.has_unit_moved(unit.id):
                return ActionValidation(False, "Unit has already moved this turn")

        # Check if destination is reachable
        reachable = self.movement_system.get_reachable_hexes(
            self.board, action.from_q, action.from_r, unit
        )

        if (action.to_q, action.to_r) not in reachable:
            return ActionValidation(False, f"Hex ({action.to_q},{action.to_r}) is not reachable")

        # Check if destination is occupied
        # Obstacles don't count for stacking - any unit can enter obstacle hexes
        dest_hex = self.board.get_hex(action.to_q, action.to_r)
        if dest_hex and dest_hex.unit is not None:
            dest_is_obstacle = getattr(dest_hex.unit, 'unit_type', None) == 'Obstacle'
            if not dest_is_obstacle:
                return ActionValidation(False, "Destination hex is occupied")

        return ActionValidation(True)
    
    def validate_attack(self, action: AttackAction, unit, target, game_state) -> ActionValidation:
        """Validate an attack action"""
        # Check if unit can still attack (supports Double Shot - allows 2 attacks)
        if not game_state.can_unit_attack(unit.id):
            return ActionValidation(False, "Unit has already used all attacks this turn")
        
        # Check range - calculate max range from attack values
        # Long range is 5-8 hexes, so max theoretical range is 8
        max_range = 0
        if getattr(unit, 'veh_long', 0) > 0 or getattr(unit, 'per_long', 0) > 0:
            max_range = 8
        elif getattr(unit, 'veh_medium', 0) > 0 or getattr(unit, 'per_medium', 0) > 0:
            max_range = 4
        elif getattr(unit, 'veh_short', 0) > 0 or getattr(unit, 'per_short', 0) > 0:
            max_range = 1
        
        if action.distance > max_range:
            return ActionValidation(False, f"Target out of range (distance: {action.distance}, max: {max_range})")
        
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