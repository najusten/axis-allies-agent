from typing import List, Set, Tuple, Optional
from collections import deque
from board import Board, Hex

class MovementSystem:
    """Handles unit movement and line of sight"""
    
    # Terrain movement costs (how many movement points to enter)
    TERRAIN_COSTS = {
        'open': 1,
        'road': 1,      # Roads don't slow you down
        'forest': 2,    # Forests cost extra movement
        'hill': 2,      # Hills cost extra movement
        'building': 1,  # Buildings normal cost
        'water': 99     # Water is impassable for most units
    }
    
    @staticmethod
    def get_reachable_hexes(board: Board, start_q: int, start_r: int, 
                           max_speed: int) -> Set[Tuple[int, int]]:
        """
        Get all hexes reachable from start position with given speed.
        Returns set of (q, r) coordinates.
        Uses breadth-first search with movement costs.
        """
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
                terrain_cost = MovementSystem.TERRAIN_COSTS.get(neighbor.terrain, 1)
                
                # Skip if terrain is impassable
                if terrain_cost >= 99:
                    continue
                
                # Skip if hex is occupied by another unit
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
    
    @staticmethod
    def has_line_of_sight(board: Board, q1: int, r1: int, 
                         q2: int, r2: int) -> Tuple[bool, List[Hex]]:
        """
        Check if there's line of sight between two hexes.
        Returns (has_los, blocking_hexes)
        
        Simple implementation: forests and buildings block LOS
        """
        # Get all hexes along the line
        line_hexes = MovementSystem._get_line_hexes(board, q1, r1, q2, r2)
        
        blocking_terrain = ['forest', 'building']
        blocking_hexes = []
        
        # Check each hex along the line (excluding start and end)
        for hex_tile in line_hexes[1:-1]:  # Skip first and last
            if hex_tile.terrain in blocking_terrain:
                blocking_hexes.append(hex_tile)
        
        has_los = len(blocking_hexes) == 0
        return has_los, blocking_hexes
    
    @staticmethod
    def _get_line_hexes(board: Board, q1: int, r1: int, 
                       q2: int, r2: int) -> List[Hex]:
        """
        Get all hexes along a line between two points.
        Uses linear interpolation in cube coordinates.
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


# Test the movement system
if __name__ == "__main__":
    from units import load_units
    from board import Board
    
    # Create a test board
    board = Board(width=15, height=15)
    
    # Add some terrain
    # Forest area
    for q in range(6, 9):
        for r in range(6, 9):
            board.set_terrain(q, r, 'forest')
    
    # Hill
    board.set_terrain(10, 5, 'hill')
    
    # Load a unit
    units = load_units()
    infantry = [u for u in units if u.unit_type == 'Soldier' and u.speed == 1][0]
    tank = [u for u in units if u.unit_type == 'Vehicle' and u.speed >= 4][0]
    
    print(f"=== Testing Movement System ===\n")
    print(f"Infantry unit: {infantry.name}, Speed: {infantry.speed}")
    print(f"Tank unit: {tank.name}, Speed: {tank.speed}\n")
    
    # Place infantry at (5, 5)
    board.place_unit(infantry, 5, 5)
    
    # Calculate reachable hexes
    reachable = MovementSystem.get_reachable_hexes(board, 5, 5, infantry.speed)
    print(f"Infantry at (5,5) can reach {len(reachable)} hexes with speed {infantry.speed}")
    print(f"Reachable positions: {sorted(reachable)[:10]}...\n")
    
    # Test tank movement
    board.place_unit(tank, 3, 3)
    reachable_tank = MovementSystem.get_reachable_hexes(board, 3, 3, tank.speed)
    print(f"Tank at (3,3) can reach {len(reachable_tank)} hexes with speed {tank.speed}\n")
    
    # Test line of sight
    print("=== Testing Line of Sight ===\n")
    
    # Clear LOS
    has_los, blocking = MovementSystem.has_line_of_sight(board, 3, 3, 5, 5)
    print(f"LOS from (3,3) to (5,5): {has_los}")
    if blocking:
        print(f"  Blocked by: {[f'{h.q},{h.r} ({h.terrain})' for h in blocking]}")
    
    # Blocked LOS (through forest)
    has_los, blocking = MovementSystem.has_line_of_sight(board, 5, 5, 10, 10)
    print(f"\nLOS from (5,5) to (10,10): {has_los}")
    if blocking:
        print(f"  Blocked by: {[f'({h.q},{h.r}) {h.terrain}' for h in blocking]}")
    
    # Test range categories
    print(f"\n=== Range Categories ===")
    for dist in [1, 2, 4, 5, 8]:
        category = MovementSystem.get_range_category(dist)
        print(f"Distance {dist} hexes = {category} range")