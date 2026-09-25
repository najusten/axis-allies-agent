"""
Battle-map generator: theater-flavoured, balanced between the two sides, with a
road network that is continuous and goes somewhere.

Balance comes from symmetry. Every feature is placed on one half of the map and
copied onto the other half through the board's centre (a 180-degree rotation for
even widths, a left-right reflection for odd widths — both keep hex adjacency,
so a road or a stream stays continuous when copied). Players therefore face the
same amount of cover, the same roads and the same obstacles, while shapes are
not so regular that the map looks mirrored at a glance (clusters grow randomly
on each side, only their sizes are paired).

Roads are laid with A* between places a road would actually connect — map edges,
villages, the objective — preferring open ground and existing roads, and they are
stored as links between adjacent hexes (Board.add_road). They never start or stop
in the middle of a field.

Theaters set the look (bocage and villages in Western Europe, steppe, forest and
marsh in the East, open desert with ridges in North Africa, jungle in the
Pacific, hills in Italy); each feature can also be switched off.
"""

import heapq
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from board import Board

Cell = Tuple[int, int]      # (col, row) offset coordinates


# ----------------------------------------------------------------------
# Theaters and options
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class TheaterStyle:
    label: str
    description: str
    forest: float       # share of the map covered by forest clusters
    hills: float
    marsh: float
    buildings: float    # scattered farmhouses / ruins (building hexes)
    villages: int       # village pairs, one on each half (plus the central one)
    central_village: bool
    stream: float       # chance of a stream
    hedges: float       # 0..1 bocage intensity
    cross_road: float   # chance of a second road crossing north-south
    cluster: Tuple[int, int] = (3, 6)   # min/max hexes per terrain cluster


THEATER_STYLES: Dict[str, TheaterStyle] = {
    'western_europe': TheaterStyle(
        'Western Europe (Normandy)', 'Bocage farmland: hedgerows, villages, orchards and a stream.',
        forest=0.08, hills=0.03, marsh=0.02, buildings=0.02, villages=1, central_village=True,
        stream=0.7, hedges=0.8, cross_road=0.7),
    'eastern_front': TheaterStyle(
        'Eastern Front', 'Open steppe with large forests, marshy lowland and scattered villages.',
        forest=0.12, hills=0.02, marsh=0.05, buildings=0.01, villages=1, central_village=True,
        stream=0.6, hedges=0.0, cross_road=0.4, cluster=(4, 9)),
    'north_africa': TheaterStyle(
        'North Africa', 'Open desert: rocky ridges, an oasis village and a coast road.',
        forest=0.0, hills=0.07, marsh=0.0, buildings=0.01, villages=0, central_village=True,
        stream=0.0, hedges=0.0, cross_road=0.3, cluster=(3, 7)),
    'italy': TheaterStyle(
        'Italy', 'Hill country with stone villages, olive groves and river valleys.',
        forest=0.05, hills=0.10, marsh=0.0, buildings=0.03, villages=1, central_village=True,
        stream=0.6, hedges=0.2, cross_road=0.6),
    'pacific': TheaterStyle(
        'Pacific (jungle)', 'Dense jungle cut by a track, swamps and a native village.',
        forest=0.26, hills=0.03, marsh=0.06, buildings=0.0, villages=0, central_village=True,
        stream=0.5, hedges=0.0, cross_road=0.2, cluster=(5, 10)),
    'urban': TheaterStyle(
        'Town fight', 'A town straddling the objective, with gardens, rubble and a river.',
        forest=0.03, hills=0.0, marsh=0.0, buildings=0.08, villages=2, central_village=True,
        stream=0.5, hedges=0.3, cross_road=1.0),
}

DENSITY = {'sparse': 0.6, 'normal': 1.0, 'dense': 1.5}


@dataclass
class MapOptions:
    theater: str = 'western_europe'
    density: str = 'normal'
    # Feature switches: None = the theater decides, False = never, True = always
    streams: Optional[bool] = None
    marshes: Optional[bool] = None
    hedges: Optional[bool] = None
    forests: Optional[bool] = None
    hills: Optional[bool] = None
    villages: Optional[bool] = None
    seed: Optional[int] = None


# ----------------------------------------------------------------------
# Generator
# ----------------------------------------------------------------------

