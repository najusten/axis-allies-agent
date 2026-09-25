"""
Game Setup System for Axis & Allies Miniatures

Handles:
- Nation filtering (Axis vs Allies, or historically accurate theaters)
- Year filtering (early war, mid war, late war)
- Army building with point limits
- Scenario configuration
"""

import csv
import re
import os
import random
from typing import List, Dict, Tuple, Optional, Set
from dataclasses import dataclass, field
from copy import deepcopy

from units import Unit
from game_state import GameState, UnitState
from board import Board


# =============================================================================
# NATION DEFINITIONS
# =============================================================================

# Broad alliance groupings
AXIS_NATIONS = {
    'Germany', 'Italy', 'Japan',
    # Minor Axis
    'Hungary', 'Romania', 'Bulgaria', 'Finland', 'Croatia', 'Slovakia'
}

ALLIED_NATIONS = {
    'US', 'UK', 'USSR', 'France', 'China',
    # Commonwealth
    'Australia', 'Canada', 'NZ New Zealand', 'SA South Africa',
    # Other Allies
    'Poland', 'Belgium', 'Greece', 'Yugoslavia'
}

# Map display names to data names
NATION_ALIASES = {
    'USA': 'US',
    'Soviet Union': 'USSR',
    'Russia': 'USSR',
    'New Zealand': 'NZ New Zealand',
    'South Africa': 'SA South Africa',
}


# =============================================================================
# HISTORICAL THEATER DEFINITIONS
# =============================================================================

@dataclass
class TheaterConfig:
    """Configuration for a historical theater of war."""
    name: str
    description: str
    allied_nations: Set[str]
    axis_nations: Set[str]
    year_range: Tuple[int, int]  # (start_year, end_year) inclusive

    def get_all_nations(self) -> Set[str]:
        return self.allied_nations | self.axis_nations


THEATERS = {
    'western_europe': TheaterConfig(
        name='Western Europe',
        description='D-Day and liberation of Western Europe (1944-45)',
        allied_nations={'US', 'UK', 'Canada', 'France', 'Poland', 'Belgium'},
        axis_nations={'Germany'},
        year_range=(1944, 1945)
    ),

    'north_africa': TheaterConfig(
        name='North Africa',
        description='Desert war in Libya, Egypt, Tunisia (1940-43)',
        allied_nations={'UK', 'Australia', 'SA South Africa', 'US',
                       'NZ New Zealand', 'France'},
        axis_nations={'Germany', 'Italy'},
        year_range=(1940, 1943)
    ),

    'mediterranean': TheaterConfig(
        name='Mediterranean',
        description='Sicily, Italy, and Mediterranean islands (1943-45)',
        allied_nations={'US', 'UK', 'Canada', 'France', 'Poland', 'Greece'},
        axis_nations={'Germany', 'Italy'},
        year_range=(1943, 1945)
    ),

    'eastern_front': TheaterConfig(
        name='Eastern Front',
        description='Germany vs Soviet Union (1941-45)',
        allied_nations={'USSR', 'Poland'},
        axis_nations={'Germany', 'Hungary', 'Romania', 'Finland',
                     'Italy', 'Croatia', 'Slovakia'},
        year_range=(1941, 1945)
    ),

    'pacific': TheaterConfig(
        name='Pacific',
        description='Island hopping campaign against Japan (1941-45)',
        allied_nations={'US', 'UK', 'Australia', 'NZ New Zealand', 'China'},
        axis_nations={'Japan'},
        year_range=(1941, 1945)
    ),

    'china_burma_india': TheaterConfig(
        name='China-Burma-India',
        description='CBI theater including Burma Road (1941-45)',
        allied_nations={'UK', 'US', 'China', 'Australia'},
        axis_nations={'Japan'},
        year_range=(1941, 1945)
    ),

    'early_war': TheaterConfig(
        name='Early War (Fall of France)',
        description='German invasion of Western Europe (1939-40)',
        allied_nations={'France', 'UK', 'Belgium', 'Poland'},
        axis_nations={'Germany'},
        year_range=(1939, 1940)
    ),

    'balkans': TheaterConfig(
        name='Balkans',
        description='Invasion of Greece and Yugoslavia (1940-41)',
        allied_nations={'Greece', 'UK', 'Yugoslavia'},
        axis_nations={'Germany', 'Italy', 'Bulgaria'},
        year_range=(1940, 1941)
    ),

    'winter_war': TheaterConfig(
        name='Winter War',
        description='Soviet-Finnish conflict (1939-40)',
        allied_nations={'USSR'},
        axis_nations={'Finland'},  # Finland was defending, not technically Axis yet
        year_range=(1939, 1940)
    ),
}


