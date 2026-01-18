"""
Facing System for Axis & Allies Miniatures

Implements vehicle facing mechanics for front/rear armor.

Key Rules:
- Vehicles must face one of 6 hex sides
- Front arc = 3 hex sides (the facing direction + 2 adjacent sides)
- Rear arc = opposite 3 hex sides + same hex + exactly perpendicular (side) shots
- Soldiers and Aircraft have NO facing
- Changing facing is free during movement
- Disrupted vehicles cannot change facing
- During defensive fire, vehicles face toward the hex they're entering
"""

from typing import Tuple, Optional, List, TYPE_CHECKING
from enum import IntEnum
from dataclasses import dataclass

if TYPE_CHECKING:
    from units import Unit
    from game_state import UnitState


class HexDirection(IntEnum):
    """
    The 6 hex directions in axial coordinates.
    Direction 0 is "East" (+q direction), going clockwise.
    
    In a pointy-top hex grid:
        Direction 0: East       (+1, 0)
        Direction 1: Southeast  (+1, -1)  
        Direction 2: Southwest  (0, -1)
        Direction 3: West       (-1, 0)
        Direction 4: Northwest  (-1, +1)
        Direction 5: Northeast  (0, +1)
    """
    EAST = 0
    SOUTHEAST = 1
    SOUTHWEST = 2
    WEST = 3
    NORTHWEST = 4
    NORTHEAST = 5


# Direction vectors for each hex direction (dq, dr)
DIRECTION_VECTORS = {
    HexDirection.EAST: (1, 0),
    HexDirection.SOUTHEAST: (1, -1),
    HexDirection.SOUTHWEST: (0, -1),
    HexDirection.WEST: (-1, 0),
    HexDirection.NORTHWEST: (-1, +1),
    HexDirection.NORTHEAST: (0, +1),
}

# Reverse lookup: vector -> direction
VECTOR_TO_DIRECTION = {v: k for k, v in DIRECTION_VECTORS.items()}


def get_direction_name(direction: HexDirection) -> str:
    """Get human-readable name for a direction"""
    names = {
        HexDirection.EAST: "East",
        HexDirection.SOUTHEAST: "Southeast", 
        HexDirection.SOUTHWEST: "Southwest",
        HexDirection.WEST: "West",
        HexDirection.NORTHWEST: "Northwest",
        HexDirection.NORTHEAST: "Northeast",
    }
    return names.get(direction, "Unknown")


def get_opposite_direction(direction: HexDirection) -> HexDirection:
    """Get the opposite direction (180 degrees)"""
    return HexDirection((direction + 3) % 6)


def get_adjacent_directions(direction: HexDirection) -> Tuple[HexDirection, HexDirection]:
    """Get the two directions adjacent to this one (clockwise and counter-clockwise)"""
    clockwise = HexDirection((direction + 1) % 6)
    counter_clockwise = HexDirection((direction - 1) % 6)
    return (counter_clockwise, clockwise)


def get_front_arc_directions(facing: HexDirection) -> List[HexDirection]:
    """
    Get the 3 directions that make up the front arc.
    Front arc = facing direction + the two adjacent directions.
    """
    left, right = get_adjacent_directions(facing)
    return [left, facing, right]


def get_rear_arc_directions(facing: HexDirection) -> List[HexDirection]:
    """
    Get the 3 directions that make up the rear arc.
    Rear arc = opposite direction + the two adjacent to opposite.
    """
    opposite = get_opposite_direction(facing)
    left, right = get_adjacent_directions(opposite)
    return [left, opposite, right]


def direction_from_positions(
    from_pos: Tuple[int, int], 
    to_pos: Tuple[int, int]
) -> Optional[HexDirection]:
    """
    Determine which hex direction goes from from_pos to to_pos.
    Returns None if positions are not adjacent or are the same.
    """
    dq = to_pos[0] - from_pos[0]
    dr = to_pos[1] - from_pos[1]
    
    return VECTOR_TO_DIRECTION.get((dq, dr))


