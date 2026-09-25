"""Controller-level rules that scenarios can't express: the Vanguard pre-game
phase and the end-of-turn removal of Aircraft."""
import pytest

from action import MoveAction
from board import Board
from game_state import GameState, UnitState, GamePhase
from scenario import build_systems, make_unit
from turn_controller import TurnController, VANGUARD_PHASE


def _controller(units, seed=1):
    systems = build_systems(seed=seed)
    gs = GameState(Board(14, 10), objective_position=(7, 5))
    for us in units:
        us.unit.id = us.unit.name          # readable ids in assertions
        gs.add_unit(us)
    tc = TurnController(gs, systems.executor, systems.generator, systems.initiative,
                        {"player1": None, "player2": None}, movement_system=systems.movement)
    return gs, tc


def test_vanguard_unit_moves_at_speed_four_before_turn_one():
    scout = UnitState(make_unit('scout', unit_type='Soldier', defense=4, speed=1,
                                per=(6, 4, 2), abilities='Vanguard'), (2, 4), 'player1', 4)
    plain = UnitState(make_unit('plain', unit_type='Soldier', defense=4, speed=1, per=(6, 4, 2)),
                      (2, 6), 'player1', 4)
    enemy = UnitState(make_unit('enemy', unit_type='Soldier', defense=4, speed=1, per=(6, 4, 2)),
                      (12, 4), 'player2', 4)
    gs, tc = _controller([scout, plain, enemy])
    tc.start()
    assert tc.current()[0] == VANGUARD_PHASE
    movers = {a.unit_id for a in tc.legal_actions()}
    assert movers == {'scout'}, "only Vanguard units move before turn 1"
    dests = {(a.to_q, a.to_r) for a in tc.legal_actions()}
    assert (6, 4) in dests, "Vanguard moves at speed 4, not the unit's own speed"
    tc.apply(MoveAction('scout', 2, 4, 6, 4, max_speed=4))
    assert gs.get_unit_state('scout').position == (6, 4)
    assert not gs.get_unit_state('scout').has_moved, "the pre-game move doesn't use up turn 1"


def test_aircraft_leave_the_map_at_the_end_of_the_turn():
    plane = UnitState(make_unit('plane', unit_type='Aircraft', defense='4/4',
                                per=(8, 6, 4), abilities='Aircraft'), (4, 4), 'player1', 4)
    plane.is_aircraft_on_map = True
    ground = UnitState(make_unit('ground', unit_type='Soldier', defense=4, speed=1, per=(6, 4, 2)),
                       (2, 4), 'player1', 4)
    enemy = UnitState(make_unit('enemy', unit_type='Soldier', defense=4, speed=1, per=(6, 4, 2)),
                      (12, 4), 'player2', 4)
    gs, tc = _controller([plane, ground, enemy])
    gs.current_phase = GamePhase.ASSAULT
    tc._end_turn()
    assert not gs.get_unit_state('plane').is_aircraft_on_map
    assert gs.get_unit_state('plane').position == (-99, -99), "an off-map aircraft has no hex"
    assert gs.get_unit_state('plane').aircraft_was_placed, "it still counts for the points tally"
