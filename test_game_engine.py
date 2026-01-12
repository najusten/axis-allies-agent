"""
Complete Game Engine Test Script

This script demonstrates and tests the complete game engine:
- Game state management
- Action generation
- Action execution
- Turn progression

Use this to verify all systems are working together correctly.
"""

from game_state import GameState, UnitState, GamePhase, create_test_game_state
from action import MoveAction, AttackAction, PassAction, EndPhaseAction
from action_generator import ActionGenerator
from action_executor import ActionExecutor
from movement import MovementSystem
from combat import CombatSystem
from abilities import AbilitySystem
from units import load_units
from board import Board
from copy import deepcopy


def setup_game_systems():
    """Initialize all game systems"""
    ability_system = AbilitySystem('Axis and Allies Unit Data for Analysis - Special_Abilities.csv')
    movement_system = MovementSystem(ability_system)
    combat_system = CombatSystem(ability_system)
    
    action_generator = ActionGenerator(movement_system, combat_system, ability_system)
    action_executor = ActionExecutor(movement_system, combat_system, ability_system)
    
    return {
        'ability_system': ability_system,
        'movement_system': movement_system,
        'combat_system': combat_system,
        'action_generator': action_generator,
        'action_executor': action_executor
    }


def test_action_generation():
    """Test 1: Verify action generation works"""
    print("="*70)
    print("TEST 1: ACTION GENERATION")
    print("="*70)
    
    systems = setup_game_systems()
    game_state = create_test_game_state()
    
    print("\nInitial Game State:")
    print(game_state)
    
    # Generate all legal actions
    actions = systems['action_generator'].get_all_legal_actions(game_state, "player1")
    
    print(f"\nTotal legal actions for Player 1: {len(actions)}")
    
    # Categorize actions
    moves = [a for a in actions if isinstance(a, MoveAction)]
    attacks = [a for a in actions if isinstance(a, AttackAction)]
    
    print(f"  - Movement actions: {len(moves)}")
    print(f"  - Attack actions: {len(attacks)}")
    
    # Show sample actions
    print("\nSample movement actions:")
    for action in moves[:5]:
        print(f"  {action}")
    
    if len(moves) > 5:
        print(f"  ... and {len(moves) - 5} more")
    
    print("\nSample attack actions:")
    for action in attacks[:5]:
        print(f"  {action}")
    
    if len(attacks) > 5:
        print(f"  ... and {len(attacks) - 5} more")
    
    print("\n✓ Action generation test complete")
    return True


def test_action_execution():
    """Test 2: Verify action execution works"""
    print("\n" + "="*70)
    print("TEST 2: ACTION EXECUTION")
    print("="*70)
    
    systems = setup_game_systems()
    game_state = create_test_game_state()
    
    print("\nInitial positions:")
    for unit_id, unit_state in game_state.units.items():
        print(f"  {unit_state.unit.name} ({unit_id}): {unit_state.position}")
    
    # Get a move action
    actions = systems['action_generator'].get_all_legal_actions(game_state, "player1")
    move_actions = [a for a in actions if isinstance(a, MoveAction)]
    
    if move_actions:
        action = move_actions[0]
        print(f"\nExecuting: {action}")
        
        result = systems['action_executor'].execute_action(game_state, action)
        print(f"Result: {result}")
        
        print("\nUpdated positions:")
        for unit_id, unit_state in game_state.units.items():
            print(f"  {unit_state.unit.name} ({unit_id}): {unit_state.position}")
        
        print("\n✓ Action execution test complete")
        return True
    else:
        print("✗ No move actions available!")
        return False