# =============================================================================
# UNIT LOADING
# =============================================================================

def load_all_units(unit_file: str = None) -> List[Unit]:
    """Load all units from the CSV file."""
    if unit_file is None:
        if os.path.exists('Axis_and_Allies_Unit_Data_for_Analysis_-_Unit_Stats.csv'):
            unit_file = 'Axis_and_Allies_Unit_Data_for_Analysis_-_Unit_Stats.csv'
        else:
            unit_file = 'Axis and Allies Unit Data for Analysis - Unit_Stats.csv'

    units = []
    with open(unit_file, 'r') as file:
        reader = csv.DictReader(file)
        rows = list(reader)
    for i, row in enumerate(rows):
        if True:
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
            # A few rows in the data file end their ability list with the *next*
            # unit's name (one shifted column). That is not an ability: drop it,
            # rather than give the unit a phantom one — or worse a real one named
            # after another unit (a Pillbox acting as a Tank Obstacle).
            abilities = [a.strip() for a in unit.abilities if a.strip() and a.strip() != '-']
            next_name = (rows[i + 1].get('Unit Name') or '').strip() if i + 1 < len(rows) else None
            if abilities and next_name and abilities[-1] == next_name and abilities[-1] != unit.name:
                abilities.pop()
            unit.abilities = abilities
            units.append(unit)

    return units


def normalize_nation(nation: str) -> str:
    """Normalize nation name to match data format."""
    return NATION_ALIASES.get(nation, nation)


# =============================================================================
# UNIT FILTERING
# =============================================================================

class UnitFilter:
    """Filters units based on various criteria."""

    def __init__(self, all_units: List[Unit] = None):
        self.all_units = all_units or load_all_units()

    def filter(self,
               nations: Set[str] = None,
               year_range: Tuple[int, int] = None,
               unit_types: Set[str] = None,
               min_cost: int = None,
               max_cost: int = None,
               require_combat: bool = True) -> List[Unit]:
        """
        Filter units based on criteria.

        Args:
            nations: Set of allowed nations
            year_range: (min_year, max_year) inclusive
            unit_types: Set of allowed unit types ('Soldier', 'Vehicle', etc.)
            min_cost: Minimum point cost
            max_cost: Maximum point cost
            require_combat: If True, only include units that can attack

        Returns:
            List of units matching all criteria
        """
        result = []

        for unit in self.all_units:
            # Nation filter
            if nations and unit.nation not in nations:
                continue

            # Year filter
            if year_range:
                try:
                    unit_year = int(unit.year) if unit.year else 0
                    if unit_year < year_range[0] or unit_year > year_range[1]:
                        continue
                except (ValueError, TypeError):
                    # Skip units with invalid year data
                    continue

            # Unit type filter
            if unit_types and unit.unit_type not in unit_types:
                continue

            # Cost filters
            if min_cost is not None and unit.cost < min_cost:
                continue
            if max_cost is not None and unit.cost > max_cost:
                continue

            # Combat capability filter
            if require_combat:
                has_attack = (
                    unit.per_short > 0 or unit.per_medium > 0 or unit.per_long > 0 or
                    unit.veh_short > 0 or unit.veh_medium > 0 or unit.veh_long > 0
                )
                if not has_attack:
                    continue

            result.append(unit)

        return result

    def get_by_alliance(self, alliance: str, **kwargs) -> List[Unit]:
        """Get units for an alliance ('axis' or 'allies')."""
        if alliance.lower() == 'axis':
            nations = AXIS_NATIONS
        elif alliance.lower() in ('allies', 'allied'):
            nations = ALLIED_NATIONS
        else:
            raise ValueError(f"Unknown alliance: {alliance}")

        return self.filter(nations=nations, **kwargs)

    def get_by_theater(self, theater: str, side: str, **kwargs) -> List[Unit]:
        """
        Get units for a specific theater and side.

        Args:
            theater: Theater name (e.g., 'western_europe', 'pacific')
            side: 'axis' or 'allies'
            **kwargs: Additional filter criteria (overrides theater defaults)
        """
        if theater not in THEATERS:
            raise ValueError(f"Unknown theater: {theater}. Available: {list(THEATERS.keys())}")

        config = THEATERS[theater]

        if side.lower() in ('allies', 'allied'):
            nations = config.allied_nations
        elif side.lower() == 'axis':
            nations = config.axis_nations
        else:
            raise ValueError(f"Side must be 'axis' or 'allies', got: {side}")

        # Use theater's year range unless overridden
        year_range = kwargs.pop('year_range', config.year_range)

        return self.filter(nations=nations, year_range=year_range, **kwargs)


# =============================================================================
# ARMY BUILDING
# =============================================================================

