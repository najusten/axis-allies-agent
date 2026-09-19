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


def test_mcts_rollouts_leave_live_game_untouched():
    """MCTS simulates forward on clones: the live state, the shared dice RNG
    stream and the executor's human-decision hooks must be exactly as before."""
    from mcts import MCTSAgent
    random.seed(9)
    sys_ = build_systems(seed=9)
    gs = quick_setup_broad(points=80).create_game()
    mcts = MCTSAgent("m", time_limit=0.2, movement_system=sys_.movement, rng=random.Random(1))
    tc = TurnController(gs, sys_.executor, sys_.generator, sys_.initiative,
                        {"player1": mcts, "player2": AggressiveRandomAgent("b")},
                        max_turns=6, movement_system=sys_.movement)
    mcts.attach(tc)
    tc.start()
    while tc.current() and tc.current()[1] != "player1":
        tc._run_ai_phase(tc.current()[1], None)
        tc.end_phase()
    legal = tc.legal_actions() + [__import__('action').PassAction("player1")]
    assert legal
    snapshot = gs.clone()
    rng_state = sys_.executor.dice.rng.getstate()
    asker = sys_.executor.df_asker
    action = mcts.choose_action(gs, legal, "player1")
    assert action is not None
    assert mcts.stats['rollouts'] > 0
    assert sys_.executor.dice.rng.getstate() == rng_state
    assert sys_.executor.df_asker is asker and sys_.executor.df_decisions == {}
    assert tc.game_state is gs
    for uid, us in gs.units.items():
        ref = snapshot.units[uid]
        assert (us.position, us.is_alive, us.has_moved, us.is_disrupted) == \
               (ref.position, ref.is_alive, ref.has_moved, ref.is_disrupted)
    assert gs.turn_number == snapshot.turn_number and gs.current_phase == snapshot.current_phase