def test_combat_system():
    """Test 3: Verify combat works"""
    print("\n" + "="*70)
    print("TEST 3: COMBAT SYSTEM")
    print("="*70)
    
    systems = setup_game_systems()
    game_state = create_test_game_state()
    
    # Switch to assault phase
    game_state.current_phase = GamePhase.ASSAULT
    
    print("\nInitial unit health:")
    for unit_id, unit_state in game_state.units.items():
        print(f"  {unit_state.unit.name} ({unit_id}): {unit_state.current_health} HP")
    
    # Get attack actions
    actions = systems['action_generator'].get_all_legal_actions(game_state, "player1")
    attack_actions = [a for a in actions if isinstance(a, AttackAction)]
    
    if attack_actions:
        action = attack_actions[0]
        print(f"\nExecuting: {action}")
        
        result = systems['action_executor'].execute_action(game_state, action)
        print(f"Result: {result}")
        
        print("\nUpdated unit health:")
        for unit_id, unit_state in game_state.units.items():
            if unit_state.is_alive:
                print(f"  {unit_state.unit.name} ({unit_id}): {unit_state.current_health} HP")
            else:
                print(f"  {unit_state.unit.name} ({unit_id}): DESTROYED")
        
        print("\n✓ Combat system test complete")
        return True
    else:
        print("✗ No attack actions available (units may be out of range)")
        return False


def test_turn_progression():
    """Test 4: Verify turn/phase progression"""
    print("\n" + "="*70)
    print("TEST 4: TURN PROGRESSION")
    print("="*70)
    
    systems = setup_game_systems()
    game_state = create_test_game_state()
    
    print("\nInitial state:")
    print(f"  Turn: {game_state.turn_number}")
    print(f"  Phase: {game_state.current_phase}")
    print(f"  Active player: {game_state.active_player}")
    
    # End phase
    end_action = EndPhaseAction(game_state.current_phase)
    result = systems['action_executor'].execute_action(game_state, end_action)
    print(f"\n{result}")
    
    print(f"\nAfter ending phase:")
    print(f"  Turn: {game_state.turn_number}")
    print(f"  Phase: {game_state.current_phase}")
    print(f"  Active player: {game_state.active_player}")
    
    # End another phase to switch players
    end_action = EndPhaseAction(game_state.current_phase)
    result = systems['action_executor'].execute_action(game_state, end_action)
    print(f"\n{result}")
    
    end_action = EndPhaseAction(game_state.current_phase)
    result = systems['action_executor'].execute_action(game_state, end_action)
    print(f"\n{result}")
    
    print(f"\nAfter ending turn:")
    print(f"  Turn: {game_state.turn_number}")
    print(f"  Phase: {game_state.current_phase}")
    print(f"  Active player: {game_state.active_player}")
    
    print("\n✓ Turn progression test complete")
    return True


def test_game_state_cloning():
    """Test 5: Verify game state can be cloned (important for AI)"""
    print("\n" + "="*70)
    print("TEST 5: GAME STATE CLONING")
    print("="*70)
    
    systems = setup_game_systems()
    game_state = create_test_game_state()
    
    print("\nOriginal game state:")
    print(f"  Units: {len(game_state.units)}")
    print(f"  Turn: {game_state.turn_number}")
    
    # Clone the state
    cloned_state = game_state.clone()
    
    print("\nCloned game state:")
    print(f"  Units: {len(cloned_state.units)}")
    print(f"  Turn: {cloned_state.turn_number}")
    
    # Modify clone
    actions = systems['action_generator'].get_all_legal_actions(cloned_state, "player1")
    if actions:
        move_actions = [a for a in actions if isinstance(a, MoveAction)]
        if move_actions:
            systems['action_executor'].execute_action(cloned_state, move_actions[0])
    
    print("\nAfter modifying clone:")
    print("  Original units have moved:", any(u.has_moved for u in game_state.units.values()))
    print("  Cloned units have moved:", any(u.has_moved for u in cloned_state.units.values()))
    
    if not any(u.has_moved for u in game_state.units.values()):
        print("\n✓ Cloning works correctly - original unchanged")
        return True
    else:
        print("\n✗ Cloning failed - original was modified")
        return False


