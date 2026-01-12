from typing import List, Set, Tuple, Optional
from collections import deque
from board import Board, Hex

class MovementSystem:
    """Handles unit movement and line of sight with ability integration"""
    
    # Base terrain movement costs (how many movement points to enter)
    BASE_TERRAIN_COSTS = {
        'open': 1,
        'road': 1,      # Roads don't slow you down
        'forest': 2,    # Forests cost extra for vehicles (infantry 1)
        'hill': 2,      # Hills cost extra for vehicles (infantry 1)
        'building': 1,  # Buildings normal cost
        'water': 99     # Water is impassable for most units
    }
    
    def __init__(self, ability_system=None):
        """Initialize with optional ability system"""
        self.ability_system = ability_system
    
    def get_terrain_cost(self, unit, terrain: str) -> int:
        """
        Get movement cost for terrain, considering unit abilities.
        NOTE: This is the BASE cost. Entry vs within-terrain is handled separately.
        """
        base_cost = self.BASE_TERRAIN_COSTS.get(terrain, 1)
        
        # Infantry/Soldiers: forests and hills cost 1 (not 2)
        if unit.unit_type == 'Soldier':
            if terrain in ['forest', 'hill']:
                return 1
        
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
    
    def _get_terrain_cost_with_entry(self, unit, current_terrain: str, previous_terrain: str) -> int:
        """
        Calculate movement cost considering whether unit is entering terrain or already in it.
        
        For Vehicles:
        - Entering forest/hill from non-forest/hill: 2 movement
        - Moving within forest (forest->forest): 1 movement
        - Moving within hills (hill->hill): 1 movement
        
        For Infantry: Always 1 for forest/hill
        """
        # Infantry always pays 1 for forest/hill
        if unit.unit_type == 'Soldier':
            if current_terrain in ['forest', 'hill']:
                return 1
            return self.BASE_TERRAIN_COSTS.get(current_terrain, 1)
        
        # Vehicles: Check if entering or staying in same terrain type
        if current_terrain in ['forest', 'hill']:
            # If coming from the same terrain type, cost is 1
            if previous_terrain == current_terrain:
                return 1
            # If entering from different terrain, cost is 2
            else:
                return 2
        
        # All other terrain uses base cost
        return self.BASE_TERRAIN_COSTS.get(current_terrain, 1)
    
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
        
        Important: Forest/Hill entry cost is 2 for vehicles, but moving within
        forest (forest->forest) is only 1.
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
        
        # Queue: (q, r, movement_remaining, previous_terrain)
        start_hex = board.get_hex(start_q, start_r)
        start_terrain = start_hex.terrain if start_hex else 'open'
        queue = deque([(start_q, start_r, max_speed, start_terrain)])
        
        # Track best movement remaining for each hex
        visited = {(start_q, start_r): max_speed}
        
        while queue:
            q, r, movement, prev_terrain = queue.popleft()
            
            # Check all neighbors
            neighbors = board.get_neighbors(q, r)
            for neighbor in neighbors:
                nq, nr = neighbor.q, neighbor.r
                
                # Skip if terrain is impassable
                if neighbor.terrain == 'water' or self.BASE_TERRAIN_COSTS.get(neighbor.terrain, 1) >= 99:
                    continue
                
                # Skip if hex is occupied by another unit
                if neighbor.unit is not None:
                    continue
                
                # Calculate terrain cost considering entry vs within-terrain movement
                terrain_cost = self._get_terrain_cost_with_entry(
                    unit, neighbor.terrain, prev_terrain
                )
                
                new_movement = movement - terrain_cost
                
                # If we can reach this hex and haven't visited it with more movement
                if new_movement >= 0:
                    # Don't revisit the starting position
                    if (nq, nr) == (start_q, start_r):
                        continue
                    
                    # Only add if we haven't been here, or we got here with more movement
                    if (nq, nr) not in visited or visited[(nq, nr)] < new_movement:
                        visited[(nq, nr)] = new_movement
                        reachable.add((nq, nr))
                        queue.append((nq, nr, new_movement, neighbor.terrain))
        
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
    
    @staticmethod
    def _get_hex_edges(hex_q: int, hex_r: int) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
        """
        Get the 6 edges of a hex as line segments in cartesian coordinates.
        
        In our coordinate system:
        - x = q + r * 0.5
        - y = r * √3/2
        - Adjacent hexes in q-direction are 1.0 apart in x
        - Therefore flat-to-flat width = 1.0
        - Inradius = 0.5
        - Circumradius = 1/√3 ≈ 0.577
        """
        import math
        
        # Convert hex center to cartesian
        cx = hex_q + hex_r * 0.5
        cy = hex_r * (3**0.5 / 2)
        
        # For our coordinate system, circumradius = 1/√3
        radius = 1.0 / (3**0.5)  # ≈ 0.577
        
        # Flat-top hex: first vertex at 30 degrees
        vertices = []
        for i in range(6):
            angle = math.pi / 6 + i * math.pi / 3  # 30°, 90°, 150°, 210°, 270°, 330°
            vx = cx + radius * math.cos(angle)
            vy = cy + radius * math.sin(angle)
            vertices.append((vx, vy))
        
        # Create edges connecting consecutive vertices
        edges = []
        for i in range(6):
            v1 = vertices[i]
            v2 = vertices[(i + 1) % 6]
            edges.append((v1, v2))
        
        return edges
    
    @staticmethod
    def _point_on_line_segment(px: float, py: float, 
                               x1: float, y1: float, 
                               x2: float, y2: float,
                               tolerance: float = 0.01) -> bool:
        """
        Check if point (px, py) lies on line segment from (x1,y1) to (x2,y2).
        Uses tolerance for floating point comparison.
        """
        # Check if point is within bounding box
        if not (min(x1, x2) - tolerance <= px <= max(x1, x2) + tolerance and
                min(y1, y2) - tolerance <= py <= max(y1, y2) + tolerance):
            return False
        
        # Check collinearity: cross product should be near zero
        dx1 = px - x1
        dy1 = py - y1
        dx2 = x2 - x1
        dy2 = y2 - y1
        
        cross = abs(dx1 * dy2 - dy1 * dx2)
        
        # If cross product is near zero, point is on the line
        return cross < tolerance
    
    @staticmethod
    def _line_segments_overlap(seg1_start: Tuple[float, float], seg1_end: Tuple[float, float],
                              seg2_start: Tuple[float, float], seg2_end: Tuple[float, float],
                              tolerance: float = 0.01) -> bool:
        """
        Check if two line segments overlap (share a portion of their length).
        Returns True if segments are collinear and overlap.
        
        Tolerance is only for floating-point precision, not game rules.
        The game rule requires EXACT collinearity.
        """
        x1, y1 = seg1_start
        x2, y2 = seg1_end
        x3, y3 = seg2_start
        x4, y4 = seg2_end
        
        # Check if segments are collinear
        # Both endpoints of seg2 should be on line of seg1
        dx = x2 - x1
        dy = y2 - y1
        
        # Avoid division by zero
        if abs(dx) < tolerance and abs(dy) < tolerance:
            return False
        
        # Check collinearity using cross products
        cross1 = abs((x3 - x1) * dy - (y3 - y1) * dx)
        cross2 = abs((x4 - x1) * dy - (y4 - y1) * dx)
        
        if cross1 > tolerance or cross2 > tolerance:
            return False  # Not collinear
        
        # Segments are collinear, check if they overlap
        # Project onto primary axis (x or y, whichever has more variation)
        if abs(dx) > abs(dy):
            # Project onto x-axis
            t1_start = 0
            t1_end = 1
            t2_start = (x3 - x1) / dx if abs(dx) > tolerance else 0
            t2_end = (x4 - x1) / dx if abs(dx) > tolerance else 0
        else:
            # Project onto y-axis
            t1_start = 0
            t1_end = 1
            t2_start = (y3 - y1) / dy if abs(dy) > tolerance else 0
            t2_end = (y4 - y1) / dy if abs(dy) > tolerance else 0
        
        # Normalize so start < end
        if t2_start > t2_end:
            t2_start, t2_end = t2_end, t2_start
        
        # Check for overlap
        overlap = max(t1_start, t2_start) <= min(t1_end, t2_end)
        
        return overlap
    
    @staticmethod
    def _los_passes_along_hex_edge(q1: int, r1: int, q2: int, r2: int,
                                   hex_q: int, hex_r: int,
                                   tolerance: float = 0.01) -> bool:
        """
        Check if LOS from (q1,r1) to (q2,r2) passes EXACTLY along any edge of hex (hex_q, hex_r).
        Returns True if LOS line is collinear with and overlaps a hex edge.
        
        Tolerance is only for floating-point precision, not game rules.
        """
        # Convert LOS to cartesian
        x1 = q1 + r1 * 0.5
        y1 = r1 * (3**0.5 / 2)
        x2 = q2 + r2 * 0.5
        y2 = r2 * (3**0.5 / 2)
        
        los_segment = ((x1, y1), (x2, y2))
        
        # Get all edges of the hex
        hex_edges = MovementSystem._get_hex_edges(hex_q, hex_r)
        
        # Check if LOS overlaps with any edge
        for edge in hex_edges:
            if MovementSystem._line_segments_overlap(los_segment[0], los_segment[1],
                                                     edge[0], edge[1],
                                                     tolerance):
                return True
        
        return False
    
    @staticmethod
    def _line_intersects_hex_interior(q1: int, r1: int, q2: int, r2: int, 
                                       hex_q: int, hex_r: int,
                                       tolerance: float = 0.01) -> bool:
        """
        Check if line from (q1,r1) to (q2,r2) passes through hex interior
        (not just along an edge).
        
        Returns True if line passes through interior.
        Returns False if line only grazes edge or misses hex.
        """
        # First check if LOS passes along hex edge
        if MovementSystem._los_passes_along_hex_edge(q1, r1, q2, r2, hex_q, hex_r, tolerance):
            return False  # Passes along edge, not through interior
        
        # Convert hex centers to cartesian coordinates
        x1 = q1 + r1 * 0.5
        y1 = r1 * (3**0.5 / 2)
        
        x2 = q2 + r2 * 0.5
        y2 = r2 * (3**0.5 / 2)
        
        hex_x = hex_q + hex_r * 0.5
        hex_y = hex_r * (3**0.5 / 2)
        
        # Calculate closest point on line segment to hex center
        dx = x2 - x1
        dy = y2 - y1
        
        if dx == 0 and dy == 0:
            return False
        
        # Parameter t of closest point: P = P1 + t*(P2-P1)
        t = max(0, min(1, ((hex_x - x1) * dx + (hex_y - y1) * dy) / (dx*dx + dy*dy)))
        
        # Closest point on line segment
        closest_x = x1 + t * dx
        closest_y = y1 + t * dy
        
        # Distance from hex center to closest point on line
        dist = ((hex_x - closest_x)**2 + (hex_y - closest_y)**2) ** 0.5
        
        # Hex inradius = 0.5 in our coordinate system
        inradius = 0.5
        
        return dist < inradius - tolerance
    
    def has_line_of_sight(self, board: Board, unit, 
                         q1: int, r1: int, q2: int, r2: int) -> Tuple[bool, List[Hex]]:
        """
        Check if there's line of sight between two hexes, considering unit abilities.
        
        LOS Rules (PROPER GEOMETRIC IMPLEMENTATION):
        - LOS is drawn from center of hex to center of target hex
        - If LOS passes through interior of blocking hex: BLOCKED
        - If LOS passes along 1 shared edge (counts as single edge): LOS is VALID
        - If LOS passes along 2+ distinct edges: LOS is BLOCKED
        
        Returns (has_los, blocking_hexes)
        """
        # Get all hexes along the line
        line_hexes = self._get_line_hexes(board, q1, r1, q2, r2)
        
        # Also check hexes adjacent to the line path (to catch edge cases)
        candidate_hexes = set()
        for hex_tile in line_hexes:
            candidate_hexes.add(hex_tile)
            for neighbor in board.get_neighbors(hex_tile.q, hex_tile.r):
                candidate_hexes.add(neighbor)
        
        blocking_terrain = ['forest', 'building']
        fully_blocking_hexes = []  # Line passes through interior
        edge_grazing_hexes = []    # Line passes along edge only
        
        # Check each candidate hex (excluding start and end)
        for hex_tile in candidate_hexes:
            if (hex_tile.q == q1 and hex_tile.r == r1) or (hex_tile.q == q2 and hex_tile.r == r2):
                continue
                
            if hex_tile.terrain in blocking_terrain:
                passes_through_interior = self._line_intersects_hex_interior(
                    q1, r1, q2, r2, hex_tile.q, hex_tile.r
                )
                
                passes_along_edge = self._los_passes_along_hex_edge(
                    q1, r1, q2, r2, hex_tile.q, hex_tile.r
                )
                
                if passes_through_interior:
                    fully_blocking_hexes.append(hex_tile)
                elif passes_along_edge:
                    edge_grazing_hexes.append(hex_tile)
        
        all_blocking = fully_blocking_hexes + edge_grazing_hexes
        
        # Count distinct edges: if two hexes are adjacent and both have edge grazes,
        # they share the same edge, so count as 1
        distinct_edge_count = len(edge_grazing_hexes)
        if len(edge_grazing_hexes) == 2:
            hex1, hex2 = edge_grazing_hexes[0], edge_grazing_hexes[1]
            # Check if they're adjacent (distance 1)
            distance = board.hex_distance(hex1.q, hex1.r, hex2.q, hex2.r)
            if distance == 1:
                # They share an edge, count as 1 distinct edge
                distinct_edge_count = 1
        elif len(edge_grazing_hexes) > 2:
            # For 3+ hexes, we need more sophisticated edge counting
            # For now, assume they're distinct edges
            distinct_edge_count = len(edge_grazing_hexes)
        
        # Check if unit has abilities to see through obstacles
        if self.ability_system and (fully_blocking_hexes or distinct_edge_count >= 2):
            can_see_through = not self.ability_system.check_los_blocked(
                unit, 
                line_hexes[-1].terrain if line_hexes else 'open',
                all_blocking
            )
            if can_see_through:
                return True, []
        
        # Apply LOS rules:
        # - Any hex where line passes through interior: BLOCKED
        # - Line passes along 2+ distinct edges: BLOCKED
        # - Line passes along 1 edge (shared or not): LOS is VALID
        has_los = len(fully_blocking_hexes) == 0 and distinct_edge_count <= 1
        
        return has_los, all_blocking if not has_los else []
    
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
    
    # Test 3: Line of sight with proper edge detection
    print(f"\n{'='*70}")
    print(f"TEST 3: Line of Sight with Geometric Edge Detection")
    print(f"{'='*70}")
    
    # Clear LOS
    has_los, blocking = movement_system.has_line_of_sight(board, infantry, 3, 3, 5, 5)
    print(f"LOS from (3,3) to (5,5) [open terrain]: {has_los}")
    if blocking:
        print(f"  Blocked by: {[(h.q, h.r, h.terrain) for h in blocking]}")
    
    # Blocked LOS (through forest interior)
    has_los, blocking = movement_system.has_line_of_sight(board, infantry, 5, 5, 10, 10)
    print(f"\nLOS from (5,5) to (10,10) [through forest]: {has_los}")
    if blocking:
        print(f"  Analysis of blocking hexes:")
        for h in blocking:
            passes_interior = movement_system._line_intersects_hex_interior(5, 5, 10, 10, h.q, h.r)
            passes_edge = movement_system._los_passes_along_hex_edge(5, 5, 10, 10, h.q, h.r)
            status = "INTERIOR" if passes_interior else ("EDGE" if passes_edge else "MISS")
            print(f"    ({h.q}, {h.r}): {status}")
    
    # Multiple test cases for edge detection
    print(f"\n--- Comprehensive Edge Detection Tests ---")
    test_cases = [
        ((5, 6), (9, 8), "Diagonal across forest"),
        ((5, 7), (9, 7), "Horizontal through forest"),
        ((6, 5), (8, 9), "Diagonal through corner"),
        ((7, 5), (7, 9), "Vertical through forest"),
    ]
    
    for (start, end, desc) in test_cases:
        print(f"\n{desc}: {start} → {end}")
        has_los, blocking = movement_system.has_line_of_sight(board, infantry, start[0], start[1], end[0], end[1])
        
        if blocking:
            interior_count = 0
            edge_count = 0
            for h in blocking:
                passes_interior = movement_system._line_intersects_hex_interior(start[0], start[1], end[0], end[1], h.q, h.r)
                passes_edge = movement_system._los_passes_along_hex_edge(start[0], start[1], end[0], end[1], h.q, h.r)
                
                if passes_interior:
                    interior_count += 1
                    print(f"  ({h.q},{h.r}): Interior")
                elif passes_edge:
                    edge_count += 1
                    print(f"  ({h.q},{h.r}): Edge graze")
            
            print(f"  Result: {interior_count} interior, {edge_count} edge | LOS: {has_los}")
            
            # Explain result
            if interior_count > 0:
                expected = False
                print(f"  → Expected BLOCKED (interior hit)")
            elif edge_count > 1:
                expected = False
                print(f"  → Expected BLOCKED (2+ edges)")
            elif edge_count == 1:
                expected = True
                print(f"  → Expected VALID (1 edge graze)")
            else:
                expected = True
                print(f"  → Expected VALID (no blocking)")
            
            if has_los == expected:
                print(f"  ✓ Correct")
            else:
                print(f"  ✗ MISMATCH: got {has_los}, expected {expected}")
        else:
            print(f"  Clear LOS: {has_los}")
    
    print(f"\n✓ LOS Rules (Geometric Implementation):")
    print(f"  • Line through interior of blocking hex: BLOCKED")
    print(f"  • Line EXACTLY along edge of 1 blocking hex: VALID")
    print(f"  • Line along edges of 2+ blocking hexes: BLOCKED")
    
    # Special test: True edge case - Diamond configuration
    print(f"\n--- TRUE EDGE CASE TEST ---")
    print(f"Scenario: Single Diamond (1 shared edge)")
    print(f"       (7,6)")
    print(f"   (6,7) (7,7)  ← Share ONE edge")
    print(f"       (6,8)")
    print(f"Expected: LOS passes along 1 shared edge → VALID")
    
    # Clear previous test terrain
    board.set_terrain(10, 10, 'open')
    board.set_terrain(11, 10, 'open')
    
    # Place the two middle forest hexes
    board.set_terrain(6, 7, 'forest')
    board.set_terrain(7, 7, 'forest')
    
    # Manually check the hexes BEFORE calling has_line_of_sight
    edge_graze_hexes_manual = []
    for hex_tile in [board.get_hex(6, 7), board.get_hex(7, 7)]:
        if hex_tile and hex_tile.terrain in ['forest', 'building']:
            passes_edge = movement_system._los_passes_along_hex_edge(7, 6, 6, 8, hex_tile.q, hex_tile.r)
            passes_interior = movement_system._line_intersects_hex_interior(7, 6, 6, 8, hex_tile.q, hex_tile.r)
            if passes_edge and not passes_interior:
                edge_graze_hexes_manual.append(hex_tile)
    
    # Test edge detection
    has_los, blocking = movement_system.has_line_of_sight(board, infantry, 7, 6, 6, 8)
    
    print(f"\nResult:")
    print(f"  Hexes with edge grazes: {len(edge_graze_hexes_manual)}")
    print(f"  Has LOS: {has_los}")
    
    # Check if they're adjacent
    if len(edge_graze_hexes_manual) == 2:
        dist = board.hex_distance(edge_graze_hexes_manual[0].q, edge_graze_hexes_manual[0].r,
                                    edge_graze_hexes_manual[1].q, edge_graze_hexes_manual[1].r)
        print(f"  Distance between hexes: {dist}")
        if dist == 1:
            print(f"  → Adjacent hexes share ONE edge")
            print(f"  → Counts as 1 distinct edge")
    
    if len(edge_graze_hexes_manual) == 2 and has_los:
        print(f"\n✓✓✓ SUCCESS! Edge case working perfectly! ✓✓✓")
        print(f"  • 2 hexes with edge grazes (adjacent)")
        print(f"  • 1 shared edge = 1 distinct edge")
        print(f"  • LOS correctly ALLOWED")
    elif has_los and len(edge_graze_hexes_manual) == 0:
        print(f"\n✓ LOS is clear (no blocking terrain)")
    else:
        print(f"\n✗ Unexpected result")
    
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
    print(f"TEST 5: Terrain Movement Costs (Base Costs)")
    print(f"{'='*70}")
    print(f"Note: These are BASE costs. Forest/hill entry for vehicles costs 2,")
    print(f"      but moving within forest (forest→forest) costs only 1.")
    
    terrains = ['open', 'forest', 'road', 'hill', 'building']
    print(f"\nMovement costs for {infantry.name} (Infantry):")
    for terrain in terrains:
        cost = movement_system.get_terrain_cost(infantry, terrain)
        print(f"  {terrain}: {cost} movement point(s)")
    
    print(f"\nMovement costs for {tank.name} (Vehicle):")
    for terrain in terrains:
        cost = movement_system.get_terrain_cost(tank, terrain)
        print(f"  {terrain}: {cost} movement point(s)")
    
    # Test 6: Forest entry vs within-forest movement
    print(f"\n{'='*70}")
    print(f"TEST 6: Forest Entry Cost (Vehicles)")
    print(f"{'='*70}")
    
    tank_start_q, tank_start_r = 5, 6
    print(f"Tank starting at (5,6) - open terrain")
    print(f"Forest hexes at (6,6), (6,7), (7,7)")
    print(f"Tank speed: {tank.speed}")
    
    # Get reachable from open terrain
    reachable_from_open = movement_system.get_reachable_hexes(board, 5, 6, tank)
    
    # Check specific forest hexes
    print(f"\nReachability check:")
    if (6, 6) in reachable_from_open:
        print(f"  ✓ Can reach (6,6) - first forest hex (costs 2 to enter)")
    else:
        print(f"  ✗ Cannot reach (6,6)")
    
    if (6, 7) in reachable_from_open:
        print(f"  ✓ Can reach (6,7) - adjacent forest hex")
    else:
        print(f"  ✗ Cannot reach (6,7)")
    
    if (7, 7) in reachable_from_open:
        print(f"  ✓ Can reach (7,7) - second forest hex")
    else:
        print(f"  ✗ Cannot reach (7,7)")
    
    print(f"\nExpected paths to (7,7):")
    print(f"  Path 1: (5,6) → (6,6) → (6,7) → (7,7)")
    print(f"    Costs: open→forest(2) + forest→forest(1) + forest→forest(1) = 4")
    print(f"  Path 2: (5,6) → (6,6) → (7,6) → (7,7)")  
    print(f"    Costs: open→forest(2) + forest→forest(1) + forest→forest(1) = 4")
    
    if tank.speed >= 4 and (7, 7) in reachable_from_open:
        print(f"\n  ✓ CORRECT! Tank with speed {tank.speed} can reach (7,7)")
        print(f"    Forest entry costs 2, but forest→forest costs only 1")
    elif tank.speed < 4:
        print(f"\n  ✓ Correctly NOT reachable with speed {tank.speed} (needs 4)")
    else:
        print(f"\n  ✗ Should be reachable with speed {tank.speed}")
    
    print("\n✅ Movement system with abilities test complete!")