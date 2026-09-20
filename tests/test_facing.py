"""Rulebook facing: a hex is in front of a Vehicle if the line from its centre to the
Vehicle's hex goes through one of the front three sides or the front two corners.
Hexes directly to the right or left (the side corners) and the same hex are rear."""
from facing import is_front_arc_attack, HexDirection

FACING = HexDirection(0)          # axial (1,0)

FRONT = [(1, 0), (1, -1), (0, 1), (2, -1), (1, 1), (3, 0), (4, -2), (2, 2)]
REAR = [(0, -1), (-1, 0), (-1, 1), (1, -2), (-1, 2), (-3, 1), (2, -4), (-2, 4)]


def test_front_arc_hexes():
    for pos in FRONT:
        assert is_front_arc_attack(pos, (0, 0), FACING), pos


def test_rear_arc_hexes_including_side_corners():
    for pos in REAR:
        assert not is_front_arc_attack(pos, (0, 0), FACING), pos


def test_same_hex_is_rear():
    assert not is_front_arc_attack((0, 0), (0, 0), FACING)
