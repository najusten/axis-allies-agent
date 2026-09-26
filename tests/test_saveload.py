"""Save / load round trip: a game exported mid-turn and reloaded is the same game."""
import random

import yaml

import simulate
from game_setup import GameSetup, GameSetupConfig
from scenario import build_systems, scenario_from_dict
from stateio import dump_yaml, export_state
from turn_controller import TurnController


def _play_some(seed=11, actions=60):
    random.seed(seed)
    systems = build_systems(seed=seed)
    gs = GameSetup(GameSetupConfig(points_per_side=100)).create_game()
    gs.rng_seed = seed
    agents = {p: simulate.make_agent('heuristic', p, systems) for p in ('player1', 'player2')}
    tc = TurnController(gs, systems.executor, systems.generator, systems.initiative, agents,
                        max_turns=20, movement_system=systems.movement)
    tc.start()
    n = 0
    while n < actions and not tc.game_over:
        cur = tc.current()
        legal = tc.legal_actions()
        if not legal:
            tc.end_phase()
            continue
        action = agents[cur[1]].choose_action(tc.game_state, legal, cur[1])
        tc.apply(action)
        n += 1
    return tc


def _board_view(b):
    d = b.to_dict()
    return {
        'size': (d['width'], d['height']),
        'hexes': sorted((h['q'], h['r'], h['terrain'], h.get('road', False)) for h in d['hexes']),
        'roads': sorted(tuple(sorted(map(tuple, e))) for e in d['roads']),
        'edges': sorted((tuple(e['a']), tuple(e['b']), e['type'], e['bridge']) for e in d['edge_obstacles']),
    }


def _unit_view(gs):
    out = {}
    for uid, us in gs.units.items():
        d = us.to_dict()
        d.pop('card', None)
        out[uid] = d
    return out


def test_export_then_load_reproduces_the_position():
    tc = _play_some()
    gs = tc.game_state
    data = export_state(gs, tc, meta={'mode': 'hotseat'})
    text = dump_yaml(data)
    loaded = scenario_from_dict(yaml.safe_load(text), name='roundtrip').game_state

    assert _board_view(loaded.board) == _board_view(gs.board)
    assert loaded.objective_position == gs.objective_position
    assert loaded.turn_number == gs.turn_number
    assert loaded.current_phase == gs.current_phase
    assert loaded.active_player == gs.active_player
    assert _unit_view(loaded) == _unit_view(gs)
    assert loaded.defensive_fire_used == gs.defensive_fire_used
    for uid, ph in gs.pending_hits.items():
        want = [c.counter_type for c in ph.counters if not c.face_up]
        got = [c.counter_type for c in loaded.pending_hits.get(uid, type(ph)(uid)).counters]
        assert got == want, uid


def test_a_loaded_game_plays_on():
    tc = _play_some(seed=12, actions=40)
    data = yaml.safe_load(dump_yaml(export_state(tc.game_state, tc)))
    sc = scenario_from_dict(data, name='resume')
    agents = {p: simulate.make_agent('heuristic', p, sc.systems) for p in ('player1', 'player2')}
    tc2 = TurnController(sc.game_state, sc.systems.executor, sc.systems.generator, sc.systems.initiative,
                         agents, max_turns=12, movement_system=sc.systems.movement)
    c = data['controller']
    tc2._started = True
    tc2.phase_queue = [tuple(p) for p in c['phase_queue']]
    tc2.phase_idx = c['phase_idx']
    tc2.turn_order = list(c['turn_order'])
    tc2.run_until_human()           # both AI: runs to the end
    assert tc2.game_over