@dataclass
class ArmyConstraints:
    """Constraints for building an army."""
    max_points: int = 100
    min_units: int = 1
    max_units: int = 10
    max_vehicles: int = None  # None = no limit
    max_soldiers: int = None
    require_infantry: bool = False  # Must have at least one soldier


@dataclass
class Army:
    """A built army ready for play."""
    units: List[Unit]
    owner: str  # 'player1' or 'player2'
    total_cost: int = 0
    nations_used: Set[str] = field(default_factory=set)

    def __post_init__(self):
        self.total_cost = sum(u.cost for u in self.units)
        self.nations_used = {u.nation for u in self.units}


class ArmyBuilder:
    """Builds armies from available units within constraints."""

    def __init__(self, available_units: List[Unit]):
        self.available_units = available_units

    def build_random(self,
                     constraints: ArmyConstraints,
                     owner: str = 'player1') -> Army:
        """
        Build a random army within constraints.

        Uses a simple greedy algorithm:
        1. Shuffle available units
        2. Add units until we hit a constraint
        """
        units = []
        remaining_points = constraints.max_points

        # Shuffle for randomness
        candidates = list(self.available_units)
        random.shuffle(candidates)

        # Track type counts
        vehicle_count = 0
        soldier_count = 0

        for unit in candidates:
            # Check constraints
            if len(units) >= constraints.max_units:
                break
            if unit.cost > remaining_points:
                continue
            if constraints.max_vehicles and (unit.unit_type or '').startswith('Vehicle'):
                if vehicle_count >= constraints.max_vehicles:
                    continue
            if constraints.max_soldiers and (unit.unit_type or '').startswith('Soldier'):
                if soldier_count >= constraints.max_soldiers:
                    continue

            # Add unit
            units.append(deepcopy(unit))
            remaining_points -= unit.cost

            if (unit.unit_type or '').startswith('Vehicle'):
                vehicle_count += 1
            elif (unit.unit_type or '').startswith('Soldier'):
                soldier_count += 1

        # Check require_infantry constraint
        if constraints.require_infantry and soldier_count == 0:
            # Try to add an infantry unit
            infantry = [u for u in self.available_units
                       if (u.unit_type or '').startswith('Soldier') and u.cost <= remaining_points]
            if infantry:
                units.append(deepcopy(random.choice(infantry)))

        # Check min_units
        if len(units) < constraints.min_units:
            # Try to fill with cheapest units
            cheap_units = sorted(self.available_units, key=lambda u: u.cost)
            for unit in cheap_units:
                if len(units) >= constraints.min_units:
                    break
                if unit.cost <= remaining_points:
                    units.append(deepcopy(unit))
                    remaining_points -= unit.cost

        # Assign IDs
        for i, unit in enumerate(units):
            unit.id = f"{owner}_unit_{i}"

        return Army(units=units, owner=owner)

    def build_balanced(self,
                       constraints: ArmyConstraints,
                       owner: str = 'player1',
                       infantry_ratio: float = 0.6) -> Army:
        """
        Build a balanced army with a mix of unit types.

        Args:
            constraints: Army building constraints
            owner: Player who owns the army
            infantry_ratio: Target ratio of infantry to total units
        """
        units = []
        remaining_points = constraints.max_points

        # Separate by type
        infantry = [u for u in self.available_units if (u.unit_type or '').startswith('Soldier')]
        vehicles = [u for u in self.available_units if (u.unit_type or '').startswith('Vehicle')]
        other = [u for u in self.available_units
                if u.unit_type not in ('Soldier', 'Vehicle')]

        # Sort by cost (prefer mid-range units)
        infantry.sort(key=lambda u: abs(u.cost - 8))  # Prefer ~8 point infantry
        vehicles.sort(key=lambda u: abs(u.cost - 15))  # Prefer ~15 point vehicles

        target_infantry = int(constraints.max_units * infantry_ratio)
        target_vehicles = constraints.max_units - target_infantry

        # Add infantry
        for unit in infantry[:target_infantry * 2]:  # Extra candidates for cost fitting
            if len([u for u in units if (u.unit_type or '').startswith('Soldier')]) >= target_infantry:
                break
            if unit.cost <= remaining_points and len(units) < constraints.max_units:
                units.append(deepcopy(unit))
                remaining_points -= unit.cost

        # Add vehicles
        for unit in vehicles[:target_vehicles * 2]:
            if len([u for u in units if (u.unit_type or '').startswith('Vehicle')]) >= target_vehicles:
                break
            if unit.cost <= remaining_points and len(units) < constraints.max_units:
                if constraints.max_vehicles is None or \
                   len([u for u in units if (u.unit_type or '').startswith('Vehicle')]) < constraints.max_vehicles:
                    units.append(deepcopy(unit))
                    remaining_points -= unit.cost

        # Fill remaining points with any units
        all_remaining = infantry + vehicles + other
        random.shuffle(all_remaining)
        for unit in all_remaining:
            if len(units) >= constraints.max_units:
                break
            if unit.cost <= remaining_points:
                # Don't add duplicates
                if any(u.name == unit.name for u in units):
                    continue
                units.append(deepcopy(unit))
                remaining_points -= unit.cost

        # Assign IDs
        for i, unit in enumerate(units):
            unit.id = f"{owner}_unit_{i}"

        return Army(units=units, owner=owner)