def get_attack_direction(
    attacker_pos: Tuple[int, int],
    defender_pos: Tuple[int, int]
) -> Optional[HexDirection]:
    """
    Determine which direction an attack is coming FROM, relative to defender.
    This is the direction from defender toward attacker.
    
    Returns None if same hex or not adjacent.
    """
    if attacker_pos == defender_pos:
        return None  # Same hex - always rear
    
    # Direction from defender to attacker
    return direction_from_positions(defender_pos, attacker_pos)


def is_front_arc_attack(
    attacker_pos: Tuple[int, int],
    defender_pos: Tuple[int, int],
    defender_facing: HexDirection
) -> bool:
    """
    Determine if an attack hits the front or rear arc.
    
    Rules:
    - Same hex = rear
    - Directly perpendicular (exactly 90 degrees) = rear (side shot)
    - Front 3 hex directions = front
    - Everything else = rear
    
    Args:
        attacker_pos: Position of attacking unit
        defender_pos: Position of defending vehicle
        defender_facing: Direction the defender is facing
    
    Returns:
        True if attack is against front arc, False if rear arc
    """
    # Same hex is ALWAYS rear
    if attacker_pos == defender_pos:
        return False
    
    # Get direction attack is coming from
    attack_dir = get_attack_direction(attacker_pos, defender_pos)
    
    if attack_dir is None:
        # Not adjacent - need to calculate general direction
        # For non-adjacent attacks, use the rough direction
        dq = attacker_pos[0] - defender_pos[0]
        dr = attacker_pos[1] - defender_pos[1]
        
        # Normalize to find closest hex direction
        # This is a simplification for long-range attacks
        attack_dir = _approximate_direction(dq, dr)
    
    # Get front arc directions
    front_arc = get_front_arc_directions(defender_facing)
    
    # Check if attack direction is in front arc
    return attack_dir in front_arc


def _approximate_direction(dq: int, dr: int) -> HexDirection:
    """
    Approximate the hex direction for non-adjacent positions.
    Uses the dominant direction components.
    """
    # In axial coordinates, we need to consider q, r, and s (where s = -q-r)
    ds = -dq - dr
    
    # Find the two largest absolute values
    coords = [('q', dq), ('r', dr), ('s', ds)]
    coords.sort(key=lambda x: abs(x[1]), reverse=True)
    
    # Use the signs of the two dominant coordinates to determine direction
    # This maps to one of the 6 hex directions
    if dq > 0 and dr >= 0:
        return HexDirection.EAST if dq >= dr else HexDirection.NORTHEAST
    elif dq > 0 and dr < 0:
        return HexDirection.EAST if dq > -dr else HexDirection.SOUTHEAST
    elif dq <= 0 and dr < 0:
        return HexDirection.WEST if -dq >= -dr else HexDirection.SOUTHWEST
    elif dq < 0 and dr >= 0:
        return HexDirection.WEST if -dq > dr else HexDirection.NORTHWEST
    elif dq == 0 and dr > 0:
        return HexDirection.NORTHEAST
    elif dq == 0 and dr < 0:
        return HexDirection.SOUTHWEST
    else:
        return HexDirection.EAST  # Default


def calculate_facing_after_move(
    from_pos: Tuple[int, int],
    to_pos: Tuple[int, int]
) -> HexDirection:
    """
    Calculate the facing direction after a move.
    Vehicles face the direction they moved.
    """
    direction = direction_from_positions(from_pos, to_pos)
    
    if direction is not None:
        return direction
    else:
        # Non-adjacent move (shouldn't happen normally)
        # Approximate the direction
        dq = to_pos[0] - from_pos[0]
        dr = to_pos[1] - from_pos[1]
        return _approximate_direction(dq, dr)