class MapGenerator:
    def __init__(self, width: int, height: int, options: Optional[MapOptions] = None,
                 rng: Optional[random.Random] = None):
        self.W, self.H = width, height
        self.opt = options or MapOptions()
        self.style = THEATER_STYLES.get(self.opt.theater, THEATER_STYLES['western_europe'])
        self.rng = rng or random.Random(self.opt.seed)
        self.dens = DENSITY.get(self.opt.density, 1.0)
        self.board: Board = None
        self.objective: Cell = None
        self.report: Dict = {}

    # -- symmetry ---------------------------------------------------------

    def mirror(self, c: Cell) -> Cell:
        col, row = c
        if self.W % 2 == 0:
            return (self.W - 1 - col, self.H - 1 - row)     # 180-degree rotation
        return (self.W - 1 - col, row)                      # left-right reflection

    def ax(self, c: Cell) -> Tuple[int, int]:
        return Board.offset_to_axial(*c)

    def cell(self, q: int, r: int) -> Cell:
        return Board.axial_to_offset(q, r)

    def in_bounds(self, c: Cell) -> bool:
        return 0 <= c[0] < self.W and 0 <= c[1] < self.H

    def neighbors(self, c: Cell) -> List[Cell]:
        return [self.cell(n.q, n.r) for n in self.board.get_neighbors(*self.ax(c))]

    def left_half(self, c: Cell) -> bool:
        """The half a feature is generated on (its mirror image is the other)."""
        return c[0] < self.W // 2 if self.W % 2 == 0 else c[0] < self.W // 2

    def terrain(self, c: Cell) -> str:
        return self.board.get_hex(*self.ax(c)).terrain

    def set_pair(self, c: Cell, terrain: str):
        for x in (c, self.mirror(c)):
            if self.in_bounds(x):
                self.board.set_terrain(*self.ax(x), terrain)

    def enabled(self, flag: Optional[bool], theater_value: float) -> bool:
        if flag is False:
            return False
        if flag is True:
            return True
        return theater_value > 0

    # -- main ---------------------------------------------------------------

    def generate(self) -> Tuple[Board, Tuple[int, int]]:
        """Build a map; returns (board, objective axial). Retries until the map
        passes the playability checks."""
        best = None
        for attempt in range(12):
            self._build()
            problems = self._check()
            if not problems:
                return self.board, self.ax(self.objective)
            best = best or (self.board, self.objective, problems)
        self.board, self.objective, problems = best
        self.report['warnings'] = problems
        return self.board, self.ax(self.objective)

    def _build(self):
        W, H, st, o = self.W, self.H, self.style, self.opt
        self.board = Board(W, H)
        self.reserved: Set[Cell] = set()          # village cells: keep clusters off them
        self.villages: List[List[Cell]] = []
        self.objective = (W // 2, H // 2)
        n = W * H

        # Deployment columns stay mostly clear so both sides can set up
        self.front_rows = set()

        # 1. Villages: one at the objective, then pairs (one per half)
        if self.enabled(o.villages, 1.0 if st.central_village else 0):
            self._village(self.objective, size=self.rng.randint(2, 4), central=True)
        if self.enabled(o.villages, st.villages):
            for _ in range(st.villages if o.villages is not True else max(1, st.villages)):
                for _try in range(20):
                    c = (self.rng.randint(3, W // 2 - 2), self.rng.randint(1, H - 2))
                    if all(self._dist(c, v[0]) > 4 for v in self.villages):
                        self._village(c, size=self.rng.randint(2, 3))
                        break

        # 2. Terrain clusters, generated on one half and paired on the other
        def clusters(kind: str, share: float):
            target = int(n * share * self.dens / 2)          # per half
            placed = 0
            guard = 0
            while placed < target and guard < 200:
                guard += 1
                size = min(self.rng.randint(*st.cluster), target - placed)
                seed = (self.rng.randint(2, W // 2 - 1), self.rng.randint(0, H - 1))
                cells = self._grow(seed, size, kind)
                if not cells:
                    continue
                for c in cells:
                    self.board.set_terrain(*self.ax(c), kind)
                # the other half gets a cluster of the same size, grown on its own
                mirror_cells = self._grow(self.mirror(seed), len(cells), kind)
                for c in mirror_cells:
                    self.board.set_terrain(*self.ax(c), kind)
                # keep the halves exactly even even if one side ran out of room
                short = len(cells) - len(mirror_cells)
                for c in cells[:max(0, short)]:
                    self.board.set_terrain(*self.ax(c), 'open')
                placed += min(len(cells), len(mirror_cells))

        if self.enabled(o.forests, st.forest):
            clusters('forest', st.forest or 0.06)
        if self.enabled(o.hills, st.hills):
            clusters('hill', st.hills or 0.03)
        if self.enabled(o.marshes, st.marsh):
            clusters('marsh', st.marsh or 0.03)
        if st.buildings > 0:
            clusters('building', st.buildings)

        # 3. Stream (hex sides), meandering across the centre, before the roads so
        # roads can bridge it
        self.stream_edges: Set[frozenset] = set()
        if self.enabled(o.streams, st.stream) and (o.streams or self.rng.random() < st.stream):
            self._stream()

        # 4. Roads
        self._roads()

        # 5. Hedgerows (bocage)
        if self.enabled(o.hedges, st.hedges):
            self._hedges(st.hedges or 0.5)

        # 6. Fords: a stream must never wall off a flank
        if self.stream_edges:
            self._fords()

    # -- features -----------------------------------------------------------

    def _dist(self, a: Cell, b: Cell) -> int:
        return self.board.hex_distance(*self.ax(a), *self.ax(b))

    def _deploy_depth(self, c: Cell) -> int:
        return min(c[0], self.W - 1 - c[0])

    def _grow(self, seed: Cell, size: int, kind: str) -> List[Cell]:
        """Random blob of `size` open cells around seed (avoids villages and the
        first two columns of each deployment zone)."""
        if not self.in_bounds(seed):
            return []
        cells, frontier = [], [seed]
        seen = {seed}
        while frontier and len(cells) < size:
            c = frontier.pop(self.rng.randrange(len(frontier)))
            if (not self.in_bounds(c) or c in self.reserved or self.terrain(c) != 'open'
                    or self._deploy_depth(c) < 2 or c == self.objective
                    or self.left_half(c) != self.left_half(seed)):   # stay on the seed's half
                continue
            cells.append(c)
            for nb in self.neighbors(c):
                if nb not in seen:
                    seen.add(nb)
                    frontier.append(nb)
        return cells

    def _village(self, centre: Cell, size: int, central: bool = False):
        cells = [centre]
        for nb in self.rng.sample(self.neighbors(centre), len(self.neighbors(centre))):
            if len(cells) >= size:
                break
            cells.append(nb)
        groups = [cells] if central else [cells, [self.mirror(c) for c in cells]]
        if central and self.mirror(centre) != centre:
            # a central village straddles the centre: give it both halves
            groups = [sorted(set(cells) | {self.mirror(c) for c in cells})]
        for g in groups:
            g = [c for c in g if self.in_bounds(c)]
            for c in g:
                self.board.set_terrain(*self.ax(c), 'town')
                self.reserved.add(c)
            self.villages.append(g)

    def _stream(self):
        """A stream is the boundary between a 'west' region and the rest, so it
        is one unbroken line of hex sides from the top edge to the bottom edge.
        Its course is symmetric under the map's mirror, so each side has the
        same crossing distance."""
        W, H = self.W, self.H
        base = W // 2 - 1
        course = {}
        col = base + self.rng.choice([-1, 0])
        half_rows = range(0, (H + 1) // 2)
        for row in half_rows:
            if self.rng.random() < 0.35:
                col += self.rng.choice([-1, 1])
            col = max(base - 2, min(base + 1, col))
            course[row] = col
        for row in half_rows:
            mc, mr = self.mirror((course[row], row))
            # the boundary lies between col and col+1; mirrored it lies between
            # W-2-col and W-1-col on the mirrored row
            course.setdefault(mr, W - 2 - course[row] if W % 2 == 0 else course[row])
        west = {(c, r) for r in range(H) for c in range(W) if c <= course.get(r, base)}
        for c in west:
            for nb in self.neighbors(c):
                if nb not in west and self.in_bounds(nb):
                    self.stream_edges.add(frozenset((c, nb)))
        for e in self.stream_edges:
            a, b = tuple(e)
            if self.board.get_edge_obstacle(*self.ax(a), *self.ax(b)) is None:
                self.board.add_edge_obstacle(*self.ax(a), *self.ax(b), 'stream')
        # a little low ground along the banks
        if self.enabled(self.opt.marshes, self.style.marsh):
            banks = sorted({x for e in self.stream_edges for x in e
                            if self.terrain(x) == 'open' and self.left_half(x)
                            and self._deploy_depth(x) >= 3 and x not in self.reserved})
            for c in self.rng.sample(banks, min(len(banks), int(2 * self.dens))):
                self.set_pair(c, 'marsh')

    # -- roads ----------------------------------------------------------------

    def _step_cost(self, a: Cell, b: Cell, jitter: Dict) -> float:
        t = self.terrain(b)
        cost = {'open': 1.0, 'town': 0.6, 'building': 3.0, 'forest': 2.5, 'hill': 2.0,
                'marsh': 9.0, 'water': 1e9, 'impassable': 1e9}.get(t, 1.5)
        e = frozenset((self.ax(a), self.ax(b)))
        if e in self.board.road_edges:
            cost = 0.3                       # join an existing road rather than run parallel
        if frozenset((a, b)) in self.stream_edges:
            cost += 3.0                      # bridges are expensive: cross where it's needed
        return cost + jitter.setdefault(b, self.rng.random() * 0.6)

    def _astar(self, start: Cell, goals: Set[Cell], jitter: Dict) -> Optional[List[Cell]]:
        frontier = [(0.0, start)]
        came, cost = {start: None}, {start: 0.0}
        goal_list = list(goals)
        while frontier:
            _, c = heapq.heappop(frontier)
            if c in goals:
                path = [c]
                while came[path[-1]] is not None:
                    path.append(came[path[-1]])
                return path[::-1]
            for nb in self.neighbors(c):
                if not self.in_bounds(nb):
                    continue
                g = cost[c] + self._step_cost(c, nb, jitter)
                if g >= 1e8:
                    continue
                if nb not in cost or g < cost[nb]:
                    cost[nb] = g
                    h = min(self._dist(nb, x) for x in goal_list)
                    came[nb] = c
                    heapq.heappush(frontier, (g + h, nb))
        return None

    def _lay(self, path: List[Cell], mirrored: bool = True):
        if not path or len(path) < 2:
            return
        self.board.add_road([self.ax(c) for c in path])
        if mirrored:
            mp = [self.mirror(c) for c in path]
            if all(self.in_bounds(c) for c in mp):
                self.board.add_road([self.ax(c) for c in mp])

    def _roads(self):
        W, H = self.W, self.H
        jitter: Dict = {}
        centre = self.objective
        centre_set = {centre}
        # every road network reaches the objective area
        if self.villages:
            centre_set = set(self.villages[0])
        # main road: west edge -> centre, mirrored to the east edge
        start = (0, max(1, min(H - 2, H // 2 + self.rng.randint(-2, 2))))
        main = self._astar(start, centre_set, jitter)
        self._lay(main)
        # a cross road: north edge -> centre, mirrored south
        if self.rng.random() < self.style.cross_road:
            col = max(3, min(W - 4, W // 2 + self.rng.randint(-3, 1)))
            north = self._astar((col, 0), self._road_cells() or centre_set, jitter)
            self._lay(north)
        # side villages connect to the network
        for v in self.villages[1:]:
            if not self.left_half(v[0]):
                continue       # its mirror twin is connected by mirroring
            net = self._road_cells() - set(v)
            if net:
                self._lay(self._astar(v[0], net, jitter))
        # one network: join any pieces (mirrored halves meeting at the centre,
        # a spur that ended on the other half's road...)
        self._connect_components(jitter)

    def _road_cells(self) -> Set[Cell]:
        return {self.cell(*h) for e in self.board.road_edges for h in e}

    def _components(self) -> List[Set[Cell]]:
        graph: Dict[Cell, Set[Cell]] = {}
        for e in self.board.road_edges:
            a, b = (self.cell(*h) for h in e)
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)
        comps, seen = [], set()
        for start in graph:
            if start in seen:
                continue
            comp, stack = {start}, [start]
            seen.add(start)
            while stack:
                for nb in graph[stack.pop()]:
                    if nb not in seen:
                        seen.add(nb)
                        comp.add(nb)
                        stack.append(nb)
            comps.append(comp)
        return comps

    def _connect_components(self, jitter: Dict):
        """Link separate road pieces with the cheapest connecting road (and its
        mirror image, which keeps the map symmetric) until there is one network."""
        for _ in range(8):
            comps = self._components()
            if len(comps) <= 1:
                return
            comps.sort(key=len, reverse=True)
            main, other = comps[0], comps[1]
            a, b = min(((a, b) for a in other for b in main), key=lambda p: self._dist(*p))
            path = self._astar(a, main, jitter)
            if not path:
                return
            mirror_path = [self.mirror(c) for c in path]
            self.board.add_road([self.ax(c) for c in path])
            if all(self.in_bounds(c) for c in mirror_path):
                self.board.add_road([self.ax(c) for c in mirror_path])

    # -- hedges ----------------------------------------------------------------

    def _hedges(self, intensity: float):
        """Bocage: short runs of hedgerow along field boundaries, around villages
        and between open fields — never across a road."""
        runs = int(self.W * self.H * 0.012 * intensity * self.dens)
        for _ in range(runs):
            c = (self.rng.randint(2, self.W // 2 - 1), self.rng.randint(0, self.H - 1))
            length = self.rng.randint(2, 4)
            for _step in range(length):
                nbs = [nb for nb in self.neighbors(c) if self.in_bounds(nb)]
                if not nbs:
                    break
                nb = self.rng.choice(nbs)
                self._hedge_pair(c, nb)
                c = nb
        for v in self.villages:
            for c in v:
                for nb in self.neighbors(c):
                    if self.in_bounds(nb) and nb not in v and self.rng.random() < 0.35 * intensity \
                            and self.left_half(c):
                        self._hedge_pair(c, nb)

    def _hedge_pair(self, a: Cell, b: Cell):
        for x, y in ((a, b), (self.mirror(a), self.mirror(b))):
            if not (self.in_bounds(x) and self.in_bounds(y)):
                continue
            qa, ra = self.ax(x)
            qb, rb = self.ax(y)
            if self.board.road_between(qa, ra, qb, rb) or self.board.get_edge_obstacle(qa, ra, qb, rb):
                continue
            if self._deploy_depth(x) < 2 and self._deploy_depth(y) < 2:
                continue
            self.board.add_edge_obstacle(qa, ra, qb, rb, 'hedge')

    def _fords(self):
        """Two or three crossings without a roll on each half (a stream is an
        obstacle, not a wall)."""
        crossings = sorted((tuple(sorted(e)) for e in self.stream_edges
                            if self.left_half(min(e))), key=lambda e: e[0][1])
        if not crossings:
            return
        for a, b in self.rng.sample(crossings, min(len(crossings), 2)):
            for x, y in ((a, b), (self.mirror(a), self.mirror(b))):
                if self.in_bounds(x) and self.in_bounds(y):
                    self.board.remove_edge_obstacle(*self.ax(x), *self.ax(y))

    # -- checks ---------------------------------------------------------------

    def _check(self) -> List[str]:
        """Playability: each side can reach the objective by ground, roads reach
        the map edge, and the halves are balanced."""
        problems = []
        b = self.board
        for side_col in (0, self.W - 1):
            start = next((c for c in ((side_col, r) for r in range(self.H))
                          if self.terrain(c) not in ('water', 'impassable', 'marsh')), None)
            if start is None or not self._reachable(start, self.objective):
                problems.append(f"no vehicle route from column {side_col} to the objective")
        cover = [0, 0]
        for (q, r), h in b.hexes.items():
            c = self.cell(q, r)
            if self.W % 2 == 1 and c[0] == self.W // 2:
                continue                        # the centre column belongs to both sides
            if Board.gives_cover(h.terrain, 'Soldier'):
                cover[0 if c[0] < self.W / 2 else 1] += 1
        if abs(cover[0] - cover[1]) > max(2, (cover[0] + cover[1]) // 10):
            problems.append(f"cover imbalance {cover}")
        roads = self._road_cells()
        if roads and not any(c[0] in (0, self.W - 1) or c[1] in (0, self.H - 1) for c in roads):
            problems.append("no road reaches the map edge")
        self.report = {'cover_per_side': cover, 'road_hexes': len(roads),
                       'stream_sides': sum(1 for k in b.edge_obstacles.values() if k == 'stream'),
                       'hedges': sum(1 for k in b.edge_obstacles.values() if k == 'hedge')}
        return problems

    def _reachable(self, start: Cell, goal: Cell) -> bool:
        seen, stack = {start}, [start]
        while stack:
            c = stack.pop()
            if c == goal or self._dist(c, goal) <= 1:
                return True
            for nb in self.neighbors(c):
                if nb in seen or not self.in_bounds(nb):
                    continue
                if self.terrain(nb) in ('water', 'impassable', 'marsh'):
                    continue
                seen.add(nb)
                stack.append(nb)
        return False


def generate_map(width: int, height: int, options: Optional[MapOptions] = None,
                 rng: Optional[random.Random] = None) -> Tuple[Board, Tuple[int, int], Dict]:
    gen = MapGenerator(width, height, options, rng)
    board, objective = gen.generate()
    return board, objective, gen.report
