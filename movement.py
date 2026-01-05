from typing import List, Set, Tuple, Optional
from collections import deque
from board import Board, Hex

class MovementSystem:
    """Handles unit movement and line of sight with ability integration"""
    
    # Base terrain movement costs (how many movement points to enter)
    BASE_TERRAIN_COSTS = {
        'open': 1,
        'road': 1,      # Roads don't slow you down
        'forest': 2,    # Forests cost extra movement
        'hill': 2,      # Hills cost extra movement
        'building': 1,  # Buildings normal cost
        'water': 99     # Water is impassable for most units
    }
    
    def __init__(self, ability_system=None):
        """Initialize with optional ability system"""
        self.ability_system = ability_system
    
    def get_terrain_cost(self, unit, terrain: str) -> int:
        """
        Get movement cost for terrain, considering unit abilities.
        """
        base_cost = self.BASE_TERRAIN_COSTS.get(terrain, 1)
        
        # Check for abilities that modify terrain costs
        if self.ability_system:
            movement_mods = self.ability_system.get_movement_modifiers(unit)
            
            # Check for specific terrain penalties/bonuses
            if terrain in movement_mods.get('terrain_penalties', {}):
                return movement_mods['terrain_penalties'][terrain]
            
            # Road bonus
            if terrain == 'road' and movement_mods.get('road_bonus'):
                return 0  # Roads are free with road bonus
        
        return base_cost
    
    def get_effective_speed(self, unit, ability_mods: dict = None) -> int:
        """
        Get unit's effective speed considering abilities.
        """
        base_speed = unit.speed
        
        # Handle aircraft speed 'A'
        if isinstance(base_speed, str):
            return 0  # Aircraft don't use ground movement
        
        if ability_mods:
            base_speed += ability_mods.get('speed_bonus', 0)
            base_speed -= ability_mods.get('speed_penalty', 0)
        
        return max(0, base_speed)
    
    def can_unit_move_and_attack(self, unit) -> bool:
        """
        Check if unit can both move and attack in same turn.
        Some abilities prevent this (e.g., must set up heavy weapons).
        """
        if not self.ability_system:
            return True
        
        movement_mods = self.ability_system.get_movement_modifiers(unit)
        return movement_mods.get('can_move_and_shoot', True)
    
    def get_reachable_hexes(self, board: Board, start_q: int, start_r: int, 
                           unit, max_speed: int = None) -> Set[Tuple[int, int]]:
        """
        Get all hexes reachable from start position with given speed.
        Returns set of (q, r) coordinates.
        Uses breadth-first search with movement costs and ability modifiers.
        """
        # Get movement modifiers from abilities
        if self.ability_system:
            movement_mods = self.ability_system.get_movement_modifiers(unit)
            
            # Obstacles can't move
            if self.ability_system.is_obstacle_unit(unit):
                return {(start_q, start_r)}
        else:
            movement_mods = {}
        
        # Get effective speed
        if max_speed is None:
            max_speed = self.get_effective_speed(unit, movement_mods)
        
        reachable = set()
        reachable.add((start_q, start_r))
        
        # Queue: (q, r, movement_remaining)
        queue = deque([(start_q, start_r, max_speed)])
        visited = {(start_q, start_r): max_speed}
        
        while queue:
            q, r, movement = queue.popleft()
            
            # Check all neighbors
            neighbors = board.get_neighbors(q, r)
            for neighbor in neighbors:
                nq, nr = neighbor.q, neighbor.r
                terrain_cost = self.get_terrain_cost(unit, neighbor.terrain)
                
                # Skip if terrain is impassable
                if terrain_cost >= 99:
                    continue
                
                # Skip if hex is occupied by another unit
                # (In real game, friendly units don't block, enemies do)
                if neighbor.unit is not None:
                    continue
                
                new_movement = movement - terrain_cost
                
                # If we can reach this hex and haven't visited it with more movement
                if new_movement >= 0:
                    # Only add if we haven't been here, or we got here with more movement
                    if (nq, nr) not in visited or visited[(nq, nr)] < new_movement:
                        visited[(nq, nr)] = new_movement
                        reachable.add((nq, nr))
                        queue.append((nq, nr, new_movement))
        
        return reachable
    
    def get_assault_move_range(self, unit) -> int:
        """
        Get how far a unit can move in assault phase.
        Some abilities (Aggression, Strike and Fade) allow movement before/after attacking.
        """
        if not self.ability_system:
            return 0
        
        movement_mods = self.ability_system.get_movement_modifiers(unit)
        
        # Check for assault movement abilities
        for note in movement_mods.get('notes', []):
            # Parse "Aggression 1: Can move 1 before attacking"
            if 'Aggression' in note or 'move' in note.lower():
                # Extract number from note
                import re
                match = re.search(r'move (\d+)', note.lower())
                if match:
                    return int(match.group(1))
        
        # Can move in assault phase? (Strike and Fade, etc.)
        if movement_mods.get('can_assault_move'):
            return unit.speed if isinstance(unit.speed, int) else 0
        
        return 0
    
    def has_line_of_sight(self, board: Board, unit, 
                         q1: int, r1: int, q2: int, r2: int) -> Tuple[bool, List[Hex]]:
        """
        Check if there's line of sight between two hexes, considering unit abilities.
        Returns (has_los, blocking_hexes)
        """
        # Get all hexes along the line
        line_hexes = self._get_line_hexes(board, q1, r1, q2, r2)
        
        blocking_terrain = ['forest', 'building']
        blocking_hexes = []
        
        # Check each hex along the line (excluding start and end)
        for hex_tile in line_hexes[1:-1]:  # Skip first and last
            if hex_tile.terrain in blocking_terrain:
                blocking_hexes.append(hex_tile)
        
        # Check if unit has abilities to see through obstacles
        if self.ability_system and blocking_hexes:
            can_see_through = not self.ability_system.check_los_blocked(
                unit, 
                line_hexes[-1].terrain if line_hexes else 'open',
                blocking_hexes
            )
            if can_see_through:
                return True, []
        
        has_los = len(blocking_hexes) == 0
        return has_los, blocking_hexes
    
    @staticmethod
    def _get_line_hexes(board: Board, q1: int, r1: int, 
                       q2: int, r2: int) -> List[Hex]:
        """
        Get all hexes along a line between two points.
        Uses linear interpolation in cube coordinates.
        Line is measured from CENTER of hex to CENTER of target hex.
        """
        # Convert axial to cube coordinates
        def axial_to_cube(q, r):
            x = q
            z = r
            y = -x - z
            return x, y, z
        
        def cube_to_axial(x, y, z):
            q = x
            r = z
            return q, r
        
        def cube_round(x, y, z):
            rx = round(x)
            ry = round(y)
            rz = round(z)
            
            x_diff = abs(rx - x)
            y_diff = abs(ry - y)
            z_diff = abs(rz - z)
            
            if x_diff > y_diff and x_diff > z_diff:
                rx = -ry - rz
            elif y_diff > z_diff:
                ry = -rx - rz
            else:
                rz = -rx - ry
            
            return rx, ry, rz
        
        # Convert to cube coordinates
        x1, y1, z1 = axial_to_cube(q1, r1)
        x2, y2, z2 = axial_to_cube(q2, r2)
        
        # Calculate distance
        distance = board.hex_distance(q1, r1, q2, r2)
        
        # Interpolate along the line
        hexes = []
        for i in range(distance + 1):
            t = i / max(distance, 1)
            x = x1 + (x2 - x1) * t
            y = y1 + (y2 - y1) * t
            z = z1 + (z2 - z1) * t
            
            # Round to nearest hex
            rx, ry, rz = cube_round(x, y, z)
            q, r = cube_to_axial(rx, ry, rz)
            
            hex_tile = board.get_hex(q, r)
            if hex_tile:
                hexes.append(hex_tile)
        
        return hexes
    
    @staticmethod
    def get_range_category(distance: int) -> str:
        """Convert hex distance to range category (short/medium/long)"""
        if distance <= 1:
            return 'short'
        elif distance <= 4:
            return 'medium'
        else:
            return 'long'


