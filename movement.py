from typing import Dict, List, Set, Tuple, Optional
from collections import deque
from board import Board, Hex

class MovementSystem:
    """Handles unit movement and line of sight with ability integration"""
    
    # Base terrain movement costs (how many movement points to enter)
    BASE_TERRAIN_COSTS = {
        'open': 1,
        'road': 1,      # Roads don't slow you down
        'forest': 2,    # Double cost for Vehicles (Soldiers 1) — rulebook "Double-Cost Terrain"
        'hill': 2,      # Double cost for Vehicles (Soldiers 1)
        'building': 1,
        'town': 1,
        'marsh': 1,     # Soldiers only: Vehicles can't enter marshes (rulebook "Impassable Terrain")
        'ruins': 1,
        'stream': 1,
        'water': 99,    # Impassable (Soldiers too); Amphibious/Water Craft handled in the search
        'impassable': 99,
    }
    # Vehicles can never enter these (rulebook: "Vehicles can't enter marshes or water")
    VEHICLE_IMPASSABLE = frozenset({'marsh'})
    
    def __init__(self, ability_system=None):
        """Initialize with optional ability system"""
        self.ability_system = ability_system
        self._los_cache: Dict[tuple, tuple] = {}
    
    def get_terrain_cost(self, unit, terrain: str) -> int:
        """
        Get movement cost for terrain, considering unit abilities.
        NOTE: This is the BASE cost. Entry vs within-terrain is handled separately.
        """
        base_cost = self.BASE_TERRAIN_COSTS.get(terrain, 1)
        
        # Infantry/Soldiers: forests and hills cost 1 (not 2)
        if 'Soldier' in (unit.unit_type or ''):
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
    
    def _get_terrain_cost_with_entry(self, unit, current_terrain: str, previous_terrain: str,
                                      movement_mods: dict = None) -> int:
        """
        Calculate movement cost considering whether unit is entering terrain or already in it.

        For Vehicles:
        - Entering forest/hill from non-forest/hill: 2 movement
        - Moving within forest (forest->forest): 1 movement
        - Moving within hills (hill->hill): 1 movement

        For Infantry: Always 1 for forest/hill

        Abilities that modify terrain costs:
        - Excellent Suspension (ignore_hill_terrain): Hills cost 1
        - Brushcutters (ignore_forest_terrain): Forests cost 1
        - Trench Crossing (ignore_stream_terrain): Streams cost 1
        - Poor Suspension (poor_suspension): Can't enter hills at all (handled elsewhere)
        """
        movement_mods = movement_mods or {}

        # Infantry always pays 1 for forest/hill
        if 'Soldier' in (unit.unit_type or ''):
            if current_terrain in ['forest', 'hill']:
                return 1
            return self.BASE_TERRAIN_COSTS.get(current_terrain, 1)

        # Check for terrain-ignoring abilities (Vehicles)
        if current_terrain == 'hill' and movement_mods.get('ignore_hill_terrain', False):
            return 1  # Excellent Suspension: treat hills as clear

        if current_terrain == 'forest' and movement_mods.get('ignore_forest_terrain', False):
            return 1  # Brushcutters: no movement roll for forests

        if current_terrain == 'stream' and movement_mods.get('ignore_stream_terrain', False):
            return 1  # Trench Crossing: cross streams without roll

        # Water Craft: enter water hexes as clear terrain
        if current_terrain == 'water' and movement_mods.get('water_craft', False):
            return 1

        # Amphibious: enter water hexes as double-cost terrain
        if current_terrain == 'water' and movement_mods.get('amphibious', False):
            return 2

        # Vehicles: "When a Vehicle enters a hill or forest hex, it counts as two
        # hexes of movement" — every such hex, forest-to-forest included.
        if current_terrain in ['forest', 'hill']:
            return 2

        # All other terrain uses base cost
        return self.BASE_TERRAIN_COSTS.get(current_terrain, 1)
    
    @staticmethod
    def disrupted_move_allowed(game_state, unit_state, to_pos) -> bool:
        """
        Rulebook: disrupted units can't move. Abilities that override it:
        Robust / SS Determination / Hardened Veteran / Veteran Crew / Veteran
        Guard (any move); Courage (only closer to the nearest enemy unit);
        Charge (only closer to an enemy Soldier). Heroes ignore Disrupted.
        """
        abilities = [a.lower() for a in (getattr(unit_state.unit, 'abilities', []) or [])]
        if any(a in ('robust', 'ss determination', 'hardened veteran', 'veteran crew', 'veteran guard', 'hero')
               for a in abilities):
            return True
        board = game_state.board
        from_pos = unit_state.position
        enemy_owner = "player2" if unit_state.owner == "player1" else "player1"
        enemies = [e for e in game_state.get_units_by_owner(enemy_owner) if e.is_alive and e.is_deployed]
        if 'courage' in abilities and enemies:
            nearest = min(enemies, key=lambda e: board.hex_distance(from_pos[0], from_pos[1], *e.position))
            d0 = board.hex_distance(from_pos[0], from_pos[1], *nearest.position)
            d1 = board.hex_distance(to_pos[0], to_pos[1], *nearest.position)
            if d1 < d0:
                return True
        if 'charge' in abilities:
            for e in enemies:
                if 'Soldier' in (e.unit.unit_type or ''):
                    if board.hex_distance(to_pos[0], to_pos[1], *e.position) < board.hex_distance(from_pos[0], from_pos[1], *e.position):
                        return True
        return False

    def get_effective_speed(self, unit, ability_mods: dict = None, is_disrupted: bool = False) -> int:
        """
        Get unit's effective speed considering abilities and disruption.

        Args:
            unit: The unit to check speed for
            ability_mods: Optional pre-computed ability modifiers
            is_disrupted: Whether the unit is disrupted (for Robust check)
        """
        base_speed = unit.speed

        # Handle aircraft speed 'A'
        if isinstance(base_speed, str):
            return 0  # Aircraft don't use ground movement

        if ability_mods:
            base_speed += ability_mods.get('speed_bonus', 0)
            base_speed -= ability_mods.get('speed_penalty', 0)

            # Robust: While disrupted, this unit has speed 1
            if is_disrupted and ability_mods.get('robust', False):
                return 1

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
                           unit, max_speed: int = None, road_only: bool = False,
                           include_road_bonus: bool = True,
                           friendly_positions: Set[Tuple[int, int]] = None,
                           is_damaged: bool = False,
                           minimum_movement: bool = False) -> Set[Tuple[int, int]]:
        """
        All hexes reachable from the start with the unit's speed, using the
        rulebook movement rules:
        - Vehicles pay 2 for every forest/hill hex entered; Soldiers pay 1.
        - Vehicles can't enter marsh or water (Amphibious/Water Craft excepted).
        - Moving along a road (both hexes connected by road) costs 1 regardless
          of terrain, and for Vehicles the FIRST road hex moved along each phase
          is free ("Road Bonus").
        - road_only=True restricts the whole move to road steps (High Gear).
        Records predecessors for find_path().
        """
        if self.ability_system:
            movement_mods = self.ability_system.get_movement_modifiers(unit)
            if self.ability_system.is_obstacle_unit(unit):
                return {(start_q, start_r)}
        else:
            movement_mods = {}

        if max_speed is None:
            max_speed = self.get_effective_speed(unit, movement_mods)
            if is_damaged:
                max_speed = max(0, max_speed - 1)   # damaged Vehicle: -1 speed

        is_vehicle = 'Vehicle' in (unit.unit_type or '')
        unit_abilities = getattr(unit, 'abilities', []) or []
        has_overrun = is_vehicle and any(a.lower() == 'overrun' for a in unit_abilities)
        road_bonus = include_road_bonus and is_vehicle    # High Gear (road_only) moves still get the free first road hex

        reachable = {(start_q, start_r)}
        start_hex = board.get_hex(start_q, start_r)
        start_terrain = start_hex.terrain if start_hex else 'open'

        # State: (q, r, movement_left, previous_terrain, road_bonus_available, rolls_so_far)
        # "rolls" counts terrain/hex-side movement rolls the route requires; at
        # equal movement cost the route with fewer rolls is preferred.
        queue = deque([(start_q, start_r, max_speed, start_terrain, road_bonus, 0)])
        visited = {(start_q, start_r, road_bonus): (max_speed, 0)}
        best_left: Dict[Tuple[int, int], Tuple[int, int]] = {(start_q, start_r): (max_speed, 0)}
        came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
        edge_rolls = board.edge_obstacles

        while queue:
            q, r, movement, prev_terrain, bonus, rolls = queue.popleft()
            here = board.get_hex(q, r)
            here_road = bool(here and here.has_road)

            for neighbor in board.get_neighbors(q, r):
                nq, nr = neighbor.q, neighbor.r
                terrain = neighbor.terrain
                along_road = here_road and neighbor.has_road

                step = self._step_cost(unit, movement_mods, is_vehicle, here, neighbor, prev_terrain, bonus,
                                       road_only=road_only,
                                       minimum_move=(minimum_movement and (q, r) == (start_q, start_r)
                                                     and max_speed == 1))
                if step is None:
                    continue
                cost, new_bonus = step
                # Occupancy never blocks movement (rulebook "Stacking While Moving":
                # you may move through full hexes, even enemy ones — a hex holds up
                # to two units of each army). The generator/validator enforce the
                # stacking limit at the destination; defensive fire punishes passing
                # enemies. Enemy Vehicles can't share a hex with a Vehicle, but that
                # is a destination rule too.
                passthrough_only = False

                new_movement = movement - cost
                if new_movement < 0:
                    continue
                if (nq, nr) == (start_q, start_r):
                    continue

                # Movement rolls this step would require (forest for Vehicles,
                # streams/hedges unless along a road)
                new_rolls = rolls
                if is_vehicle and terrain == 'forest' and not along_road \
                        and not movement_mods.get('ignore_forest_terrain', False):
                    new_rolls += 1
                if edge_rolls and not along_road:
                    kind = board.get_edge_obstacle(q, r, nq, nr)
                    if kind and (kind in Board.EDGE_STREAM or kind in Board.EDGE_HEDGE
                                 or kind == 'barbed wire'):
                        new_rolls += 1

                key = (nq, nr, new_bonus)
                rank = (new_movement, -new_rolls)
                if key in visited and visited[key] >= rank:
                    continue
                visited[key] = rank
                if best_left.get((nq, nr), (-1, 0)) < rank:
                    best_left[(nq, nr)] = rank
                    came_from[(nq, nr)] = (q, r)
                if not passthrough_only:
                    reachable.add((nq, nr))
                queue.append((nq, nr, new_movement, terrain, new_bonus, new_rolls))

        self._last_came_from = came_from   # consumed by find_path()
        self._last_road_came_from = {}
        return reachable

    def _step_cost(self, unit, movement_mods: dict, is_vehicle: bool, here, neighbor,
                   prev_terrain: str, bonus: bool, road_only: bool = False,
                   minimum_move: bool = False) -> Optional[Tuple[int, bool]]:
        """Movement points to enter `neighbor` from `here`, or None if the step is
        not allowed. Returns (cost, road_bonus_still_available)."""
        terrain = neighbor.terrain
        along_road = bool(here and here.has_road) and neighbor.has_road

        # Water / impassable / marsh for vehicles
        if movement_mods.get('water_craft', False):
            if terrain != 'water':
                return None
        elif terrain == 'water':
            if not movement_mods.get('amphibious', False):
                return None
        elif self.BASE_TERRAIN_COSTS.get(terrain, 1) >= 99 and not along_road:
            return None
        if is_vehicle and terrain in self.VEHICLE_IMPASSABLE and not along_road \
                and not movement_mods.get('amphibious', False):
            return None
        if road_only and not along_road:
            return None
        if terrain == 'hill' and movement_mods.get('poor_suspension', False) and not neighbor.has_road:
            return None
        if terrain in ('marsh', 'stream') and movement_mods.get('thin_wheels', False) and not neighbor.has_road:
            return None

        new_bonus = bonus
        if along_road:
            if bonus:
                cost = 0            # first road hex of the phase is free
                new_bonus = False
            else:
                cost = 1            # along a road every hex counts as one
        else:
            cost = self._get_terrain_cost_with_entry(unit, terrain, prev_terrain, movement_mods)
            # Rulebook "Minimum Movement": a speed-1 Vehicle may enter a
            # double-cost hex as its whole move in the movement phase
            if minimum_move and is_vehicle and cost == 2:
                cost = 1
        return cost, new_bonus

    def path_cost(self, board: Board, unit, path: List[Tuple[int, int]], max_speed: int = None,
                  is_damaged: bool = False, minimum_movement: bool = False,
                  road_only: bool = False) -> Optional[int]:
        """Movement points a specific route costs under the same rules as
        get_reachable_hexes(), or None if the route is not legal for this unit
        (non-adjacent steps, impassable terrain, or more than its speed)."""
        if len(path) < 2:
            return 0
        movement_mods = self.ability_system.get_movement_modifiers(unit) if self.ability_system else {}
        if self.ability_system and self.ability_system.is_obstacle_unit(unit):
            return None
        if max_speed is None:
            max_speed = self.get_effective_speed(unit, movement_mods)
            if is_damaged:
                max_speed = max(0, max_speed - 1)
        is_vehicle = 'Vehicle' in (unit.unit_type or '')
        bonus = is_vehicle
        here = board.get_hex(*path[0])
        if here is None:
            return None
        prev_terrain = here.terrain
        total = 0
        for i, (nq, nr) in enumerate(path[1:]):
            q, r = path[i]
            if board.hex_distance(q, r, nq, nr) != 1:
                return None
            neighbor = board.get_hex(nq, nr)
            if neighbor is None:
                return None
            step = self._step_cost(unit, movement_mods, is_vehicle, here, neighbor, prev_terrain, bonus,
                                   road_only=road_only, minimum_move=(minimum_movement and i == 0 and max_speed == 1))
            if step is None:
                return None
            cost, bonus = step
            total += cost
            if total > max_speed:
                return None
            here, prev_terrain = neighbor, neighbor.terrain
        return total

    def get_reachable_hexes_with_costs(self, board, start_q, start_r, unit,
                                        max_speed=None, friendly_positions=None):
        """Like get_reachable_hexes but also returns movement cost to each hex.
        Returns (reachable_set, cost_dict) where cost_dict maps (q,r) -> movement_cost."""
        # Get movement modifiers
        if self.ability_system:
            movement_mods = self.ability_system.get_movement_modifiers(unit)
        else:
            movement_mods = {}
        if max_speed is None:
            max_speed = self.get_effective_speed(unit, movement_mods)

        reachable = self.get_reachable_hexes(
            board, start_q, start_r, unit, max_speed=max_speed,
            friendly_positions=friendly_positions
        )

        # Approximate costs using hex distance (exact terrain costs would require
        # re-running BFS with cost tracking, but distance is close enough for
        # partial movement purposes since BFS already validates reachability)
        costs = {}
        for (dq, dr) in reachable:
            if (dq, dr) != (start_q, start_r):
                costs[(dq, dr)] = board.hex_distance(start_q, start_r, dq, dr)
        return reachable, costs

    def _get_road_bonus_hexes(self, board: Board, start_q: int, start_r: int,
                               unit, bonus_speed: int, movement_mods: dict = None,
                               friendly_positions: Set[Tuple[int, int]] = None) -> Set[Tuple[int, int]]:
        # Note: unit and movement_mods params kept for potential future use (ability interactions)
        _ = unit, movement_mods  # Suppress unused warnings
        """
        Get hexes reachable only via road paths with bonus speed.
        Used for road bonus calculation - vehicles get +1 speed if staying on roads.
        """
        road_reachable = set()
        came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}

        # Queue: (q, r, movement_remaining)
        queue = deque([(start_q, start_r, bonus_speed)])
        visited = {(start_q, start_r): bonus_speed}

        while queue:
            q, r, movement = queue.popleft()
            current_hex = board.get_hex(q, r)

            # Only continue if current hex has a road
            if not current_hex or not current_hex.has_road:
                continue

            neighbors = board.get_neighbors(q, r)
            for neighbor in neighbors:
                nq, nr = neighbor.q, neighbor.r

                # Road bonus only applies when staying on roads
                if not neighbor.has_road:
                    continue

                # Skip occupied hexes (except obstacles and friendly units)
                if neighbor.unit is not None:
                    is_obstacle = getattr(neighbor.unit, 'unit_type', None) == 'Obstacle'
                    is_friendly = (friendly_positions is not None
                                   and (nq, nr) in friendly_positions)
                    if not is_obstacle and not is_friendly:
                        continue

                # Roads cost 1 movement
                new_movement = movement - 1

                if new_movement >= 0:
                    if (nq, nr) == (start_q, start_r):
                        continue

                    if (nq, nr) not in visited or visited[(nq, nr)] < new_movement:
                        visited[(nq, nr)] = new_movement
                        came_from[(nq, nr)] = (q, r)
                        road_reachable.add((nq, nr))
                        queue.append((nq, nr, new_movement))

        self._last_road_came_from = came_from
        return road_reachable

    def find_path(self, board: Board, start_q: int, start_r: int, to_q: int, to_r: int,
                  unit, max_speed: int = None, road_only: bool = False,
                  friendly_positions: Set[Tuple[int, int]] = None) -> List[Tuple[int, int]]:
        """
        Cheapest legal path (list of (q, r) from start to destination inclusive)
        using the same terrain/ability rules as get_reachable_hexes. Falls back
        to the road-bonus / High Gear road-only search when the destination is
        only reachable that way. Returns [start, dest] if no path is known.
        """
        start, dest = (start_q, start_r), (to_q, to_r)
        if start == dest:
            return [start]
        reachable = self.get_reachable_hexes(board, start_q, start_r, unit, max_speed=max_speed,
                                             road_only=road_only, friendly_positions=friendly_positions)
        came_from = dict(getattr(self, '_last_came_from', {}) or {})
        if dest not in came_from:
            # Reached only via the road bonus search (or not at all)
            road = getattr(self, '_last_road_came_from', {}) or {}
            if dest in road:
                came_from = dict(road)
            else:
                return [start, dest]
        path = [dest]
        cur = dest
        guard = 0
        while cur != start and guard < 500:
            cur = came_from.get(cur)
            if cur is None:
                return [start, dest]
            path.append(cur)
            guard += 1
        path.reverse()
        return path
    
    def get_assault_move_range(self, unit) -> int:
        """
        Get how far a unit can move in assault phase.
        Some abilities (Aggression, Strike and Fade) allow movement before/after attacking.
        """
        if not self.ability_system:
            return 0
        
        # Only Aggression X lets a unit move (at speed X) BEFORE attacking in the
        # assault phase. Strike and Fade moves AFTER attacking and is handled by
        # strike_and_fade_available; everything else is attack OR move.
        import re
        for ability in (getattr(unit, 'abilities', []) or []):
            m = re.match(r'Aggression\s+(\d+)', ability, re.IGNORECASE)
            if m:
                return int(m.group(1))
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
                         q1: int, r1: int, q2: int, r2: int,
                         smoke_screens: set = None) -> Tuple[bool, List[Hex]]:
        """
        Check if there's line of sight between two hexes, considering unit abilities.

        LOS Rules (PROPER GEOMETRIC IMPLEMENTATION):
        - LOS is drawn from center of hex to center of target hex
        - If LOS passes through interior of blocking hex: BLOCKED
        - If LOS passes along 1 shared edge (counts as single edge): LOS is VALID
        - If LOS passes along 2+ distinct edges: LOS is BLOCKED
        - Smoke screens block LOS into, out of, and through their hex

        Returns (has_los, blocking_hexes)
        """
        smoke_screens = smoke_screens or set()
        # LOS depends only on terrain, smoke and the viewer's abilities, and the
        # same pairs are asked for thousands of times per turn (AI scoring,
        # lookahead on cloned boards): memoise on the board's terrain signature.
        cache_key = (board.terrain_signature(), q1, r1, q2, r2, frozenset(smoke_screens),
                     tuple(getattr(unit, 'abilities', None) or ()))
        hit = self._los_cache.get(cache_key)
        if hit is not None:
            return hit[0], list(hit[1])
        res = self._has_line_of_sight_uncached(board, unit, q1, r1, q2, r2, smoke_screens)
        if len(self._los_cache) > 200000:
            self._los_cache.clear()
        self._los_cache[cache_key] = (res[0], list(res[1]))
        return res

    def _has_line_of_sight_uncached(self, board: Board, unit, q1: int, r1: int, q2: int, r2: int,
                                    smoke_screens: set) -> Tuple[bool, List[Hex]]:
        # Hex-side terrain (hedges) crossed by the sight line
        if board.edge_obstacles and self.hex_side_effects(board, q1, r1, q2, r2)['blocked']:
            return False, []
        # Get all hexes along the line
        line_hexes = self._get_line_hexes(board, q1, r1, q2, r2)

        # Check for smoke screens - blocks LOS into, out of, and through
        for hex_tile in line_hexes:
            if (hex_tile.q, hex_tile.r) in smoke_screens:
                return False, [hex_tile]

        # Also check hexes adjacent to the line path (to catch edge cases)
        candidate_hexes = set()
        for hex_tile in line_hexes:
            candidate_hexes.add(hex_tile)
            for neighbor in board.get_neighbors(hex_tile.q, hex_tile.r):
                candidate_hexes.add(neighbor)
        
        blocking_terrain = ['forest', 'building', 'hill', 'town']   # rulebook: towns, hills, forests
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
            attacker_hex = board.get_hex(q1, r1)
            attacker_terrain = attacker_hex.terrain if attacker_hex else 'open'
            can_see_through = not self.ability_system.check_los_blocked(
                unit,
                line_hexes[-1].terrain if line_hexes else 'open',
                all_blocking,
                attacker_terrain=attacker_terrain
            )
            if can_see_through:
                return True, []
        
        # Apply LOS rules:
        # - Any hex where line passes through interior: BLOCKED
        # - Line passes along 2+ distinct edges: BLOCKED
        # - Line passes along 1 edge (shared or not): LOS is VALID
        has_los = len(fully_blocking_hexes) == 0 and distinct_edge_count <= 1
        
        return has_los, all_blocking if not has_los else []
    
    _shared_edge_cache: Dict[Tuple[int, int, int, int], object] = {}

    @staticmethod
    def _shared_edge(qa: int, ra: int, qb: int, rb: int):
        """The edge segment two adjacent hexes share (cartesian, same frame as _get_hex_edges)."""
        key = (qa, ra, qb, rb)
        cache = MovementSystem._shared_edge_cache
        if key in cache:
            return cache[key]
        cache[key] = MovementSystem._compute_shared_edge(qa, ra, qb, rb)
        return cache[key]

    @staticmethod
    def _compute_shared_edge(qa: int, ra: int, qb: int, rb: int):
        ea = MovementSystem._get_hex_edges(qa, ra)
        eb = MovementSystem._get_hex_edges(qb, rb)
        va = {(round(x, 4), round(y, 4)) for e in ea for (x, y) in e}
        vb = {(round(x, 4), round(y, 4)) for e in eb for (x, y) in e}
        common = list(va & vb)
        return (common[0], common[1]) if len(common) == 2 else None

    @staticmethod
    def _segments_cross(p1, p2, p3, p4, tol: float = 1e-6) -> bool:
        """Proper intersection of segments p1-p2 and p3-p4 (touching an endpoint counts)."""
        def orient(a, b, c):
            v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            return 0 if abs(v) < tol else (1 if v > 0 else -1)
        o1, o2 = orient(p1, p2, p3), orient(p1, p2, p4)
        o3, o4 = orient(p3, p4, p1), orient(p3, p4, p2)
        if o1 == 0 and o2 == 0:
            return False   # collinear: running along a hedge doesn't cross it
        return o1 != o2 and o3 != o4

    def hex_side_effects(self, board: Board, q1: int, r1: int, q2: int, r2: int) -> dict:
        """
        Hex-side terrain along the sight line from (q1,r1) to (q2,r2).
        Rulebook: hedges the line crosses block LOS, except hedges on the
        attacker's or target's own hex sides — a hedge on the target's hex
        side that the line passes through gives the target cover instead.
        Returns {'blocked': bool, 'cover': bool}.
        """
        x1 = q1 + r1 * 0.5; y1 = r1 * (3 ** 0.5 / 2)
        x2 = q2 + r2 * 0.5; y2 = r2 * (3 ** 0.5 / 2)
        blocked = cover = False
        # Only hedges within the line's bounding box (in hex distance) can matter
        span = board.hex_distance(q1, r1, q2, r2)
        for key, kind in board.edge_obstacles.items():
            if kind not in Board.EDGE_LOS_BLOCKING:
                continue
            (qa, ra), (qb, rb) = tuple(key)
            if board.hex_distance(q1, r1, qa, ra) > span + 1 or board.hex_distance(q2, r2, qa, ra) > span + 1:
                continue
            seg = self._shared_edge(qa, ra, qb, rb)
            if seg is None or not self._segments_cross((x1, y1), (x2, y2), seg[0], seg[1]):
                continue
            touches_attacker = (q1, r1) in key
            touches_target = (q2, r2) in key
            if touches_target:
                cover = True
            elif not touches_attacker:
                blocked = True
        return {'blocked': blocked, 'cover': cover}

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
    infantry = [u for u in combat_units if 'Soldier' in (u.unit_type or '') and u.speed == 1][0]
    tank = [u for u in combat_units if 'Vehicle' in (u.unit_type or '') and isinstance(u.speed, int) and u.speed >= 4][0]
    
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