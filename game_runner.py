"""
Game Runner for Axis & Allies Miniatures

Executes complete games between two agents.
Handles the full game loop: phases, turns, victory conditions.
"""

import random
from typing import List, Optional, Tuple, Callable
from copy import deepcopy

from game_state import GameState, GamePhase, UnitState
from board import Board
from units import Unit
from action import Action, MoveAction, AttackAction, PassAction, EndPhaseAction
from action_generator import ActionGenerator
from action_executor import ActionExecutor, ActionResult
from movement import MovementSystem
from abilities import AbilitySystem


class Agent:
    """Base class for game-playing agents"""
    
    def __init__(self, name: str = "Agent"):
        self.name = name
    
    def choose_action(self, game_state: GameState, 
                      legal_actions: List[Action], 
                      player: str) -> Action:
        """
        Choose an action from the list of legal actions.
        Override this in subclasses.
        """
        raise NotImplementedError


class RandomAgent(Agent):
    """Agent that chooses random legal actions"""
    
    def __init__(self, name: str = "RandomAgent"):
        super().__init__(name)
    
    def choose_action(self, game_state: GameState,
                      legal_actions: List[Action],
                      player: str) -> Action:
        """Pick a random legal action"""
        return random.choice(legal_actions)


class AggressiveRandomAgent(Agent):
    """Agent that prefers attacks and moves toward enemies"""
    
    def __init__(self, name: str = "AggressiveAgent"):
        super().__init__(name)
    
    def choose_action(self, game_state: GameState,
                      legal_actions: List[Action],
                      player: str) -> Action:
        """Prefer attacks, then moves toward enemies, then other actions"""
        attacks = [a for a in legal_actions if isinstance(a, AttackAction)]
        moves = [a for a in legal_actions if isinstance(a, MoveAction)]
        
        # Always attack if possible
        if attacks:
            return random.choice(attacks)
        
        # Move toward nearest enemy
        if moves:
            enemy_player = "player2" if player == "player1" else "player1"
            enemy_units = game_state.get_units_by_owner(enemy_player)
            
            if enemy_units:
                # Find center of enemy units
                enemy_positions = [eu.position for eu in enemy_units]
                avg_q = sum(p[0] for p in enemy_positions) / len(enemy_positions)
                avg_r = sum(p[1] for p in enemy_positions) / len(enemy_positions)
                
                # Score moves by how close they get to enemy center
                def move_score(move):
                    dist = abs(move.to_q - avg_q) + abs(move.to_r - avg_r)
                    return -dist  # Negative so closer = higher score
                
                # Pick move that gets closest (with some randomness)
                moves_sorted = sorted(moves, key=move_score, reverse=True)
                # Pick from top 3 best moves randomly
                best_moves = moves_sorted[:min(3, len(moves_sorted))]
                return random.choice(best_moves)
            else:
                return random.choice(moves)
        
        # Filter out ability uses that don't help (just clutter the log)
        non_ability_actions = [a for a in legal_actions 
                              if not isinstance(a, type(legal_actions[0]).__class__) 
                              or not hasattr(a, 'ability_name')]
        
        if non_ability_actions:
            return random.choice(non_ability_actions)
        
        return random.choice(legal_actions)