def calculate_facing_for_defensive_fire(
    vehicle_from_pos: Tuple[int, int],
    vehicle_to_pos: Tuple[int, int]
) -> HexDirection:
    """
    Calculate vehicle facing during defensive fire.
    
    Rule: Vehicle faces toward the hex it is entering.
    This means attacks during defensive fire hit the FRONT.
    """
    return calculate_facing_after_move(vehicle_from_pos, vehicle_to_pos)


class FacingSystem:
    """
    Manages facing for all units in the game.
    
    Only Vehicles have facing. Soldiers and Aircraft do not.
    """
    
    def __init__(self):
        # Store facing for each unit by ID
        # Only vehicles need entries here
        self._unit_facing: dict[str, HexDirection] = {}
    
    def set_facing(self, unit_id: str, facing: HexDirection):
        """Set facing for a unit"""
        self._unit_facing[unit_id] = facing
    
    def get_facing(self, unit_id: str) -> Optional[HexDirection]:
        """Get facing for a unit. Returns None if not set or not a vehicle."""
        return self._unit_facing.get(unit_id)
    
    def update_facing_after_move(
        self,
        unit_id: str,
        unit_type: str,
        from_pos: Tuple[int, int],
        to_pos: Tuple[int, int]
    ) -> Optional[HexDirection]:
        """
        Update facing after a unit moves.
        Only vehicles update facing.
        
        Returns new facing or None if unit doesn't use facing.
        """
        if unit_type != 'Vehicle':
            return None
        
        new_facing = calculate_facing_after_move(from_pos, to_pos)
        self._unit_facing[unit_id] = new_facing
        return new_facing
    
    def set_initial_facing(
        self,
        unit_id: str,
        unit_type: str,
        facing: HexDirection = HexDirection.EAST
    ):
        """Set initial facing for a unit during deployment."""
        if unit_type == 'Vehicle':
            self._unit_facing[unit_id] = facing
    
    def can_change_facing(self, unit_state: 'UnitState') -> bool:
        """
        Check if a unit can change facing.
        
        Rules:
        - Only vehicles have facing
        - Disrupted units cannot change facing (they cannot move)
        """
        if unit_state.unit.unit_type != 'Vehicle':
            return False
        
        if unit_state.is_disrupted:
            return False
        
        return True
    
    def get_defense_value(
        self,
        defender_unit: 'Unit',
        defender_pos: Tuple[int, int],
        attacker_pos: Tuple[int, int],
        defender_facing: Optional[HexDirection] = None
    ) -> int:
        """
        Get the appropriate defense value based on attack direction.
        
        Args:
            defender_unit: The defending unit
            defender_pos: Position of defender
            attacker_pos: Position of attacker
            defender_facing: Facing direction (only for vehicles)
        
        Returns:
            Front or rear defense value as appropriate
        """
        # Soldiers have no facing - always use front defense
        if defender_unit.unit_type != 'Vehicle':
            return defender_unit.defense_front or defender_unit.defense or 3
        
        # Vehicles need facing check
        if defender_facing is None:
            # No facing set - default to front
            return defender_unit.defense_front or 3
        
        # Check if attack is from front arc
        is_front = is_front_arc_attack(attacker_pos, defender_pos, defender_facing)
        
        if is_front:
            return defender_unit.defense_front or 3
        else:
            return defender_unit.defense_rear or defender_unit.defense_front or 3
    
    def remove_unit(self, unit_id: str):
        """Remove facing data for a destroyed unit."""
        self._unit_facing.pop(unit_id, None)