# =============================================================================
# GAME SETUP
# =============================================================================

@dataclass
class GameSetupConfig:
    """Configuration for setting up a game."""
    # Mode: 'broad' (any Axis vs Allies) or 'theater' (historically accurate)
    mode: str = 'broad'

    # Theater name (only used if mode='theater')
    theater: str = None

    # Custom nation selection (overrides mode/theater)
    nations_p1: Set[str] = None
    nations_p2: Set[str] = None

    # Year filtering
    year_range: Tuple[int, int] = None  # None = no year filter

    # Rulebook "Historical Army Limits": each army is drawn from one group of
    # nations that actually fought together (see HISTORICAL_GROUPS)
    historical: bool = False

    # Exclude Aircraft (the browser UI has no flight/airstrike controls yet)
    exclude_aircraft: bool = False

    # Army constraints
    points_per_side: int = 100
    max_units_per_side: int = 10

    # Board setup
    board_width: int = 18
    board_height: int = 12

    # Objective
    objective_position: Tuple[int, int] = None  # None = center of board


# Rulebook p.27 "Historical Army Limits" (Commonwealth forces grouped with the UK)
HISTORICAL_GROUPS = {
    'axis': [
        {'Germany', 'Italy', 'Romania', 'Hungary', 'Finland', 'Slovakia', 'Croatia', 'Bulgaria'},
        {'Japan'},
    ],
    'allies': [
        {'US', 'UK', 'France', 'Canada', 'Australia', 'NZ New Zealand', 'SA South Africa', 'Belgium', 'Greece'},
        {'USSR'},
        {'China'},
        {'Poland'},
    ],
}