class GameRunner:
    """
    Runs complete games between two agents.
    
    Simplified game flow (per turn):
    1. Player 1 Movement Phase
    2. Player 2 Movement Phase  
    3. Player 1 Assault Phase
    4. Player 2 Assault Phase
    5. End of Turn (clear disruption from previous turn, check victory)
    """
    
    def __init__(self, ability_file: str = None, verbose: bool = True):
        """
        Initialize the game runner.
        
        Args:
            ability_file: Path to abilities CSV (auto-detected if None)
            verbose: If True, print game events
        """
        import os
        
        # Auto-detect ability file
        if ability_file is None:
            if os.path.exists('Axis_and_Allies_Unit_Data_for_Analysis_-_Special_Abilities.csv'):
                ability_file = 'Axis_and_Allies_Unit_Data_for_Analysis_-_Special_Abilities.csv'
            else:
                ability_file = 'Axis and Allies Unit Data for Analysis - Special_Abilities.csv'
        
        self.ability_system = AbilitySystem(ability_file)
        self.movement_system = MovementSystem(self.ability_system)
        self.action_generator = ActionGenerator(
            self.movement_system, None, self.ability_system
        )
        self.action_executor = ActionExecutor(
            self.movement_system, None, self.ability_system
        )
        self.verbose = verbose
    
    def log(self, message: str):
        """Print if verbose mode is on"""
        if self.verbose:
            print(message)
    
    def run_game(self, game_state: GameState,
                 agent1: Agent, agent2: Agent,
                 max_turns: int = 20) -> dict:
        """
        Run a complete game between two agents.
        
        Args:
            game_state: Initial game state
            agent1: Agent controlling player1
            agent2: Agent controlling player2
            max_turns: Maximum turns before draw
        
        Returns:
            Dictionary with game results
        """
        agents = {"player1": agent1, "player2": agent2}
        
        self.log("=" * 70)
        self.log("GAME START")
        self.log("=" * 70)
        self.log(f"Player 1: {agent1.name}")
        self.log(f"Player 2: {agent2.name}")
        self.log(f"Max turns: {max_turns}")
        self.log("")
        
        turn_history = []
        
        while game_state.turn_number <= max_turns:
            turn_events = []
            
            self.log(f"\n{'='*60}")
            self.log(f"TURN {game_state.turn_number}")
            self.log(f"{'='*60}")
            
            # === MOVEMENT PHASE ===
            for player in ["player1", "player2"]:
                game_state.current_phase = GamePhase.MOVEMENT
                game_state.active_player = player
                agent = agents[player]
                
                # Reset defensive fire tracking for this phase
                self.action_executor.reset_defensive_fire_phase()
                
                self.log(f"\n--- {player} Movement Phase ---")
                
                phase_actions = self._run_phase(
                    game_state, agent, player, max_actions=10
                )
                turn_events.extend(phase_actions)
                
                # Check for victory after each phase
                winner = game_state.check_victory_conditions()
                if winner:
                    return self._game_result(winner, game_state, turn_history, "elimination")
            
            # === ASSAULT PHASE ===
            for player in ["player1", "player2"]:
                game_state.current_phase = GamePhase.ASSAULT
                game_state.active_player = player
                agent = agents[player]
                
                self.log(f"\n--- {player} Assault Phase ---")
                
                phase_actions = self._run_phase(
                    game_state, agent, player, max_actions=20
                )
                turn_events.extend(phase_actions)
                
                # Check for victory
                winner = game_state.check_victory_conditions()
                if winner:
                    return self._game_result(winner, game_state, turn_history, "elimination")
            
            # === END OF TURN ===
            self._end_of_turn(game_state)
            turn_history.append(turn_events)
            
            # Increment turn
            game_state.turn_number += 1
            
            # Reset unit flags for next turn
            for unit_state in game_state.units.values():
                unit_state.has_moved = False
                unit_state.has_attacked = False
                unit_state.abilities_used.clear()
        
        # Max turns reached - determine winner by remaining points
        return self._game_result(None, game_state, turn_history, "max_turns")
    
    def _run_phase(self, game_state: GameState, agent: Agent,
                   player: str, max_actions: int = 10) -> List[dict]:
        """
        Run a single phase for one player.
        
        Returns list of action events.
        """
        events = []
        actions_taken = 0
        
        while actions_taken < max_actions:
            # Get legal actions
            legal_actions = self.action_generator.get_all_legal_actions(
                game_state, player
            )
            
            # Filter to phase-appropriate actions
            if game_state.current_phase == GamePhase.MOVEMENT:
                # In movement phase, only moves (no attacks)
                legal_actions = [a for a in legal_actions 
                               if isinstance(a, MoveAction)]
            
            # Always allow passing/ending phase
            if not legal_actions:
                self.log(f"  {player}: No actions available, ending phase")
                break
            
            # Add pass option
            legal_actions.append(PassAction(player))
            
            # Agent chooses action
            action = agent.choose_action(game_state, legal_actions, player)
            
            # Handle pass
            if isinstance(action, PassAction):
                self.log(f"  {player}: Passes")
                break
            
            # Execute action
            result = self.action_executor.execute_action(game_state, action)
            
            event = {
                'player': player,
                'action': str(action),
                'success': result.success,
                'message': result.message
            }
            events.append(event)
            
            if result.success:
                self.log(f"  {result.message}")
                
                # Check if unit was destroyed
                if result.unit_destroyed:
                    self.log(f"    💥 {result.unit_destroyed} destroyed!")
            else:
                self.log(f"  FAILED: {result.message}")
            
            actions_taken += 1
            
            # After an attack, the unit can't do more this phase
            if isinstance(action, AttackAction):
                # In simplified rules, one attack per unit per turn is already enforced
                pass
        
        return events
    
    def _end_of_turn(self, game_state: GameState):
        """
        Process end of turn effects.
        
        In full rules this would:
        - Apply simultaneous damage (Casualty Phase)
        - Clear face-up disrupted counters from previous turn
        - Flip face-down counters face-up
        
        Simplified: Just clear disruption that's been active for a turn
        """
        self.log(f"\n--- End of Turn {game_state.turn_number} ---")
        
        # Report surviving units
        p1_units = game_state.get_units_by_owner("player1")
        p2_units = game_state.get_units_by_owner("player2")
        
        self.log(f"  Player 1: {len(p1_units)} units remaining")
        self.log(f"  Player 2: {len(p2_units)} units remaining")
        
        # In a more complete implementation, we'd track which disruptions
        # were applied this turn vs last turn and clear appropriately
    
    def _game_result(self, winner: Optional[str], game_state: GameState,
                     turn_history: List, reason: str) -> dict:
        """Create game result dictionary"""
        p1_units = game_state.get_units_by_owner("player1")
        p2_units = game_state.get_units_by_owner("player2")
        
        # If no winner by elimination, determine by remaining units
        if winner is None:
            if len(p1_units) > len(p2_units):
                winner = "player1"
            elif len(p2_units) > len(p1_units):
                winner = "player2"
            else:
                winner = "draw"
        
        self.log(f"\n{'='*70}")
        self.log(f"GAME OVER")
        self.log(f"{'='*70}")
        self.log(f"Winner: {winner}")
        self.log(f"Reason: {reason}")
        self.log(f"Turns played: {game_state.turn_number}")
        self.log(f"Player 1 remaining: {len(p1_units)} units")
        self.log(f"Player 2 remaining: {len(p2_units)} units")
        
        return {
            'winner': winner,
            'reason': reason,
            'turns': game_state.turn_number,
            'p1_remaining': len(p1_units),
            'p2_remaining': len(p2_units),
            'history': turn_history
        }


