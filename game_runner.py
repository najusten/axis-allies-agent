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
from evaluation import GameStateEvaluator
from initiative import InitiativeSystem
from game_setup import (
    GameSetup, GameSetupConfig, quick_setup_broad, quick_setup_theater,
    quick_setup_custom, list_theaters, THEATERS
)
from mcts import MCTSAgent

# Optional visualization import
try:
    from game_visualizer import GameVisualizer, LiveGameVisualizer
    VISUALIZATION_AVAILABLE = True
except ImportError:
    VISUALIZATION_AVAILABLE = False


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


class GreedyAgent(Agent):
    """
    Agent that evaluates each action and chooses the one with the highest score.

    Uses GameStateEvaluator to score resulting game states.
    When no executor is available, falls back to a heuristic ordering.
    """

    def __init__(self, name: str = "GreedyAgent",
                 action_executor: ActionExecutor = None,
                 evaluator: GameStateEvaluator = None,
                 tie_break: str = 'random'):
        """
        Initialize the greedy agent.

        Args:
            name: Agent name for display
            action_executor: ActionExecutor for simulating actions (can be set later)
            evaluator: GameStateEvaluator (creates one if None)
            tie_break: How to break ties ('random', 'attack_priority', 'first')
        """
        super().__init__(name)
        self.action_executor = action_executor
        self.evaluator = evaluator or GameStateEvaluator()
        self.tie_break = tie_break

    def set_action_executor(self, executor: ActionExecutor):
        """Set the action executor (called by GameRunner)."""
        self.action_executor = executor

    def choose_action(self, game_state: GameState,
                      legal_actions: List[Action],
                      player: str) -> Action:
        """
        Choose the action with the highest evaluated score.

        Args:
            game_state: Current game state
            legal_actions: List of legal actions to choose from
            player: The player making the decision

        Returns:
            The best action according to evaluation
        """
        # Handle edge cases
        if not legal_actions:
            return PassAction(player)

        if len(legal_actions) == 1:
            return legal_actions[0]

        # If we have an executor, use full evaluation
        if self.action_executor:
            return self._choose_with_evaluation(game_state, legal_actions, player)
        else:
            return self._choose_with_heuristic(game_state, legal_actions, player)

    def _choose_with_evaluation(self, game_state: GameState,
                                legal_actions: List[Action],
                                player: str) -> Action:
        """Choose action using full state evaluation."""
        scored_actions = []

        for action in legal_actions:
            score = self.evaluator.evaluate_action(
                game_state, action, player, self.action_executor
            )
            scored_actions.append((action, score))

        # Sort by score (descending)
        scored_actions.sort(key=lambda x: x[1], reverse=True)

        # Find all actions with the best score
        best_score = scored_actions[0][1]
        best_actions = [a for a, s in scored_actions if s == best_score]

        # Break ties
        return self._break_tie(best_actions)

    def _choose_with_heuristic(self, game_state: GameState,
                               legal_actions: List[Action],
                               player: str) -> Action:
        """
        Choose action using heuristic ordering when no executor is available.

        Priority:
        1. Attack disrupted/damaged enemies (prefer closer)
        2. Move toward cover and enemies (but stay 2+ hexes away)
        3. Pass
        """
        attacks = [a for a in legal_actions if isinstance(a, AttackAction)]
        moves = [a for a in legal_actions if isinstance(a, MoveAction)]
        passes = [a for a in legal_actions if isinstance(a, PassAction)]

        # Prioritize attacks on disrupted/damaged targets
        if attacks:
            scored_attacks = []
            for attack in attacks:
                target_state = game_state.get_unit_state(attack.target_id)
                score = 0

                if target_state:
                    # Prefer disrupted targets
                    if target_state.is_disrupted:
                        score += 20
                    # Prefer damaged targets
                    if target_state.is_damaged:
                        score += 15
                    # Prefer closer targets
                    score -= attack.distance

                scored_attacks.append((attack, score))

            scored_attacks.sort(key=lambda x: x[1], reverse=True)
            best_score = scored_attacks[0][1]
            best_attacks = [a for a, s in scored_attacks if s == best_score]
            return self._break_tie(best_attacks)

        # If no attacks, consider moves
        if moves:
            enemy_player = "player2" if player == "player1" else "player1"
            enemy_units = game_state.get_units_by_owner(enemy_player)

            if enemy_units:
                scored_moves = []
                for move in moves:
                    score = 0

                    # Check terrain at destination
                    dest_hex = game_state.board.get_hex(move.to_q, move.to_r)
                    if dest_hex and dest_hex.terrain in ['forest', 'building', 'hill', 'town']:
                        score += 10  # Cover bonus

                    # Calculate distance to nearest enemy
                    min_enemy_dist = float('inf')
                    for enemy_state in enemy_units:
                        dist = game_state.board.hex_distance(
                            move.to_q, move.to_r,
                            enemy_state.position[0], enemy_state.position[1]
                        )
                        min_enemy_dist = min(min_enemy_dist, dist)

                    # Prefer medium range (2-4 hexes)
                    if 2 <= min_enemy_dist <= 4:
                        score += 5
                    elif min_enemy_dist < 2:
                        score -= 3  # Too close
                    elif min_enemy_dist > 6:
                        score -= 2  # Too far

                    scored_moves.append((move, score))

                scored_moves.sort(key=lambda x: x[1], reverse=True)
                best_score = scored_moves[0][1]
                best_moves = [m for m, s in scored_moves if s == best_score]
                return self._break_tie(best_moves)
            else:
                return random.choice(moves)

        # Otherwise pass
        if passes:
            return passes[0]

        return random.choice(legal_actions)

    def _break_tie(self, actions: List[Action]) -> Action:
        """Break tie between equally scored actions."""
        if len(actions) == 1:
            return actions[0]

        if self.tie_break == 'random':
            return random.choice(actions)
        elif self.tie_break == 'attack_priority':
            # Prefer attacks over moves over passes
            attacks = [a for a in actions if isinstance(a, AttackAction)]
            if attacks:
                return random.choice(attacks)
            moves = [a for a in actions if isinstance(a, MoveAction)]
            if moves:
                return random.choice(moves)
            return actions[0]
        else:  # 'first'
            return actions[0]


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
    
    def __init__(self, ability_file: str = None, verbose: bool = True,
                 visualize: bool = False, viz_output_path: str = "game_view.html"):
        """
        Initialize the game runner.

        Args:
            ability_file: Path to abilities CSV (auto-detected if None)
            verbose: If True, print game events
            visualize: If True, generate HTML visualization after each turn
            viz_output_path: Path for visualization output file
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
        self.initiative_system = InitiativeSystem(
            self.ability_system, self.movement_system
        )
        self.verbose = verbose

        # Visualization support
        self.visualize = visualize and VISUALIZATION_AVAILABLE
        self.viz_output_path = viz_output_path
        self._visualizer = None
        if self.visualize:
            self._visualizer = GameVisualizer()
    
    def log(self, message: str):
        """Print if verbose mode is on"""
        if self.verbose:
            print(message)

    def update_visualization(self, game_state: GameState, open_browser: bool = False):
        """
        Update the game visualization if enabled.

        Args:
            game_state: Current game state to visualize
            open_browser: If True, open the visualization in browser
        """
        if not self.visualize or not self._visualizer:
            return

        self._visualizer.generate_html(game_state, output_path=self.viz_output_path)

        if open_browser:
            import webbrowser
            import os
            webbrowser.open(f'file://{os.path.abspath(self.viz_output_path)}')
    
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

        # Wire up action executor for GreedyAgent instances
        if isinstance(agent1, GreedyAgent):
            agent1.set_action_executor(self.action_executor)
        if isinstance(agent2, GreedyAgent):
            agent2.set_action_executor(self.action_executor)

        # Wire up MCTSAgent instances
        evaluator = GameStateEvaluator()
        for agent in [agent1, agent2]:
            if isinstance(agent, MCTSAgent):
                agent.set_action_executor(self.action_executor)
                agent.set_action_generator(self.action_generator)
                agent.set_evaluator(evaluator)

        self.log("=" * 70)
        self.log("GAME START")
        self.log("=" * 70)
        self.log(f"Player 1: {agent1.name}")
        self.log(f"Player 2: {agent2.name}")
        self.log(f"Max turns: {max_turns}")
        self.log("")

        turn_history = []

        # === VANGUARD PHASE (pre-game movement) ===
        # After deployment, units with Vanguard can move at speed 4 before turn 1
        vanguard_events = self._run_vanguard_phase(game_state, agents)
        if vanguard_events:
            turn_history.append({'phase': 'vanguard', 'events': vanguard_events})

        while game_state.turn_number <= max_turns:
            turn_events = []

            self.log(f"\n{'='*60}")
            self.log(f"TURN {game_state.turn_number}")
            self.log(f"{'='*60}")

            # === INITIATIVE PHASE ===
            first_player, p1_init, p2_init = self.initiative_system.determine_first_player(game_state)
            second_player = "player2" if first_player == "player1" else "player1"
            turn_order = [first_player, second_player]

            self.log(f"\n--- Initiative Phase ---")
            self.log(f"  {p1_init}")
            self.log(f"  {p2_init}")
            self.log(f"  → {first_player} goes first")

            # === MOVEMENT PHASE ===
            for player in turn_order:
                game_state.current_phase = GamePhase.MOVEMENT
                game_state.active_player = player
                agent = agents[player]

                # Reset defensive fire tracking for this phase
                self.action_executor.reset_defensive_fire_phase()

                # Exert Will: At beginning of movement phase, remove Disrupted from adjacent friendly Soldiers
                self._apply_exert_will(game_state, player)

                self.log(f"\n--- {player} Movement Phase ---")

                phase_actions = self._run_phase(
                    game_state, agent, player, max_actions=10
                )
                turn_events.extend(phase_actions)

                # Hard Charger: At end of movement phase, remove Disrupted from units with this ability
                self._apply_hard_charger(game_state, player)

                # Check for victory after each phase
                winner = game_state.check_victory_conditions()
                if winner:
                    return self._game_result(winner, game_state, turn_history, "elimination")
            
            # === FLIGHT PHASE (Aircraft placement) ===
            for player in turn_order:
                game_state.current_phase = GamePhase.FLIGHT
                game_state.active_player = player
                agent = agents[player]

                # Check if player has any Aircraft to place
                player_units = game_state.get_units_by_owner(player)
                has_aircraft = any(
                    u.unit.unit_type == 'Aircraft' and not u.is_aircraft_on_map
                    for u in player_units if u.is_alive
                )

                if has_aircraft:
                    self.log(f"\n--- {player} Flight Phase ---")
                    phase_actions = self._run_phase(
                        game_state, agent, player, max_actions=5
                    )
                    turn_events.extend(phase_actions)

            # === ASSAULT PHASE (ground units) ===
            for player in turn_order:
                game_state.current_phase = GamePhase.ASSAULT
                game_state.active_player = player
                agent = agents[player]

                # Reset casualty tracking for this player's assault phase
                self.action_executor.reset_assault_phase()

                self.log(f"\n--- {player} Assault Phase ---")

                phase_actions = self._run_phase(
                    game_state, agent, player, max_actions=20
                )
                turn_events.extend(phase_actions)

            # === AIRSTRIKE PHASE (Aircraft attacks) ===
            for player in turn_order:
                game_state.current_phase = GamePhase.AIRSTRIKE
                game_state.active_player = player
                agent = agents[player]

                # Check if player has any Aircraft on map
                player_units = game_state.get_units_by_owner(player)
                has_aircraft_on_map = any(
                    u.unit.unit_type == 'Aircraft' and u.is_aircraft_on_map
                    for u in player_units if u.is_alive
                )

                if has_aircraft_on_map:
                    self.log(f"\n--- {player} Airstrike Phase ---")
                    phase_actions = self._run_phase(
                        game_state, agent, player, max_actions=10
                    )
                    turn_events.extend(phase_actions)

            # === CASUALTY PHASE ===
            self.log(f"\n--- Casualty Phase ---")
            casualty_results = self.action_executor.resolve_casualty_phase(game_state)
            
            # Log casualty phase results
            if casualty_results['units_destroyed']:
                for unit_id in casualty_results['units_destroyed']:
                    self.log(f"  💀 {unit_id} destroyed")
            if casualty_results['units_damaged']:
                for unit_id in casualty_results['units_damaged']:
                    self.log(f"  🔧 {unit_id} damaged")
            if casualty_results['units_disrupted']:
                for unit_id in casualty_results['units_disrupted']:
                    self.log(f"  ⚡ {unit_id} disrupted")
            if casualty_results['disruption_cleared']:
                for unit_id in casualty_results['disruption_cleared']:
                    self.log(f"  ✓ {unit_id} disruption cleared")
            
            if not any([casualty_results['units_destroyed'], 
                       casualty_results['units_damaged'],
                       casualty_results['units_disrupted']]):
                self.log(f"  (no casualties)")
            
            # Check for victory after casualty phase
            winner = game_state.check_victory_conditions()
            if winner:
                return self._game_result(winner, game_state, turn_history, "elimination")

            # === OBJECTIVE CONTROL CHECK (Turn 7+) ===
            if game_state.turn_number >= 7:
                obj_controller = game_state.check_objective_control()
                if obj_controller:
                    obj_q, obj_r = game_state.objective_position
                    self.log(f"\n*** {obj_controller} CONTROLS OBJECTIVE at ({obj_q},{obj_r})! ***")
                    return self._game_result(obj_controller, game_state, turn_history, "objective")

            # === END OF TURN ===
            self._end_of_turn(game_state)
            turn_history.append(turn_events)

            # Update visualization if enabled
            self.update_visualization(game_state, open_browser=(game_state.turn_number == 1))

            # Increment turn
            game_state.turn_number += 1

            # Reset unit flags for next turn
            for unit_state in game_state.units.values():
                unit_state.has_moved = False
                unit_state.has_attacked = False
                unit_state.abilities_used.clear()

        # Turn 10 tiebreaker: winner by points
        p1_points = game_state.get_total_points("player1")
        p2_points = game_state.get_total_points("player2")

        if p1_points > p2_points:
            return self._game_result("player1", game_state, turn_history, "points")
        elif p2_points > p1_points:
            return self._game_result("player2", game_state, turn_history, "points")
        else:
            return self._game_result(None, game_state, turn_history, "draw")

    def _run_vanguard_phase(self, game_state: GameState, agents: dict) -> List[dict]:
        """
        Vanguard Phase: After deployment, units with Vanguard can move at speed 4
        before the first turn begins.

        Returns list of vanguard movement events.
        """
        events = []

        # Check if any units have Vanguard ability
        has_vanguard_units = False
        for player in ["player1", "player2"]:
            for unit_state in game_state.get_units_by_owner(player):
                if not unit_state.is_alive:
                    continue
                abilities = getattr(unit_state.unit, 'abilities', []) or []
                if any(a.lower() == 'vanguard' for a in abilities):
                    has_vanguard_units = True
                    break
            if has_vanguard_units:
                break

        if not has_vanguard_units:
            return events

        self.log(f"\n{'='*60}")
        self.log("VANGUARD PHASE (Pre-Game Movement)")
        self.log(f"{'='*60}")

        # Both players move their Vanguard units (player 1 first)
        for player in ["player1", "player2"]:
            player_units = game_state.get_units_by_owner(player)
            vanguard_units = []

            for unit_state in player_units:
                if not unit_state.is_alive:
                    continue
                abilities = getattr(unit_state.unit, 'abilities', []) or []
                if any(a.lower() == 'vanguard' for a in abilities):
                    vanguard_units.append(unit_state)

            if not vanguard_units:
                continue

            self.log(f"\n--- {player} Vanguard Movement ---")

            for unit_state in vanguard_units:
                unit = unit_state.unit
                q, r = unit_state.position

                # Generate possible moves at speed 4
                reachable = self.movement_system.get_reachable_hexes(
                    game_state.board, q, r, unit, max_speed=4
                )

                if not reachable or len(reachable) <= 1:
                    continue

                # Let agent choose the move
                agent = agents[player]
                move_actions = []
                for (dest_q, dest_r) in reachable:
                    if (dest_q, dest_r) != (q, r):
                        move_action = MoveAction(
                            unit_id=unit.id,
                            from_q=q, from_r=r,
                            to_q=dest_q, to_r=dest_r
                        )
                        move_actions.append(move_action)

                if not move_actions:
                    continue

                # Add pass option (don't move)
                move_actions.append(PassAction(unit_id=unit.id))

                # Agent chooses action
                game_state.current_phase = GamePhase.MOVEMENT
                game_state.active_player = player
                chosen_action = agent.choose_action(game_state, move_actions, player)

                if isinstance(chosen_action, MoveAction):
                    # Execute the move
                    result = self.action_executor.execute(game_state, chosen_action)
                    if result.success:
                        self.log(f"  {unit.name} moves from ({q},{r}) to ({chosen_action.to_q},{chosen_action.to_r})")
                        events.append({
                            'player': player,
                            'unit': unit.name,
                            'action': 'vanguard_move',
                            'from': (q, r),
                            'to': (chosen_action.to_q, chosen_action.to_r)
                        })
                        # Reset has_moved flag so unit can move again in turn 1
                        unit_state.has_moved = False
                else:
                    self.log(f"  {unit.name} stays at ({q},{r})")

        return events

    def _apply_exert_will(self, game_state: GameState, player: str):
        """
        Exert Will: At the beginning of movement phase, remove all Disrupted
        counters from friendly Soldiers adjacent to units with this ability.
        """
        player_units = game_state.get_units_by_owner(player)

        for unit_state in player_units:
            if not unit_state.is_alive:
                continue

            unit = unit_state.unit
            abilities = getattr(unit, 'abilities', []) or []
            has_exert_will = any(a.lower() == 'exert will' for a in abilities)

            if not has_exert_will:
                continue

            # Find adjacent friendly Soldiers and remove their Disrupted counters
            unit_pos = unit_state.position
            for friendly_state in player_units:
                if not friendly_state.is_alive:
                    continue
                if friendly_state.unit.id == unit.id:
                    continue
                if friendly_state.unit.unit_type != 'Soldier':
                    continue
                if not friendly_state.is_disrupted:
                    continue

                # Check if adjacent
                friendly_pos = friendly_state.position
                dist = game_state.board.hex_distance(
                    unit_pos[0], unit_pos[1], friendly_pos[0], friendly_pos[1]
                )
                if dist <= 1:
                    friendly_state.is_disrupted = False
                    self.log(f"  Exert Will: {friendly_state.unit.name} Disrupted removed by {unit.name}")

    def _apply_hard_charger(self, game_state: GameState, player: str):
        """
        Hard Charger: At the end of movement phase, remove any face-up
        Disrupted counters from units with this ability.
        """
        player_units = game_state.get_units_by_owner(player)

        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            if not unit_state.is_disrupted:
                continue

            unit = unit_state.unit
            abilities = getattr(unit, 'abilities', []) or []
            has_hard_charger = any(a.lower() == 'hard charger' for a in abilities)

            if has_hard_charger:
                unit_state.is_disrupted = False
                self.log(f"  Hard Charger: {unit.name} Disrupted removed")

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

        # Report objective status
        obj_q, obj_r = game_state.objective_position
        obj_controller = game_state.check_objective_control()
        p1_adj = len(game_state.get_units_adjacent_to_objective("player1"))
        p2_adj = len(game_state.get_units_adjacent_to_objective("player2"))

        if obj_controller:
            self.log(f"  Objective ({obj_q},{obj_r}): CONTROLLED by {obj_controller}")
        else:
            status = "CONTESTED" if (p1_adj > 0 and p2_adj > 0) else "UNCONTROLLED"
            self.log(f"  Objective ({obj_q},{obj_r}): {status} (P1:{p1_adj}, P2:{p2_adj} adjacent)")

        if game_state.turn_number >= 6:
            turns_until = 7 - game_state.turn_number
            if turns_until > 0:
                self.log(f"  *** Objective victory check in {turns_until} turn(s)! ***")
    
    def _game_result(self, winner: Optional[str], game_state: GameState,
                     turn_history: List, reason: str) -> dict:
        """Create game result dictionary"""
        p1_units = game_state.get_units_by_owner("player1")
        p2_units = game_state.get_units_by_owner("player2")
        p1_points = game_state.get_total_points("player1")
        p2_points = game_state.get_total_points("player2")

        # If no winner specified, it's a draw
        if winner is None:
            winner = "draw"

        self.log(f"\n{'='*70}")
        self.log(f"GAME OVER")
        self.log(f"{'='*70}")
        self.log(f"Winner: {winner}")
        self.log(f"Reason: {reason}")
        self.log(f"Turns played: {game_state.turn_number}")
        self.log(f"Player 1 remaining: {len(p1_units)} units ({p1_points:.0f} pts)")
        self.log(f"Player 2 remaining: {len(p2_units)} units ({p2_points:.0f} pts)")

        return {
            'winner': winner,
            'reason': reason,
            'turns': game_state.turn_number,
            'p1_remaining': len(p1_units),
            'p2_remaining': len(p2_units),
            'p1_points': p1_points,
            'p2_points': p2_points,
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
    
    # Get some vehicles that have both front and rear defense
    vehicles = [u for u in all_units
                if u.unit_type == 'Vehicle' and u.defense_front and u.defense_rear 
                and u.speed and u.speed != 'A']
    
    # Axis nations
    axis = ['Germany', 'Japan', 'Italy']
    allied = ['USA', 'UK', 'Soviet Union']
    
    axis_soldiers = [u for u in soldiers if u.nation in axis]
    allied_soldiers = [u for u in soldiers if u.nation in allied]
    axis_vehicles = [u for u in vehicles if u.nation in axis]
    allied_vehicles = [u for u in vehicles if u.nation in allied]
    
    # Create player 1 (Axis) units - 2 soldiers + 1 vehicle
    p1_units = []
    
    # Add soldiers
    for i, unit in enumerate(axis_soldiers[:2]):
        u = deepcopy(unit)
        u.id = f"p1_soldier_{i}"
        pos = (5, 5 + i * 2)
        defense = getattr(u, 'defense_front', 3)
        p1_units.append(UnitState(u, pos, "player1", defense))
    
    # Add a vehicle if available
    if axis_vehicles:
        tank = deepcopy(axis_vehicles[0])
        tank.id = "p1_tank_0"
        pos = (5, 9)
        defense = tank.defense_front
        tank_state = UnitState(tank, pos, "player1", defense)
        tank_state.facing = 0  # Facing East initially
        p1_units.append(tank_state)
    
    # Create player 2 (Allied) units - 2 soldiers + 1 vehicle
    p2_units = []
    
    # Add soldiers
    for i, unit in enumerate(allied_soldiers[:2]):
        u = deepcopy(unit)
        u.id = f"p2_soldier_{i}"
        pos = (8, 5 + i * 2)
        defense = getattr(u, 'defense_front', 3)
        p2_units.append(UnitState(u, pos, "player2", defense))
    
    # Add a vehicle if available
    if allied_vehicles:
        tank = deepcopy(allied_vehicles[0])
        tank.id = "p2_tank_0"
        pos = (8, 9)
        defense = tank.defense_front
        tank_state = UnitState(tank, pos, "player2", defense)
        tank_state.facing = 3  # Facing West initially (toward enemy)
        p2_units.append(tank_state)
    
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


def run_test_game_greedy(verbose: bool = True):
    """Run a single test game with GreedyAgent vs RandomAgent"""
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

    # Create agents - GreedyAgent vs RandomAgent
    agent1 = GreedyAgent("Greedy Commander")
    agent2 = RandomAgent("Random Commander")

    # Run game
    runner = GameRunner(verbose=verbose)
    result = runner.run_game(game_state, agent1, agent2, max_turns=15)

    return result


def run_greedy_vs_random(num_games: int = 10, verbose: bool = False):
    """Run multiple games: GreedyAgent vs RandomAgent with objective victory"""
    import csv
    import os

    print(f"\nRunning {num_games} games: GreedyAgent vs RandomAgent (with objective)...\n")

    # Load combat-capable soldiers
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

    soldiers = [u for u in all_units if u.unit_type == 'Soldier' and u.per_short > 0]

    results = {
        'greedy_wins': 0,
        'random_wins': 0,
        'draw': 0,
        'total_turns': 0,
        'by_reason': {}
    }

    for i in range(num_games):
        # Create board with objective at center (7, 7)
        board = Board(15, 15)

        # Add cover terrain around but not on objective
        for q, r in [(6, 6), (6, 7), (6, 8), (8, 6), (8, 7), (8, 8)]:
            board.set_terrain(q, r, 'forest')

        # Symmetric starting positions - both equidistant from objective at (7,7)
        # P1 starts west, P2 starts east
        inf1 = deepcopy(soldiers[0]); inf1.id = 'p1_inf1'
        inf2 = deepcopy(soldiers[1]); inf2.id = 'p1_inf2'
        inf3 = deepcopy(soldiers[2]); inf3.id = 'p2_inf1'
        inf4 = deepcopy(soldiers[3]); inf4.id = 'p2_inf2'

        p1_units = [
            UnitState(inf1, (3, 6), 'player1', inf1.defense_front),
            UnitState(inf2, (3, 8), 'player1', inf2.defense_front),
        ]
        p2_units = [
            UnitState(inf3, (11, 6), 'player2', inf3.defense_front),
            UnitState(inf4, (11, 8), 'player2', inf4.defense_front),
        ]

        # Objective at center
        objective_pos = (7, 7)
        game_state = GameState(board, p1_units, p2_units, objective_position=objective_pos)
        game_state.current_phase = GamePhase.MOVEMENT

        # Alternate who plays which side
        if i % 2 == 0:
            agent1 = GreedyAgent("Greedy")
            agent2 = RandomAgent("Random")
            greedy_player = "player1"
        else:
            agent1 = RandomAgent("Random")
            agent2 = GreedyAgent("Greedy")
            greedy_player = "player2"

        runner = GameRunner(verbose=verbose)
        result = runner.run_game(game_state, agent1, agent2, max_turns=10)

        winner = result['winner']
        reason = result['reason']

        if winner == greedy_player:
            results['greedy_wins'] += 1
        elif winner == 'draw':
            results['draw'] += 1
        else:
            results['random_wins'] += 1

        results['total_turns'] += result['turns']
        results['by_reason'][reason] = results['by_reason'].get(reason, 0) + 1

        if not verbose:
            greedy_side = "P1" if greedy_player == "player1" else "P2"
            greedy_won = "GREEDY" if winner == greedy_player else ("RANDOM" if winner != 'draw' else "DRAW")
            print(f"Game {i+1}: Greedy={greedy_side}, Winner={greedy_won} ({reason}) turn {result['turns']}")

    print(f"\n{'='*50}")
    print(f"RESULTS AFTER {num_games} GAMES (with objective)")
    print(f"{'='*50}")
    print(f"GreedyAgent wins: {results['greedy_wins']} ({100*results['greedy_wins']/num_games:.1f}%)")
    print(f"RandomAgent wins: {results['random_wins']} ({100*results['random_wins']/num_games:.1f}%)")
    print(f"Draws: {results['draw']} ({100*results['draw']/num_games:.1f}%)")
    print(f"Average turns: {results['total_turns']/num_games:.1f}")
    print(f"Victory reasons: {results['by_reason']}")

    return results


def run_theater_game(theater: str,
                     agent1: Agent = None,
                     agent2: Agent = None,
                     points: int = 100,
                     verbose: bool = True) -> dict:
    """
    Run a single game in a historical theater.

    Args:
        theater: Theater name (e.g., 'western_europe', 'pacific', 'eastern_front')
        agent1: Agent for player 1 (Allies), defaults to GreedyAgent
        agent2: Agent for player 2 (Axis), defaults to RandomAgent
        points: Points per side
        verbose: Print game events

    Returns:
        Game result dictionary
    """
    setup = quick_setup_theater(theater, points=points)

    if verbose:
        print(f"\n{'='*60}")
        print("GAME SETUP")
        print(f"{'='*60}")
        print(setup.describe())

    game_state = setup.create_game(build_method='balanced')
    game_state.current_phase = GamePhase.MOVEMENT

    if verbose:
        print(f"\nPlayer 1 Army (Allies):")
        for u in game_state.get_units_by_owner('player1'):
            print(f"  {u.unit.name} ({u.unit.nation}, {u.unit.year}) - {u.unit.cost}pts")

        print(f"\nPlayer 2 Army (Axis):")
        for u in game_state.get_units_by_owner('player2'):
            print(f"  {u.unit.name} ({u.unit.nation}, {u.unit.year}) - {u.unit.cost}pts")

    agent1 = agent1 or GreedyAgent("Allied Commander")
    agent2 = agent2 or RandomAgent("Axis Commander")

    runner = GameRunner(verbose=verbose)
    return runner.run_game(game_state, agent1, agent2, max_turns=10)


def run_theater_comparison(theater: str,
                           num_games: int = 20,
                           points: int = 100,
                           verbose: bool = False) -> dict:
    """
    Run multiple games in a theater comparing GreedyAgent vs RandomAgent.

    Alternates which side each agent plays for fairness.
    """
    theater_config = THEATERS[theater]
    print(f"\n{'='*60}")
    print(f"THEATER: {theater_config.name}")
    print(f"{'='*60}")
    print(f"{theater_config.description}")
    print(f"Allied: {', '.join(sorted(theater_config.allied_nations))}")
    print(f"Axis: {', '.join(sorted(theater_config.axis_nations))}")
    print(f"Years: {theater_config.year_range[0]}-{theater_config.year_range[1]}")
    print(f"\nRunning {num_games} games ({points} pts/side)...\n")

    results = {
        'greedy_wins': 0,
        'random_wins': 0,
        'draw': 0,
        'total_turns': 0,
        'by_reason': {}
    }

    for i in range(num_games):
        setup = quick_setup_theater(theater, points=points)
        game_state = setup.create_game(build_method='balanced')
        game_state.current_phase = GamePhase.MOVEMENT

        # Alternate sides
        if i % 2 == 0:
            agent1 = GreedyAgent("Greedy")
            agent2 = RandomAgent("Random")
            greedy_player = "player1"
        else:
            agent1 = RandomAgent("Random")
            agent2 = GreedyAgent("Greedy")
            greedy_player = "player2"

        runner = GameRunner(verbose=verbose)
        result = runner.run_game(game_state, agent1, agent2, max_turns=10)

        winner = result['winner']
        reason = result['reason']

        if winner == greedy_player:
            results['greedy_wins'] += 1
        elif winner == 'draw':
            results['draw'] += 1
        else:
            results['random_wins'] += 1

        results['total_turns'] += result['turns']
        results['by_reason'][reason] = results['by_reason'].get(reason, 0) + 1

        greedy_side = "Allies" if greedy_player == "player1" else "Axis"
        greedy_won = "GREEDY" if winner == greedy_player else ("RANDOM" if winner != 'draw' else "DRAW")
        print(f"Game {i+1}: Greedy={greedy_side}, Winner={greedy_won} ({reason}) turn {result['turns']}")

    print(f"\n{'='*50}")
    print(f"RESULTS: {theater_config.name}")
    print(f"{'='*50}")
    print(f"GreedyAgent wins: {results['greedy_wins']} ({100*results['greedy_wins']/num_games:.1f}%)")
    print(f"RandomAgent wins: {results['random_wins']} ({100*results['random_wins']/num_games:.1f}%)")
    print(f"Draws: {results['draw']} ({100*results['draw']/num_games:.1f}%)")
    print(f"Average turns: {results['total_turns']/num_games:.1f}")
    print(f"Victory reasons: {results['by_reason']}")

    return results


def run_all_theaters_test(num_games_per_theater: int = 10,
                          points: int = 75) -> dict:
    """Run comparison games across all theaters."""
    print("=" * 70)
    print("ALL THEATERS TEST")
    print("=" * 70)

    all_results = {}
    for theater in THEATERS.keys():
        try:
            results = run_theater_comparison(
                theater, num_games=num_games_per_theater, points=points
            )
            all_results[theater] = results
        except Exception as e:
            print(f"Error in {theater}: {e}")
            all_results[theater] = {'error': str(e)}

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY ACROSS ALL THEATERS")
    print("=" * 70)
    total_greedy = sum(r.get('greedy_wins', 0) for r in all_results.values() if 'error' not in r)
    total_random = sum(r.get('random_wins', 0) for r in all_results.values() if 'error' not in r)
    total_games = total_greedy + total_random + sum(r.get('draw', 0) for r in all_results.values() if 'error' not in r)

    if total_games > 0:
        print(f"Total GreedyAgent wins: {total_greedy} ({100*total_greedy/total_games:.1f}%)")
        print(f"Total RandomAgent wins: {total_random} ({100*total_random/total_games:.1f}%)")

    return all_results


def run_mcts_vs_greedy(num_games: int = 10,
                       points: int = 75,
                       mcts_simulations: int = 300,
                       verbose: bool = False) -> dict:
    """
    Run MCTS vs Greedy comparison.

    Args:
        num_games: Number of games to run
        points: Points per side
        mcts_simulations: MCTS simulations per decision
        verbose: Print game details
    """
    print(f"\n{'='*60}")
    print(f"MCTS vs GREEDY COMPARISON")
    print(f"{'='*60}")
    print(f"MCTS simulations per move: {mcts_simulations}")
    print(f"Points per side: {points}")
    print(f"Running {num_games} games...\n")

    results = {
        'mcts_wins': 0,
        'greedy_wins': 0,
        'draw': 0,
        'total_turns': 0,
        'by_reason': {}
    }

    import time
    total_time = 0

    for i in range(num_games):
        setup = quick_setup_theater('western_europe', points=points)
        game_state = setup.create_game(build_method='balanced')
        game_state.current_phase = GamePhase.MOVEMENT

        # Alternate sides
        if i % 2 == 0:
            agent1 = MCTSAgent("MCTS", num_simulations=mcts_simulations)
            agent2 = GreedyAgent("Greedy")
            mcts_player = "player1"
        else:
            agent1 = GreedyAgent("Greedy")
            agent2 = MCTSAgent("MCTS", num_simulations=mcts_simulations)
            mcts_player = "player2"

        start_time = time.time()
        runner = GameRunner(verbose=verbose)
        result = runner.run_game(game_state, agent1, agent2, max_turns=10)
        elapsed = time.time() - start_time
        total_time += elapsed

        winner = result['winner']
        reason = result['reason']

        if winner == mcts_player:
            results['mcts_wins'] += 1
            outcome = 'MCTS'
        elif winner == 'draw':
            results['draw'] += 1
            outcome = 'DRAW'
        else:
            results['greedy_wins'] += 1
            outcome = 'GREEDY'

        results['total_turns'] += result['turns']
        results['by_reason'][reason] = results['by_reason'].get(reason, 0) + 1

        mcts_side = "P1" if mcts_player == "player1" else "P2"
        print(f"Game {i+1}: MCTS={mcts_side}, Winner={outcome} ({reason}) "
              f"turn {result['turns']} [{elapsed:.1f}s]")

    print(f"\n{'='*50}")
    print(f"RESULTS: MCTS vs GREEDY")
    print(f"{'='*50}")
    print(f"MCTSAgent wins: {results['mcts_wins']} ({100*results['mcts_wins']/num_games:.1f}%)")
    print(f"GreedyAgent wins: {results['greedy_wins']} ({100*results['greedy_wins']/num_games:.1f}%)")
    print(f"Draws: {results['draw']} ({100*results['draw']/num_games:.1f}%)")
    print(f"Average turns: {results['total_turns']/num_games:.1f}")
    print(f"Total time: {total_time:.1f}s (avg {total_time/num_games:.1f}s/game)")
    print(f"Victory reasons: {results['by_reason']}")

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

    # Run GreedyAgent vs RandomAgent comparison
    print("\n\n>>> Running GreedyAgent vs RandomAgent comparison...\n")
    run_greedy_vs_random(num_games=20, verbose=False)