"""Determinism and lookahead isolation across whole games."""
from game_runner import AggressiveRandomAgent, GreedyAgent
from game_setup import quick_setup_broad
from evaluation import GameStateEvaluator
from scenario import build_systems
from turn_controller import TurnController
import random


def _play(seed: int):
    random.seed(seed)                       # army building / agent choices use the global RNG
    sys_ = build_systems(seed=seed)
    gs = quick_setup_broad(points=80).create_game()
    tc = TurnController(gs, sys_.executor, sys_.generator, sys_.initiative,
                        {"player1": AggressiveRandomAgent("a"), "player2": AggressiveRandomAgent("b")},
                        max_turns=12, movement_system=sys_.movement)
    res = tc.run_game()
    return res, tc.events


def test_same_seed_same_game():
    r1, e1 = _play(3)
    r2, e2 = _play(3)
    assert r1 == r2
    assert [ev.get('message') for ev in e1] == [ev.get('message') for ev in e2]


def test_greedy_lookahead_does_not_touch_live_state():
    random.seed(5)
    sys_ = build_systems(seed=5)
    gs = quick_setup_broad(points=80).create_game()
    greedy = GreedyAgent("g", sys_.executor, GameStateEvaluator())
    tc = TurnController(gs, sys_.executor, sys_.generator, sys_.initiative,
                        {"player1": greedy, "player2": AggressiveRandomAgent("b")},
                        max_turns=6, movement_system=sys_.movement)
    tc.start()
    # Walk a few phases; before every greedy decision the live pending-hit
    # bookkeeping must be unchanged by the agent's simulated lookahead.
    for _ in range(6):
        if tc.game_over:
            break
        legal = tc.legal_actions()
        if tc.current_player() == "player1" and legal:
            before = {k: [c.counter_type for c in v.counters] for k, v in gs.pending_hits.items()}
            positions = {uid: us.position for uid, us in gs.units.items()}
            greedy.choose_action(gs, legal, "player1")
            after = {k: [c.counter_type for c in v.counters] for k, v in gs.pending_hits.items()}
            assert before == after
            assert positions == {uid: us.position for uid, us in gs.units.items()}
        tc.run_until_human()  # no humans: plays the whole game