class GameSetup:
    """Sets up complete games with armies, board, and objectives."""

    def __init__(self, config: GameSetupConfig = None):
        self.config = config or GameSetupConfig()
        self.unit_filter = UnitFilter()

    def get_available_units(self, side: str) -> List[Unit]:
        """
        Get available units for a side based on configuration.

        Args:
            side: 'player1' or 'player2'
        """
        config = self.config

        # Determine nations for this side
        if side == 'player1':
            if config.nations_p1:
                nations = {normalize_nation(n) for n in config.nations_p1}
            elif config.mode == 'theater' and config.theater:
                nations = THEATERS[config.theater].allied_nations
            else:  # broad mode, p1 = allies by default
                nations = ALLIED_NATIONS
        else:  # player2
            if config.nations_p2:
                nations = {normalize_nation(n) for n in config.nations_p2}
            elif config.mode == 'theater' and config.theater:
                nations = THEATERS[config.theater].axis_nations
            else:  # broad mode, p2 = axis by default
                nations = AXIS_NATIONS

        # Historical limits: restrict to one group of nations that fought together.
        # Groups are picked at random, weighted by how many units they offer.
        if config.historical and not (config.nations_p1 if side == 'player1' else config.nations_p2):
            groups = HISTORICAL_GROUPS['allies' if side == 'player1' else 'axis']
            candidates = [g & nations for g in groups if g & nations]
            if candidates:
                pool = self.unit_filter.filter(nations=nations, year_range=config.year_range, require_combat=True)
                weights = [max(1, sum(1 for u in pool if u.nation in g)) for g in candidates]
                nations = random.choices(candidates, weights=weights, k=1)[0]

        # Determine year range
        year_range = config.year_range
        if year_range is None and config.mode == 'theater' and config.theater:
            year_range = THEATERS[config.theater].year_range

        units = self.unit_filter.filter(
            nations=nations,
            year_range=year_range,
            require_combat=True
        )
        if config.exclude_aircraft:
            units = [u for u in units if 'Aircraft' not in (u.unit_type or '')]
        return units

    def build_armies(self,
                     build_method: str = 'balanced') -> Tuple[Army, Army]:
        """
        Build armies for both sides.

        Args:
            build_method: 'random' or 'balanced'

        Returns:
            Tuple of (player1_army, player2_army)
        """
        constraints = ArmyConstraints(
            max_points=self.config.points_per_side,
            max_units=self.config.max_units_per_side,
            require_infantry=True
        )

        # Build player 1 army
        p1_units = self.get_available_units('player1')
        p1_builder = ArmyBuilder(p1_units)
        if build_method == 'random':
            p1_army = p1_builder.build_random(constraints, 'player1')
        else:
            p1_army = p1_builder.build_balanced(constraints, 'player1')

        # Build player 2 army
        p2_units = self.get_available_units('player2')
        p2_builder = ArmyBuilder(p2_units)
        if build_method == 'random':
            p2_army = p2_builder.build_random(constraints, 'player2')
        else:
            p2_army = p2_builder.build_balanced(constraints, 'player2')

        return p1_army, p2_army

    def create_board(self, terrain_density: float = 0.15) -> Board:
        """
        Create a game board with terrain.

        Layout is designed in offset (col, row) space so roads run across
        the map and towns are compact; everything is converted to axial
        before touching the board.
        """
        W, H = self.config.board_width, self.config.board_height
        board = Board(W, H)
        A = Board.offset_to_axial

        def in_bounds(col, row):
            return 0 <= col < W and 0 <= row < H

        # Natural terrain clusters (no towns — those are placed deliberately)
        terrain_types = ['forest', 'forest', 'hill', 'building']
        num_terrain_hexes = int(W * H * terrain_density)

        clusters = num_terrain_hexes // 4
        for _ in range(clusters):
            col = random.randint(3, W - 4)
            row = random.randint(2, H - 3)
            terrain = random.choice(terrain_types)
            q, r = A(col, row)
            board.set_terrain(q, r, terrain)
            for nb in board.get_neighbors(q, r):
                if random.random() < 0.5:
                    board.set_terrain(nb.q, nb.r, terrain)

        # Place 1-2 small towns (2-3 hexes each)
        num_towns = random.choice([1, 2])
        town_cells = set()   # (col, row)
        town_hexes = set()   # axial
        for _ in range(num_towns):
            for _attempt in range(10):
                tc = random.randint(4, W - 5)
                tr = random.randint(3, H - 4)
                if any(abs(tc - oc) <= 3 and abs(tr - orow) <= 3 for oc, orow in town_cells):
                    continue
                q, r = A(tc, tr)
                board.set_terrain(q, r, 'town')
                town_cells.add((tc, tr))
                town_hexes.add((q, r))
                nbs = board.get_neighbors(q, r)
                for nb in random.sample(nbs, min(2, len(nbs))):
                    board.set_terrain(nb.q, nb.r, 'town')
                    town_hexes.add((nb.q, nb.r))
                    town_cells.add(Board.axial_to_offset(nb.q, nb.r))
                break

        # A road across the map (left edge to right edge), bending toward a town
        road_row = H // 2 + random.randint(-2, 2)
        if town_cells:
            nearest = min(town_cells, key=lambda c: abs(c[0] - W // 2))
            target_row = nearest[1]
        else:
            target_row = road_row
        for col in range(W):
            q, r = A(col, road_row)
            if board.get_hex(q, r) and board.get_hex(q, r).terrain != 'town':
                board.set_terrain(q, r, 'road')
            if road_row < target_row and col < W * 2 // 3:
                if random.random() < 0.4:
                    road_row += 1
            elif road_row > target_row and col < W * 2 // 3:
                if random.random() < 0.4:
                    road_row -= 1
            road_row = max(1, min(H - 2, road_row))

        # A stream: a line of hex sides between two columns near the CENTRE of
        # the map so both sides must cross it equally, bridged wherever the road
        # crosses it and with a couple of fords (gaps) so it never walls off a side.
        if random.random() < 0.7:
            col = random.choice([W // 2 - 1, W // 2])
            edges = []
            for row in range(H):
                q, r = A(col, row)
                for nb in board.get_neighbors(q, r):
                    if Board.axial_to_offset(nb.q, nb.r)[0] == col + 1:
                        edges.append((q, r, nb.q, nb.r))
            fords = set(random.sample(range(len(edges)), min(len(edges), random.choice([2, 3]))))
            for i, (q, r, nq, nr) in enumerate(edges):
                if i in fords:
                    continue
                board.add_edge_obstacle(q, r, nq, nr, 'stream')

        # Hedges: field boundaries around towns
        for (q, r) in list(town_hexes):
            nbs = [nb for nb in board.get_neighbors(q, r) if nb.terrain not in ('town', 'road')]
            for nb in random.sample(nbs, min(len(nbs), random.choice([1, 2]))):
                if not board.get_edge_obstacle(q, r, nb.q, nb.r):
                    board.add_edge_obstacle(q, r, nb.q, nb.r, 'hedge')

        # Balance cover terrain across both sides of the board
        self._balance_cover(board)

        return board

    def _balance_cover(self, board: Board):
        """Ensure roughly equal cover hexes on both sides of the board."""
        cover_types = {'forest', 'building', 'hill', 'town', 'ruins'}
        mid_q = self.config.board_width // 2

        # Count cover on each side
        left_cover = []
        right_cover = []
        for (q, r), hex_obj in board.hexes.items():
            if hex_obj.terrain in cover_types:
                if q < mid_q:
                    left_cover.append((q, r))
                else:
                    right_cover.append((q, r))

        # If imbalance > 1, add cover to the weaker side until it's within 1
        diff = len(left_cover) - len(right_cover)
        if abs(diff) <= 1:
            return

        # Determine which side needs more cover
        if diff > 0:
            # Right side needs more
            add_side_range = range(mid_q, self.config.board_width)
        else:
            # Left side needs more
            add_side_range = range(0, mid_q)

        needed = abs(diff) - 1   # close the gap to at most 1 hex
        added = 0
        candidates = []
        for col in add_side_range:
            for q, r in board.column(col):
                if board.get_hex(q, r).terrain == 'open':
                    candidates.append((q, r))

        random.shuffle(candidates)
        cover_options = ['forest', 'forest', 'hill', 'building']
        for q, r in candidates:
            if added >= needed:
                break
            board.set_terrain(q, r, random.choice(cover_options))
            added += 1

    def _has_ability(self, unit: Unit, ability_name: str) -> bool:
        """Check if a unit has a specific ability (case-insensitive)."""
        abilities = getattr(unit, 'abilities', []) or []
        return any(ability_name.lower() in a.lower() for a in abilities)

    def _get_edge_hexes(self, board: Board) -> List[Tuple[int, int]]:
        """Get all hexes on the edge of the battle map."""
        return [(q, r) for (q, r), hex_obj in board.hexes.items()
                if board.is_edge(q, r) and hex_obj.terrain != 'impassable']

    def _get_gliderborne_hexes(self, board: Board, opponent_start_q: int,
                                is_player1: bool) -> List[Tuple[int, int]]:
        """Get valid hexes for Gliderborne deployment (anywhere not in opponent's starting area)."""
        valid_hexes = []
        for (q, r), hex_obj in board.hexes.items():
            # Skip the opponent's starting area (their two edge columns)
            if is_player1 and q >= opponent_start_q:
                continue
            if not is_player1 and q <= opponent_start_q + 1:
                continue
            if hex_obj.terrain == 'impassable':
                continue
            valid_hexes.append((q, r))
        return valid_hexes

    def place_units(self, board: Board,
                    p1_army: Army, p2_army: Army) -> Tuple[List[UnitState], List[UnitState]]:
        """
        Place army units on the board in starting positions.

        Player 1 starts on the west (left) side.
        Player 2 starts on the east (right) side.

        Special deployment rules:
        - Gliderborne: Deploy in any unoccupied hex not in opponent's starting area
        - Partisan: Deploy on any unoccupied hex on the edge of the battle map
        """
        p1_unit_states = []
        p2_unit_states = []
        occupied_hexes = set()

        # Player 1 starting zone (left side, within 2 hexes of edge: columns 0-1)
        p1_positions = [(q, r) for col in range(0, 2) for q, r in board.column(col)
                        if board.get_hex(q, r).terrain != 'impassable']
        random.shuffle(p1_positions)

        # Player 2 starting zone (right side, within 2 hexes of edge)
        p2_positions = [(q, r) for col in range(board.width - 2, board.width)
                        for q, r in board.column(col)
                        if board.get_hex(q, r).terrain != 'impassable']
        random.shuffle(p2_positions)

        # Get special deployment hexes
        edge_hexes = self._get_edge_hexes(board)
        p1_gliderborne_hexes = self._get_gliderborne_hexes(board, self.config.board_width - 2, is_player1=True)
        p2_gliderborne_hexes = self._get_gliderborne_hexes(board, 1, is_player1=False)

        # Separate units by deployment type
        def categorize_units(army):
            normal = []
            gliderborne = []
            partisan = []
            for unit in army.units:
                if self._has_ability(unit, 'gliderborne'):
                    gliderborne.append(unit)
                elif self._has_ability(unit, 'partisan'):
                    partisan.append(unit)
                else:
                    normal.append(unit)
            return normal, gliderborne, partisan

        p1_normal, p1_gliderborne, p1_partisan = categorize_units(p1_army)
        p2_normal, p2_gliderborne, p2_partisan = categorize_units(p2_army)

        def create_unit_state(unit, pos, owner):
            defense = getattr(unit, 'defense_front', unit.defense_front)
            unit_state = UnitState(unit, pos, owner, defense)
            # Set facing based on owner
            if (unit.unit_type or '').startswith('Vehicle'):
                unit_state.facing = 0 if owner == 'player1' else 3  # East or West
            occupied_hexes.add(pos)
            return unit_state

        # Place player 1 normal units
        pos_idx = 0
        for unit in p1_normal:
            if pos_idx < len(p1_positions):
                p1_unit_states.append(create_unit_state(unit, p1_positions[pos_idx], 'player1'))
                pos_idx += 1

        # Place player 1 gliderborne units
        available_gliderborne = [h for h in p1_gliderborne_hexes if h not in occupied_hexes]
        random.shuffle(available_gliderborne)
        for i, unit in enumerate(p1_gliderborne):
            if i < len(available_gliderborne):
                p1_unit_states.append(create_unit_state(unit, available_gliderborne[i], 'player1'))

        # Place player 1 partisan units (on edge hexes)
        available_edge = [h for h in edge_hexes if h not in occupied_hexes]
        random.shuffle(available_edge)
        for i, unit in enumerate(p1_partisan):
            if i < len(available_edge):
                p1_unit_states.append(create_unit_state(unit, available_edge[i], 'player1'))

        # Place player 2 normal units
        pos_idx = 0
        for unit in p2_normal:
            if pos_idx < len(p2_positions):
                p2_unit_states.append(create_unit_state(unit, p2_positions[pos_idx], 'player2'))
                pos_idx += 1

        # Place player 2 gliderborne units
        available_gliderborne = [h for h in p2_gliderborne_hexes if h not in occupied_hexes]
        random.shuffle(available_gliderborne)
        for i, unit in enumerate(p2_gliderborne):
            if i < len(available_gliderborne):
                p2_unit_states.append(create_unit_state(unit, available_gliderborne[i], 'player2'))

        # Place player 2 partisan units (on edge hexes)
        available_edge = [h for h in edge_hexes if h not in occupied_hexes]
        random.shuffle(available_edge)
        for i, unit in enumerate(p2_partisan):
            if i < len(available_edge):
                p2_unit_states.append(create_unit_state(unit, available_edge[i], 'player2'))

        return p1_unit_states, p2_unit_states

    def army_from_names(self, names: List[str], owner: str) -> Army:
        """Hand-picked army: unit names (duplicates allowed) from the catalog."""
        import copy
        catalog = {u.name: u for u in load_all_units()}
        units = []
        for n in names:
            if n not in catalog:
                raise ValueError(f"Unknown unit: {n}")
            units.append(copy.deepcopy(catalog[n]))
        return Army(units=units, owner=owner)

    def create_game(self,
                    build_method: str = 'balanced',
                    terrain_density: float = 0.15,
                    p1_names: List[str] = None,
                    p2_names: List[str] = None) -> GameState:
        """
        Create a complete game state ready to play.

        Args:
            build_method: 'random' or 'balanced' army building
            terrain_density: How much terrain to add to the board
            p1_names / p2_names: hand-picked unit lists (None = build automatically)

        Returns:
            Fully configured GameState
        """
        # Build armies
        p1_army, p2_army = self.build_armies(build_method)
        if p1_names:
            p1_army = self.army_from_names(p1_names, 'player1')
        if p2_names:
            p2_army = self.army_from_names(p2_names, 'player2')

        # Create board
        board = self.create_board(terrain_density)

        # Place units
        p1_units, p2_units = self.place_units(board, p1_army, p2_army)

        # Determine objective position
        if self.config.objective_position:
            obj_pos = self.config.objective_position
        else:
            obj_pos = (self.config.board_width // 2, self.config.board_height // 2)

        # Create game state
        game_state = GameState(board, p1_units, p2_units, objective_position=obj_pos)

        return game_state

    def describe(self) -> str:
        """Return a human-readable description of this setup."""
        config = self.config
        lines = []

        if config.mode == 'theater' and config.theater:
            theater = THEATERS[config.theater]
            lines.append(f"Theater: {theater.name}")
            lines.append(f"  {theater.description}")
            lines.append(f"  Allied: {', '.join(sorted(theater.allied_nations))}")
            lines.append(f"  Axis: {', '.join(sorted(theater.axis_nations))}")
            lines.append(f"  Years: {theater.year_range[0]}-{theater.year_range[1]}")
        elif config.nations_p1 or config.nations_p2:
            lines.append("Custom Nations:")
            if config.nations_p1:
                lines.append(f"  Player 1: {', '.join(sorted(config.nations_p1))}")
            if config.nations_p2:
                lines.append(f"  Player 2: {', '.join(sorted(config.nations_p2))}")
        else:
            lines.append("Mode: Broad (any Axis vs any Allies)")

        if config.year_range:
            lines.append(f"Year Range: {config.year_range[0]}-{config.year_range[1]}")

        lines.append(f"Points: {config.points_per_side} per side")
        lines.append(f"Max Units: {config.max_units_per_side} per side")

        return '\n'.join(lines)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def quick_setup_broad(points: int = 100,
                      year_range: Tuple[int, int] = None) -> GameSetup:
    """Quick setup for broad Axis vs Allies game."""
    config = GameSetupConfig(
        mode='broad',
        year_range=year_range,
        points_per_side=points
    )
    return GameSetup(config)


def quick_setup_theater(theater: str,
                        points: int = 100,
                        year_override: Tuple[int, int] = None) -> GameSetup:
    """Quick setup for a historical theater."""
    if theater not in THEATERS:
        available = ', '.join(THEATERS.keys())
        raise ValueError(f"Unknown theater '{theater}'. Available: {available}")

    config = GameSetupConfig(
        mode='theater',
        theater=theater,
        year_range=year_override,
        points_per_side=points
    )
    return GameSetup(config)


def quick_setup_custom(nations_p1: List[str],
                       nations_p2: List[str],
                       points: int = 100,
                       year_range: Tuple[int, int] = None) -> GameSetup:
    """Quick setup with custom nation selection."""
    config = GameSetupConfig(
        nations_p1=set(nations_p1),
        nations_p2=set(nations_p2),
        year_range=year_range,
        points_per_side=points
    )
    return GameSetup(config)


def list_theaters() -> None:
    """Print available theaters and their details."""
    print("Available Theaters:")
    print("=" * 60)
    for key, theater in THEATERS.items():
        print(f"\n{key}:")
        print(f"  {theater.name} ({theater.year_range[0]}-{theater.year_range[1]})")
        print(f"  {theater.description}")
        print(f"  Allied: {', '.join(sorted(theater.allied_nations))}")
        print(f"  Axis: {', '.join(sorted(theater.axis_nations))}")


# =============================================================================
# TEST
# =============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("GAME SETUP SYSTEM TEST")
    print("=" * 70)

    # List theaters
    list_theaters()

    # Test broad setup
    print("\n" + "=" * 70)
    print("TEST: Broad Axis vs Allies (1942-1943)")
    print("=" * 70)

    setup = quick_setup_broad(points=50, year_range=(1942, 1943))
    print(setup.describe())

    p1_units = setup.get_available_units('player1')
    p2_units = setup.get_available_units('player2')
    print(f"\nAvailable units: P1={len(p1_units)}, P2={len(p2_units)}")

    game = setup.create_game(build_method='balanced')
    print(f"\nGame created:")
    print(f"  P1 units: {len(game.get_units_by_owner('player1'))}")
    print(f"  P2 units: {len(game.get_units_by_owner('player2'))}")
    print(f"  Objective: {game.objective_position}")

    # Test theater setup
    print("\n" + "=" * 70)
    print("TEST: Eastern Front Theater")
    print("=" * 70)

    setup = quick_setup_theater('eastern_front', points=75)
    print(setup.describe())

    p1_units = setup.get_available_units('player1')
    p2_units = setup.get_available_units('player2')
    print(f"\nAvailable units: P1 (Soviet)={len(p1_units)}, P2 (Axis)={len(p2_units)}")

    # Show sample units
    print("\nSample Soviet units:")
    for u in p1_units[:5]:
        print(f"  {u.name} ({u.nation}, {u.year}) - {u.cost}pts")

    print("\nSample Axis units:")
    for u in p2_units[:5]:
        print(f"  {u.name} ({u.nation}, {u.year}) - {u.cost}pts")

    # Test custom setup
    print("\n" + "=" * 70)
    print("TEST: Custom - US+UK vs Germany (1944)")
    print("=" * 70)

    setup = quick_setup_custom(
        nations_p1=['US', 'UK'],
        nations_p2=['Germany'],
        points=60,
        year_range=(1944, 1945)
    )
    print(setup.describe())

    game = setup.create_game()
    p1 = game.get_units_by_owner('player1')
    p2 = game.get_units_by_owner('player2')

    print(f"\nPlayer 1 Army ({sum(u.unit.cost for u in p1)} pts):")
    for u in p1:
        print(f"  {u.unit.name} ({u.unit.nation}) at {u.position}")

    print(f"\nPlayer 2 Army ({sum(u.unit.cost for u in p2)} pts):")
    for u in p2:
        print(f"  {u.unit.name} ({u.unit.nation}) at {u.position}")

    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)