def create_test_armies(units_per_side: int = 3) -> Tuple[List[UnitState], List[UnitState]]:
    """
    Create two armies for testing.
    
    Returns (player1_units, player2_units)
    """
    import os
    import csv
    
    # Load units
    unit_file = 'Axis_and_Allies_Unit_Data_for_Analysis_-_Unit_Stats.csv'
    if not os.path.exists(unit_file):
        unit_file = 'Axis and Allies Unit Data for Analysis - Unit_Stats.csv'
    
    all_units = []
    with open(unit_file, 'r') as file:
        reader = csv.DictReader(file)
        for row in reader:
            unit = Unit(
                name=row['Unit Name'], nation=row['Nation'], unit_type=row['Type'],
                year=row['Year'], cost=row['Cost'], defense=row['Def'],
                speed=row['Speed'], veh_short=row['Veh S'], veh_medium=row['Veh M'],
                veh_long=row['Veh L'], per_short=row['Per S'], per_medium=row['Per M'],
                per_long=row['Per L'], abilities=row['Abilities']
            )
            all_units.append(unit)
    
    # Get soldiers that can fight
    soldiers = [u for u in all_units 
                if u.unit_type == 'Soldier' and u.per_short > 0 and u.speed and u.speed != 'A']
    
    # Get some vehicles
    vehicles = [u for u in all_units
                if u.unit_type == 'Vehicle' and u.defense_front]
    
    # Axis nations
    axis = ['Germany', 'Japan', 'Italy']
    allied = ['USA', 'UK', 'Soviet Union']
    
    axis_soldiers = [u for u in soldiers if u.nation in axis]
    allied_soldiers = [u for u in soldiers if u.nation in allied]
    
    # Create player 1 (Axis) units - deploy on left-center
    p1_units = []
    for i, unit in enumerate(axis_soldiers[:units_per_side]):
        u = deepcopy(unit)
        u.id = f"p1_unit_{i}"
        # Deploy closer to center - about 3 hexes from enemy
        pos = (5, 5 + i * 2)
        defense = getattr(u, 'defense_front', 3)
        p1_units.append(UnitState(u, pos, "player1", defense))
    
    # Create player 2 (Allied) units - deploy on right-center
    p2_units = []
    for i, unit in enumerate(allied_soldiers[:units_per_side]):
        u = deepcopy(unit)
        u.id = f"p2_unit_{i}"
        # Deploy closer to center - about 3 hexes from enemy
        pos = (8, 5 + i * 2)
        defense = getattr(u, 'defense_front', 3)
        p2_units.append(UnitState(u, pos, "player2", defense))
    
    return p1_units, p2_units


