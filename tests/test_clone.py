"""GameState.clone() must copy every field and share nothing mutable."""
import dataclasses

from board import Board
from game_state import GameState, UnitState, GamePhase
from scenario import make_unit


def _sample_state():
    board = Board(8, 8)
    board.set_terrain(2, 2, 'forest')
    board.add_edge_obstacle(1, 1, 2, 1, 'barbed wire')
    gs = GameState(board, objective_position=(4, 4))
    a = make_unit("A", unit_type='Vehicle', defense="5/3")
    b = make_unit("B")
    ua = UnitState(a, (1, 1), "player1", 5)
    ub = UnitState(b, (6, 6), "player2", 4)
    ua.facing = 2
    ua.targets_attacked_this_turn.add(b.id)
    ua.headshot_used = True
    gs.add_unit(ua)
    gs.add_unit(ub)
    gs.current_phase = GamePhase.ASSAULT
    gs.turn_number = 3
    gs.smoke_screens.add((3, 3))
    gs.defensive_fire_used.add(b.id)
    gs.rng_seed = 7
    return gs, ua, ub


def test_every_unit_state_field_is_copied():
    gs, ua, ub = _sample_state()
    c = gs.clone()
    for uid, us in gs.units.items():
        cu = c.units[uid]
        for f in dataclasses.fields(UnitState):
            assert getattr(us, f.name) == getattr(cu, f.name), f.name
            value = getattr(us, f.name)
            if isinstance(value, (set, list, dict)):
                assert value is not getattr(cu, f.name), f"{f.name} shared between clones"
        assert cu.unit is us.unit, "Unit objects are immutable and should be shared"


def test_every_game_state_attribute_is_present():
    gs, _, _ = _sample_state()
    c = gs.clone()
    missing = [k for k in vars(gs) if k not in vars(c)]
    assert not missing, missing
    assert c.board.edge_obstacles == gs.board.edge_obstacles
    assert c.board.edge_obstacles is not gs.board.edge_obstacles
    assert c.board.get_hex(2, 2).terrain == 'forest'
    assert c.smoke_screens == {(3, 3)} and c.smoke_screens is not gs.smoke_screens
    assert c.defensive_fire_used == gs.defensive_fire_used
    assert c.objective_position == (4, 4)
    assert c.rng_seed == 7


def test_mutating_clone_leaves_original_alone():
    gs, ua, ub = _sample_state()
    c = gs.clone()
    c.units[ua.unit.id].is_disrupted = True
    c.move_unit(ua.unit.id, 2, 2)
    c.defensive_fire_used.add("x")
    assert not ua.is_disrupted
    assert ua.position == (1, 1)
    assert "x" not in gs.defensive_fire_used
    assert gs.board.get_hex(1, 1).unit is ua.unit