def demo_facing_system():
    """Demonstrate facing system mechanics"""
    print("=" * 70)
    print("FACING SYSTEM DEMONSTRATION")
    print("=" * 70)
    
    # Test direction calculations
    print("\n--- Direction Tests ---")
    
    # Test adjacent directions
    test_cases = [
        ((5, 5), (6, 5), "East"),
        ((5, 5), (6, 4), "Southeast"),
        ((5, 5), (5, 4), "Southwest"),
        ((5, 5), (4, 5), "West"),
        ((5, 5), (4, 6), "Northwest"),
        ((5, 5), (5, 6), "Northeast"),
    ]
    
    for from_pos, to_pos, expected in test_cases:
        direction = direction_from_positions(from_pos, to_pos)
        name = get_direction_name(direction) if direction is not None else "None"
        status = "✓" if name == expected else "✗"
        print(f"  {from_pos} -> {to_pos}: {name} (expected {expected}) {status}")
    
    # Test front/rear arc
    print("\n--- Front/Rear Arc Tests ---")
    print("Vehicle at (5,5) facing EAST")
    
    facing = HexDirection.EAST
    front_dirs = get_front_arc_directions(facing)
    rear_dirs = get_rear_arc_directions(facing)
    
    print(f"  Front arc: {[get_direction_name(d) for d in front_dirs]}")
    print(f"  Rear arc: {[get_direction_name(d) for d in rear_dirs]}")
    
    # Test attacks from various positions
    print("\n--- Attack Direction Tests ---")
    print("Vehicle at (5,5) facing EAST")
    
    attack_tests = [
        ((6, 5), "East attack", True),       # Direct front
        ((5, 6), "Northeast attack", True),  # Front arc
        ((4, 6), "Northwest attack", False), # Rear arc
        ((4, 5), "West attack", False),      # Direct rear
        ((5, 5), "Same hex", False),         # Always rear
        ((8, 5), "Long range East", True),   # Long range front
        ((2, 5), "Long range West", False),  # Long range rear
    ]
    
    for attacker_pos, desc, expected_front in attack_tests:
        is_front = is_front_arc_attack(attacker_pos, (5, 5), facing)
        arc = "FRONT" if is_front else "REAR"
        expected_arc = "FRONT" if expected_front else "REAR"
        status = "✓" if arc == expected_arc else "✗"
        print(f"  Attacker at {attacker_pos} ({desc}): {arc} (expected {expected_arc}) {status}")
    
    # Test facing system class
    print("\n--- FacingSystem Class Tests ---")
    
    facing_system = FacingSystem()
    
    # Set initial facing
    facing_system.set_initial_facing("tank_1", "Vehicle", HexDirection.NORTHEAST)
    facing_system.set_initial_facing("infantry_1", "Soldier", HexDirection.EAST)
    
    print(f"  Tank facing: {get_direction_name(facing_system.get_facing('tank_1'))}")
    print(f"  Infantry facing: {facing_system.get_facing('infantry_1')} (should be None)")
    
    # Update facing after move
    new_facing = facing_system.update_facing_after_move(
        "tank_1", "Vehicle", (5, 5), (6, 5)
    )
    print(f"  Tank moved East, new facing: {get_direction_name(new_facing)}")
    
    # Test defense value calculation
    print("\n--- Defense Value Tests ---")
    
    # Create a mock unit
    class MockUnit:
        def __init__(self):
            self.unit_type = 'Vehicle'
            self.defense_front = 6
            self.defense_rear = 4
            self.defense = 6
    
    mock_tank = MockUnit()
    
    # Attack from front
    defense = facing_system.get_defense_value(
        mock_tank, (5, 5), (6, 5), HexDirection.EAST
    )
    print(f"  Attack from East (front): defense = {defense} (expected 6)")
    
    # Attack from rear
    defense = facing_system.get_defense_value(
        mock_tank, (5, 5), (4, 5), HexDirection.EAST
    )
    print(f"  Attack from West (rear): defense = {defense} (expected 4)")
    
    # Attack from same hex
    defense = facing_system.get_defense_value(
        mock_tank, (5, 5), (5, 5), HexDirection.EAST
    )
    print(f"  Attack from same hex: defense = {defense} (expected 4)")
    
    print("\n" + "=" * 70)
    print("FACING SYSTEM DEMONSTRATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    demo_facing_system()