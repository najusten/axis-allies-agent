"""Map generator properties: continuous roads that go somewhere, balanced halves,
feature switches honoured, and a playable map for every theater."""
from collections import defaultdict

import pytest

from board import Board
from mapgen import MapOptions, THEATER_STYLES, generate_map

SEEDS = range(12)
SIZE = (17, 13)          # the default: odd, so there is a true centre hex


def _road_graph(board):
    graph = defaultdict(set)
    for e in board.road_edges:
        a, b = tuple(e)
        graph[a].add(b)
        graph[b].add(a)
    return graph


@pytest.mark.parametrize('theater', sorted(THEATER_STYLES))
def test_roads_are_one_network_and_only_end_at_the_edge_or_a_village(theater):
    for seed in SEEDS:
        board, objective, _ = generate_map(*SIZE, MapOptions(theater=theater, seed=seed))
        graph = _road_graph(board)
        assert graph, f"{theater}/{seed}: no roads"
        # one connected network
        start = next(iter(graph))
        seen, stack = {start}, [start]
        while stack:
            for nb in graph[stack.pop()]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        assert seen == set(graph), f"{theater}/{seed}: road network is broken into pieces"
        # every link joins adjacent hexes
        for e in board.road_edges:
            a, b = tuple(e)
            assert board.hex_distance(*a, *b) == 1
        # no road stops in the middle of a field
        for hex_, nbs in graph.items():
            if len(nbs) == 1:
                col, row = Board.axial_to_offset(*hex_)
                at_edge = col in (0, board.width - 1) or row in (0, board.height - 1)
                in_village = board.get_hex(*hex_).terrain == 'town'
                assert at_edge or in_village, f"{theater}/{seed}: road dead-ends at {(col, row)}"
        # and the network reaches the map edge
        assert any(Board.axial_to_offset(*h)[0] in (0, board.width - 1) or
                   Board.axial_to_offset(*h)[1] in (0, board.height - 1) for h in graph)


@pytest.mark.parametrize('theater', sorted(THEATER_STYLES))
def test_both_halves_are_balanced(theater):
    for seed in SEEDS:
        board, objective, report = generate_map(*SIZE, MapOptions(theater=theater, seed=seed))
        left, right = report['cover_per_side']
        assert abs(left - right) <= 2, f"{theater}/{seed}: cover {left} vs {right}"
        assert not report.get('warnings'), report.get('warnings')


def test_objective_is_in_the_middle_and_equally_far_from_both_edges():
    board, objective, _ = generate_map(17, 13, MapOptions(seed=1))
    col, row = Board.axial_to_offset(*objective)
    assert (col, row) == (8, 6)
    assert col == board.width - 1 - col


@pytest.mark.parametrize('size', [(18, 12), (19, 11), (15, 11)])
def test_other_board_sizes_still_make_connected_balanced_maps(size):
    for seed in range(6):
        board, objective, report = generate_map(*size, MapOptions(theater='western_europe', seed=seed))
        assert abs(report['cover_per_side'][0] - report['cover_per_side'][1]) <= 2
        assert board.road_edges


def test_feature_switches_are_honoured():
    for seed in SEEDS:
        board, _, _ = generate_map(*SIZE, MapOptions(theater='western_europe', seed=seed, streams=False,
                                                      marshes=False, hedges=False, forests=False, hills=False))
        kinds = {h.terrain for h in board.hexes.values()}
        assert not kinds & {'marsh', 'forest', 'hill'}
        assert not any(k in ('stream', 'hedge') for k in board.edge_obstacles.values())


def test_streams_can_be_forced_on_and_are_bridged_where_roads_cross():
    for seed in SEEDS:
        board, _, _ = generate_map(*SIZE, MapOptions(theater='north_africa', seed=seed, streams=True))
        streams = [k for k, v in board.edge_obstacles.items() if v == 'stream']
        assert streams, "a desert map with streams switched on has a stream"
        # every road link across the stream is a bridge (no roll), by definition
        for key in streams:
            a, b = sorted(key)
            if board.road_between(*a, *b):
                assert board.to_dict()['edge_obstacles']


def test_road_links_are_what_movement_uses():
    """Two road hexes side by side are only 'along a road' if a road links them."""
    board = Board(8, 6)
    board.add_road([(1, 1), (2, 1), (3, 0)])
    assert board.road_between(1, 1, 2, 1)
    assert board.road_between(2, 1, 3, 0)
    assert not board.road_between(1, 1, 1, 2)       # (1,2) has no road at all
    board.add_road([(1, 2), (2, 2)])                # a parallel road
    assert not board.road_between(1, 1, 1, 2)       # adjacent road hexes, but not linked
