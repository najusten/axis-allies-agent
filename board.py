import math
from typing import List, Tuple, Optional

class Hex:
    """Represents a single hex on the game board"""
    
    def __init__(self, q, r, terrain='open'):
        """
        q, r: Axial coordinates for hex grid
        terrain: Type of terrain in this hex
        """
        self.q = q  # Column coordinate
        self.r = r  # Row coordinate
        self.terrain = terrain
        self.unit = None  # Unit occupying this hex (if any)
    
    def __str__(self):
        unit_str = f" [{self.unit.name}]" if self.unit else ""
        return f"Hex({self.q},{self.r}) {self.terrain}{unit_str}"
    
    def __eq__(self, other):
        return self.q == other.q and self.r == other.r
    
    def __hash__(self):
        return hash((self.q, self.r))

    @property
    def has_road(self) -> bool:
        """Check if this hex has a road (terrain type is 'road')."""
        return self.terrain == 'road'


class Board:
    """Represents the game board as a hex grid"""
    
    # Valid terrain types
    TERRAIN_TYPES = ['open', 'forest', 'building', 'water', 'road', 'hill']
    
    def __init__(self, width=15, height=15):
        """Create a board with given dimensions"""
        self.width = width
        self.height = height
        self.hexes = {}  # Dictionary mapping (q,r) -> Hex

        # Edge obstacles (Barbed Wire, etc.) - maps frozenset((q1,r1), (q2,r2)) -> obstacle_type
        self.edge_obstacles = {}

        # Initialize all hexes as open terrain
        for q in range(width):
            for r in range(height):
                self.hexes[(q, r)] = Hex(q, r, 'open')
    
    def get_hex(self, q, r) -> Optional[Hex]:
        """Get hex at coordinates (q, r)"""
        return self.hexes.get((q, r))
    
    def set_terrain(self, q, r, terrain):
        """Set terrain type for a hex"""
        if terrain not in self.TERRAIN_TYPES:
            raise ValueError(f"Invalid terrain type: {terrain}")
        if (q, r) in self.hexes:
            self.hexes[(q, r)].terrain = terrain
    
    def place_unit(self, unit, q, r):
        """Place a unit at hex (q, r)"""
        hex_tile = self.get_hex(q, r)
        if hex_tile:
            hex_tile.unit = unit
            return True
        return False
    
    def remove_unit(self, q, r):
        """Remove unit from hex (q, r)"""
        hex_tile = self.get_hex(q, r)
        if hex_tile:
            hex_tile.unit = None

    def _edge_key(self, q1: int, r1: int, q2: int, r2: int):
        """Create a consistent key for an edge between two hexes."""
        return frozenset(((q1, r1), (q2, r2)))

    def add_edge_obstacle(self, q1: int, r1: int, q2: int, r2: int, obstacle_type: str):
        """Add an edge obstacle (like Barbed Wire) between two adjacent hexes."""
        key = self._edge_key(q1, r1, q2, r2)
        self.edge_obstacles[key] = obstacle_type

    def remove_edge_obstacle(self, q1: int, r1: int, q2: int, r2: int):
        """Remove an edge obstacle between two hexes."""
        key = self._edge_key(q1, r1, q2, r2)
        if key in self.edge_obstacles:
            del self.edge_obstacles[key]

    def get_edge_obstacle(self, q1: int, r1: int, q2: int, r2: int) -> Optional[str]:
        """Get the obstacle type on the edge between two hexes, or None."""
        key = self._edge_key(q1, r1, q2, r2)
        return self.edge_obstacles.get(key)

    def has_edge_obstacle(self, q1: int, r1: int, q2: int, r2: int) -> bool:
        """Check if there's an edge obstacle between two hexes."""
        return self.get_edge_obstacle(q1, r1, q2, r2) is not None

    def get_neighbors(self, q, r) -> List[Hex]:
        """Get all six neighboring hexes (axial coordinates)"""
        # The six directions in axial coordinates
        directions = [
            (1, 0), (1, -1), (0, -1),
            (-1, 0), (-1, 1), (0, 1)
        ]
        
        neighbors = []
        for dq, dr in directions:
            neighbor = self.get_hex(q + dq, r + dr)
            if neighbor:
                neighbors.append(neighbor)
        return neighbors
    
    def hex_distance(self, q1, r1, q2, r2) -> int:
        """Calculate distance between two hexes in hex units"""
        # Axial coordinate distance formula
        return (abs(q1 - q2) + abs(q1 + r1 - q2 - r2) + abs(r1 - r2)) // 2
    
    def get_units_in_range(self, q, r, max_range) -> List[Tuple[Hex, int]]:
        """Get all hexes with units within max_range of (q, r)"""
        units_in_range = []
        
        for hex_tile in self.hexes.values():
            if hex_tile.unit is not None:
                distance = self.hex_distance(q, r, hex_tile.q, hex_tile.r)
                if distance <= max_range and distance > 0:  # Exclude self
                    units_in_range.append((hex_tile, distance))
        
        return units_in_range
    
    def display_area(self, center_q, center_r, radius=5):
        """Display a text representation of the board around a center point"""
        print(f"\n=== Board area around ({center_q}, {center_r}) ===")
        
        for r in range(center_r - radius, center_r + radius + 1):
            # Offset for hex display
            offset = " " * abs(r - center_r)
            row_str = offset
            
            for q in range(center_q - radius, center_q + radius + 1):
                hex_tile = self.get_hex(q, r)
                if hex_tile:
                    # Show terrain initial or unit initial
                    if hex_tile.unit:
                        symbol = 'U'
                    else:
                        symbol = hex_tile.terrain[0].upper()
                    row_str += f"[{symbol}] "
                else:
                    row_str += "    "
            
            print(row_str)
        print()


# Test the board
if __name__ == "__main__":
    from units import load_units
    
    # Create a board
    board = Board(width=15, height=15)
    print(f"Created board: {board.width}x{board.height} hexes")
    
    # Add some terrain features
    # Create a forest area
    for q in range(3, 6):
        for r in range(3, 6):
            board.set_terrain(q, r, 'forest')
    
    # Create a village in the center
    board.set_terrain(7, 7, 'building')
    board.set_terrain(7, 8, 'building')
    board.set_terrain(8, 7, 'building')
    
    # Add a road
    for i in range(15):
        board.set_terrain(i, 7, 'road')
    
    # Load some units and place them
    units = load_units()
    # Find some soldiers
    soldiers = [u for u in units if u.unit_type == 'Soldier' and u.speed > 0][:3]
    
    if soldiers:
        board.place_unit(soldiers[0], 5, 5)
        board.place_unit(soldiers[1], 9, 9)
        print(f"\nPlaced {soldiers[0].name} at (5,5)")
        print(f"Placed {soldiers[1].name} at (9,9)")
        
        # Calculate distance
        distance = board.hex_distance(5, 5, 9, 9)
        print(f"Distance between units: {distance} hexes")
        
        # Get neighbors
        neighbors = board.get_neighbors(5, 5)
        print(f"\nNeighboring hexes of (5,5): {len(neighbors)} hexes")
        for n in neighbors:
            print(f"  {n}")
    
    # Display the board
    board.display_area(7, 7, radius=7)