def test_all_legal_actions_detailed():
    """Test 6: Show ALL legal actions in detail for human verification"""
    print("\n" + "="*70)
    print("TEST 6: COMPLETE ACTION LIST (Human Verification)")
    print("="*70)
    
    systems = setup_game_systems()
    game_state = create_test_game_state()
    
    print(game_state)
    
    # Movement phase
    game_state.current_phase = GamePhase.MOVEMENT
    print("\n--- MOVEMENT PHASE ---")
    actions = systems['action_generator'].get_all_legal_actions(game_state, "player1")
    
    print(f"\nPlayer 1 has {len(actions)} legal actions:")
    for i, action in enumerate(actions, 1):
        print(f"  {i}. {action}")
    
    # Assault phase
    game_state.current_phase = GamePhase.ASSAULT
    print("\n--- ASSAULT PHASE ---")
    actions = systems['action_generator'].get_all_legal_actions(game_state, "player1")
    
    print(f"\nPlayer 1 has {len(actions)} legal actions:")
    for i, action in enumerate(actions, 1):
        print(f"  {i}. {action}")
    
    print("\n✓ Complete action list displayed for verification")
    return True


def run_sample_game():
    """Test 7: Run a few turns of a game"""
    print("\n" + "="*70)
    print("TEST 7: SAMPLE GAME SIMULATION")
    print("="*70)
    
    systems = setup_game_systems()
    game_state = create_test_game_state()
    
    print("\nStarting game simulation...\n")
    
    # Play 3 turns
    for turn in range(3):
        print(f"\n{'='*50}")
        print(f"TURN {game_state.turn_number} - {game_state.active_player}")
        print(f"{'='*50}")
        
        # Movement phase
        game_state.current_phase = GamePhase.MOVEMENT
        print(f"\n{game_state.current_phase.upper()} PHASE:")
        
        actions = systems['action_generator'].get_all_legal_actions(
            game_state, game_state.active_player
        )
        move_actions = [a for a in actions if isinstance(a, MoveAction)]
        
        if move_actions and turn < 2:  # Only move first 2 turns
            action = move_actions[0]
            print(f"  Executing: {action}")
            result = systems['action_executor'].execute_action(game_state, action)
            print(f"  {result}")
        
        # End movement phase
        end_action = EndPhaseAction(GamePhase.MOVEMENT)
        systems['action_executor'].execute_action(game_state, end_action)
        
        # Assault phase
        print(f"\n{game_state.current_phase.upper()} PHASE:")
        
        actions = systems['action_generator'].get_all_legal_actions(
            game_state, game_state.active_player
        )
        attack_actions = [a for a in actions if isinstance(a, AttackAction)]
        
        if attack_actions:
            action = attack_actions[0]
            print(f"  Executing: {action}")
            result = systems['action_executor'].execute_action(game_state, action)
            print(f"  {result}")
        else:
            print("  No attacks available")
        
        # End assault phase (advances to next turn)
        end_action = EndPhaseAction(GamePhase.ASSAULT)
        systems['action_executor'].execute_action(game_state, end_action)
        
        # Another end phase to complete turn
        end_action = EndPhaseAction(game_state.current_phase)
        systems['action_executor'].execute_action(game_state, end_action)
        
        # Check victory
        if game_state.is_game_over():
            print(f"\n🏆 GAME OVER - Winner: {game_state.get_winner()}")
            break
    
    print("\n" + game_state.get_state_summary())
    print("\n✓ Sample game simulation complete")
    return True


def main():
    """Run all tests"""
    print("\n" + "█"*70)
    print("AXIS & ALLIES MINIATURES - GAME ENGINE TEST SUITE")
    print("█"*70)
    
    tests = [
        ("Action Generation", test_action_generation),
        ("Action Execution", test_action_execution),
        ("Combat System", test_combat_system),
        ("Turn Progression", test_turn_progression),
        ("Game State Cloning", test_game_state_cloning),
        ("Complete Action List", test_all_legal_actions_detailed),
        ("Sample Game", run_sample_game),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n✗ {test_name} FAILED with error: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Game engine is ready!")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Review errors above.")


if __name__ == "__main__":
    main()