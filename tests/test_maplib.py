"""Hand-authored battlefields load, and their roads form one network that only
ends at the map edge (or in a village)."""
from collections import defaultdict

import pytest

from board import Board
from maplib import list_maps, load_map


@pytest.mark.parametrize('map_id', [m['id'] for m in list_maps()])
def test_authored_map_loads_with_one_road_network(map_id):
    board, objective, raw = load_map(map_id)
    assert objective in board.hexes
    graph = defaultdict(set)
    for e in board.road_edges:
        a, b = tuple(e)
        graph[a].add(b)
        graph[b].add(a)
    assert graph
    start = next(iter(graph))
    seen, stack = {start}, [start]
    while stack:
        for nb in graph[stack.pop()]:
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)
    assert seen == set(graph), f"{map_id}: roads in pieces"
    for h, nbs in graph.items():
        if len(nbs) == 1:
            col, row = Board.axial_to_offset(*h)
            assert col in (0, board.width - 1) or row in (0, board.height - 1) \
                or board.get_hex(*h).terrain == 'town', f"{map_id}: road stops at {(col, row)}"
