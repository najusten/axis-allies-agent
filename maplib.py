"""
Hand-authored battlefields (maps/*.yaml).

A map file describes terrain in on-screen column,row coordinates and is meant to
be written by hand, so features are given compactly:

    name: "Villers-Bocage, June 1944"
    theater: western_europe          # used for flavour / the legend only
    description: "..."
    size: [17, 13]
    objective: [8, 6]
    terrain:                          # whole hexes
      town: [[8,6], [9,6]]
      forest: [[3,2], [3,3]]
    roads:                            # each road: waypoints, joined by straight hex lines
      - [[-1,6], [8,6], [17,4]]       # a waypoint just off the map makes the road run off it
    streams:                          # each stream: for rows top..bottom, the stream runs
      - {rows: [0, 12], between: [[0,7],[4,7],[5,6],[12,6]]}   # between col c and c+1 (piecewise)
    fords: [[3, 7]]                   # (row, col) boundary pieces left open
    hedges: [[[4,5],[5,5]], ...]      # explicit hex-side pairs
    hedge_runs: [[[2,2],[5,2]]]       # hedgerow along the south side of a run of hexes

Roads are laid as links (Board.add_road), so they follow the same rules as the
generated ones.
"""

import os
from typing import Dict, List, Optional, Tuple

from board import Board

MAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'maps')


def list_maps() -> List[dict]:
    import yaml
    out = []
    if not os.path.isdir(MAP_DIR):
        return out
    for fn in sorted(os.listdir(MAP_DIR)):
        if fn.endswith('.yaml'):
            with open(os.path.join(MAP_DIR, fn)) as fh:
                raw = yaml.safe_load(fh) or {}
            out.append({'id': 'map:' + fn[:-5], 'label': raw.get('name', fn[:-5]),
                        'description': raw.get('description', ''), 'theater': raw.get('theater')})
    return out


def _cube_line(a: Tuple[int, int], b: Tuple[int, int]) -> List[Tuple[int, int]]:
    """Axial hexes on the straight line from a to b (inclusive)."""
    n = max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs((a[0] + a[1]) - (b[0] + b[1])))
    if n == 0:
        return [a]
    out = []
    for i in range(n + 1):
        t = i / n
        q = a[0] + (b[0] - a[0]) * t + 1e-6
        r = a[1] + (b[1] - a[1]) * t + 1e-6
        s = -q - r
        rq, rr, rs = round(q), round(r), round(s)
        dq, dr, ds = abs(rq - q), abs(rr - r), abs(rs - s)
        if dq > dr and dq > ds:
            rq = -rr - rs
        elif dr > ds:
            rr = -rq - rs
        if not out or out[-1] != (rq, rr):
            out.append((rq, rr))
    return out


def load_map(map_id: str) -> Tuple[Board, Tuple[int, int], dict]:
    """Board, objective (axial) and the raw description for 'map:<file>'."""
    import yaml
    fn = map_id.split(':', 1)[1] if map_id.startswith('map:') else map_id
    with open(os.path.join(MAP_DIR, fn + '.yaml')) as fh:
        raw = yaml.safe_load(fh)
    W, H = raw.get('size', [17, 13])
    board = Board(int(W), int(H))
    A = Board.offset_to_axial

    for terrain, cells in (raw.get('terrain') or {}).items():
        for c in cells:
            if 0 <= c[0] < W and 0 <= c[1] < H:
                board.set_terrain(*A(*c), terrain)

    # streams first (roads crossing them become bridges)
    fords = {tuple(f) for f in (raw.get('fords') or [])}
    for st in raw.get('streams') or []:
        r0, r1 = st.get('rows', [0, H - 1])
        pieces = sorted(st['between'])              # [(from_row, col), ...]
        def col_at(row):
            c = pieces[0][1]
            for pr, pc in pieces:
                if row >= pr:
                    c = pc
            return c
        west = {(c, r) for r in range(r0, r1 + 1) for c in range(W) if c <= col_at(r)}
        for (c, r) in west:
            for nb in board.get_neighbors(*A(c, r)):
                oc, orow = Board.axial_to_offset(nb.q, nb.r)
                if (oc, orow) in west or not (r0 <= orow <= r1):
                    continue
                if (r, c) in fords or (orow, oc) in fords:
                    continue
                if board.get_edge_obstacle(*A(c, r), nb.q, nb.r) is None:
                    board.add_edge_obstacle(*A(c, r), nb.q, nb.r, 'stream')

    for road in raw.get('roads') or []:
        path: List[Tuple[int, int]] = []
        for a, b in zip(road, road[1:]):
            seg = _cube_line(A(*a), A(*b))
            path.extend(seg if not path else seg[1:])
        inside = [h for h in path if h in board.hexes]
        # consecutive on-map hexes of the line become road links
        run: List[Tuple[int, int]] = []
        for h in path:
            if h in board.hexes:
                run.append(h)
            else:
                if len(run) > 1:
                    board.add_road(run)
                run = []
        if len(run) > 1:
            board.add_road(run)

    for a, b in raw.get('hedges') or []:
        qa, ra = A(*a); qb, rb = A(*b)
        if board.hex_distance(qa, ra, qb, rb) == 1 and not board.road_between(qa, ra, qb, rb):
            board.add_edge_obstacle(qa, ra, qb, rb, 'hedge')
    for a, b in raw.get('hedge_runs') or []:
        for h in _cube_line(A(*a), A(*b)):
            below = Board.axial_to_offset(*h)
            nb = A(below[0], below[1] + 1)
            if h in board.hexes and nb in board.hexes and not board.road_between(*h, *nb) \
                    and board.get_edge_obstacle(*h, *nb) is None:
                board.add_edge_obstacle(*h, *nb, 'hedge')

    obj = raw.get('objective') or [W // 2, H // 2]
    return board, A(*obj), raw