# Test the updated movement system
if __name__ == "__main__":
    from units import load_units
    from board import Board
    from abilities import AbilitySystem
    
    print("=== MOVEMENT SYSTEM WITH ABILITIES ===\n")
    
    # Load systems
    ability_system = AbilitySystem('Axis and Allies Unit Data for Analysis - Special_Abilities.csv')
    movement_system = MovementSystem(ability_system)
    units = load_units()
    
    # Create a test board
    board = Board(width=15, height=15)
    
    # Add some terrain
    # Forest area
    for q in range(6, 9):
        for r in range(6, 9):
            board.set_terrain(q, r, 'forest')
    
    # Road
    for i in range(15):
        board.set_terrain(i, 7, 'road')
    
    # Hill
    board.set_terrain(10, 5, 'hill')
    
    # Get test units
    combat_units = [u for u in units if not ability_system.is_obstacle_unit(u)]
    
    # Find units with interesting movement abilities
    infantry = [u for u in combat_units if u.unit_type == 'Soldier' and u.speed == 1][0]
    tank = [u for u in combat_units if u.unit_type == 'Vehicle' and isinstance(u.speed, int) and u.speed >= 4][0]
    
    # Find unit with special movement ability if available
    special_movement = [u for u in combat_units if 
                       ability_system.unit_has_any_ability_in_category(u, 'special_movement')]
    special_unit = special_movement[0] if special_movement else None
    
    print(f"=== Testing Movement System ===\n")
    print(f"Infantry: {infantry.name}, Speed: {infantry.speed}")
    print(f"Tank: {tank.name}, Speed: {tank.speed}")
    if special_unit:
        print(f"Special: {special_unit.name}, Speed: {special_unit.speed}")
        print(f"  Abilities: {', '.join(special_unit.abilities[:3])}")
    
    # Test 1: Basic infantry movement
    print(f"\n{'='*70}")
    print(f"TEST 1: Infantry movement (speed {infantry.speed})")
    print(f"{'='*70}")
    
    reachable = movement_system.get_reachable_hexes(board, 5, 5, infantry)
    print(f"From (5,5), infantry can reach {len(reachable)} hexes")
    print(f"Sample positions: {sorted(list(reachable))[:10]}")
    
    # Check movement modifiers
    movement_mods = ability_system.get_movement_modifiers(infantry)
    print(f"\nMovement modifiers for {infantry.name}:")
    if movement_mods['notes']:
        for note in movement_mods['notes']:
            print(f"  • {note}")
    else:
        print(f"  • No special movement abilities")
    
    # Test 2: Tank movement
    print(f"\n{'='*70}")
    print(f"TEST 2: Tank movement (speed {tank.speed})")
    print(f"{'='*70}")
    
    reachable_tank = movement_system.get_reachable_hexes(board, 3, 3, tank)
    print(f"From (3,3), tank can reach {len(reachable_tank)} hexes")
    
    movement_mods_tank = ability_system.get_movement_modifiers(tank)
    if movement_mods_tank['notes']:
        print(f"Tank movement abilities:")
        for note in movement_mods_tank['notes']:
            print(f"  • {note}")
    
    # Test 3: Line of sight with abilities
    print(f"\n{'='*70}")
    print(f"TEST 3: Line of Sight")
    print(f"{'='*70}")
    
    # Clear LOS
    has_los, blocking = movement_system.has_line_of_sight(board, infantry, 3, 3, 5, 5)
    print(f"LOS from (3,3) to (5,5): {has_los}")
    if blocking:
        print(f"  Blocked by: {[(h.q, h.r, h.terrain) for h in blocking]}")
    
    # Blocked LOS (through forest)
    has_los, blocking = movement_system.has_line_of_sight(board, infantry, 5, 5, 10, 10)
    print(f"\nLOS from (5,5) to (10,10) through forest: {has_los}")
    if blocking:
        print(f"  Blocked by: {[(h.q, h.r, h.terrain) for h in blocking]}")
    
    # Test 4: Special movement abilities
    if special_unit:
        print(f"\n{'='*70}")
        print(f"TEST 4: Special Movement - {special_unit.name}")
        print(f"{'='*70}")
        
        assault_move = movement_system.get_assault_move_range(special_unit)
        print(f"Assault phase movement: {assault_move} hexes")
        
        can_move_and_shoot = movement_system.can_unit_move_and_attack(special_unit)
        print(f"Can move and attack: {can_move_and_shoot}")
    
    # Test 5: Terrain cost modifiers
    print(f"\n{'='*70}")
    print(f"TEST 5: Terrain Movement Costs")
    print(f"{'='*70}")
    
    terrains = ['open', 'forest', 'road', 'hill', 'building']
    print(f"Movement costs for {infantry.name}:")
    for terrain in terrains:
        cost = movement_system.get_terrain_cost(infantry, terrain)
        print(f"  {terrain}: {cost} movement point(s)")
    
    print("\n✅ Movement system with abilities test complete!")