def run_test_game(verbose: bool = True):
    """Run a single test game"""
    # Create board
    board = Board(15, 15)
    
    # Add some terrain
    for q in range(6, 9):
        for r in range(5, 8):
            board.set_terrain(q, r, 'forest')
    
    # Create armies
    p1_units, p2_units = create_test_armies(units_per_side=3)
    
    # Create game state
    game_state = GameState(board, p1_units, p2_units)
    game_state.current_phase = GamePhase.MOVEMENT
    
    # Create agents
    agent1 = AggressiveRandomAgent("Axis Commander")
    agent2 = AggressiveRandomAgent("Allied Commander")
    
    # Run game
    runner = GameRunner(verbose=verbose)
    result = runner.run_game(game_state, agent1, agent2, max_turns=15)
    
    return result


def run_multiple_games(num_games: int = 10, verbose: bool = False):
    """Run multiple games and report statistics"""
    print(f"\nRunning {num_games} games...\n")
    
    results = {
        'player1': 0,
        'player2': 0,
        'draw': 0,
        'total_turns': 0,
        'eliminations': 0
    }
    
    for i in range(num_games):
        result = run_test_game(verbose=verbose)
        
        winner = result['winner']
        if winner == 'player1':
            results['player1'] += 1
        elif winner == 'player2':
            results['player2'] += 1
        else:
            results['draw'] += 1
        
        results['total_turns'] += result['turns']
        
        if result['reason'] == 'elimination':
            results['eliminations'] += 1
        
        if not verbose:
            print(f"Game {i+1}: {winner} wins ({result['reason']}) in {result['turns']} turns")
    
    print(f"\n{'='*50}")
    print(f"RESULTS AFTER {num_games} GAMES")
    print(f"{'='*50}")
    print(f"Player 1 (Axis) wins: {results['player1']} ({100*results['player1']/num_games:.1f}%)")
    print(f"Player 2 (Allied) wins: {results['player2']} ({100*results['player2']/num_games:.1f}%)")
    print(f"Draws: {results['draw']} ({100*results['draw']/num_games:.1f}%)")
    print(f"Average turns: {results['total_turns']/num_games:.1f}")
    print(f"Eliminations: {results['eliminations']} ({100*results['eliminations']/num_games:.1f}%)")
    
    return results


if __name__ == "__main__":
    print("=" * 70)
    print("AXIS & ALLIES MINIATURES - GAME RUNNER")
    print("=" * 70)
    
    # Run a single verbose game
    print("\n>>> Running single test game (verbose)...\n")
    result = run_test_game(verbose=True)
    
    # Run multiple games for statistics
    print("\n\n>>> Running batch of games for statistics...\n")
    run_multiple_games(num_games=10, verbose=False)