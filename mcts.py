"""
Monte Carlo Tree Search (MCTS) for Axis & Allies Miniatures

MCTS finds good moves by simulating many random games and tracking
which moves lead to wins most often.

Four phases repeated many times:
1. Select - Walk down tree picking promising branches (UCB1)
2. Expand - Add a new child node for an untried move
3. Simulate - Play randomly until game ends
4. Backpropagate - Update win/visit counts back up the tree
"""

import math
import random
import time
from typing import List, Optional, Dict, Tuple
from copy import deepcopy
from dataclasses import dataclass, field

from game_state import GameState, GamePhase
from action import Action, PassAction, MoveAction, AttackAction
from action_generator import ActionGenerator
from action_executor import ActionExecutor
from movement import MovementSystem
from abilities import AbilitySystem


@dataclass
class MCTSNode:
    """A node in the MCTS search tree."""
    state: GameState
    parent: Optional['MCTSNode'] = None
    action: Optional[Action] = None  # Action that led to this state
    player: str = "player1"  # Player who made the action to reach this node

    children: List['MCTSNode'] = field(default_factory=list)
    untried_actions: List[Action] = field(default_factory=list)

    wins: float = 0.0
    visits: int = 0

    def __post_init__(self):
        # Will be populated by MCTS when node is created
        pass

    @property
    def is_fully_expanded(self) -> bool:
        return len(self.untried_actions) == 0

    @property
    def is_terminal(self) -> bool:
        """Check if this is a game-ending state."""
        winner = self.state.check_victory_conditions()
        if winner:
            return True
        # Also check objective control for turn 7+
        if self.state.turn_number >= 7:
            if self.state.check_objective_control():
                return True
        return False

    def ucb1(self, exploration_weight: float = 1.414) -> float:
        """
        Upper Confidence Bound formula for node selection.

        UCB1 = wins/visits + C * sqrt(ln(parent_visits) / visits)

        Balances exploitation (high win rate) with exploration (less visited).
        """
        if self.visits == 0:
            return float('inf')  # Always try unvisited nodes

        exploitation = self.wins / self.visits
        exploration = exploration_weight * math.sqrt(
            math.log(self.parent.visits) / self.visits
        )
        return exploitation + exploration

    def best_child(self, exploration_weight: float = 1.414) -> 'MCTSNode':
        """Select child with highest UCB1 score."""
        return max(self.children, key=lambda c: c.ucb1(exploration_weight))

    def best_action(self) -> Action:
        """Return the most visited child's action (best move found)."""
        if not self.children:
            return PassAction(self.player)
        # Pick the most visited child (most robust choice)
        best = max(self.children, key=lambda c: c.visits)
        return best.action


class MCTS:
    """
    Monte Carlo Tree Search implementation.

    Usage:
        mcts = MCTS(action_generator, action_executor)
        best_action = mcts.search(game_state, player, num_simulations=1000)
    """

    def __init__(self,
                 action_generator: ActionGenerator,
                 action_executor: ActionExecutor,
                 exploration_weight: float = 1.414,
                 max_rollout_depth: int = 50,
                 use_evaluation_cutoff: bool = True,
                 evaluator=None,
                 rollout_policy: str = 'random'):
        """
        Initialize MCTS.

        Args:
            action_generator: For getting legal actions
            action_executor: For executing actions
            exploration_weight: UCB1 exploration parameter (default sqrt(2))
            max_rollout_depth: Max moves in a single simulation
            use_evaluation_cutoff: If True, use evaluator at cutoff instead of playing to end
            evaluator: GameStateEvaluator for cutoff evaluation
            rollout_policy: 'random' or 'greedy' - how to pick actions during simulation
        """
        self.action_generator = action_generator
        self.action_executor = action_executor
        self.exploration_weight = exploration_weight
        self.max_rollout_depth = max_rollout_depth
        self.use_evaluation_cutoff = use_evaluation_cutoff
        self.evaluator = evaluator
        self.rollout_policy = rollout_policy

    def search(self,
               root_state: GameState,
               player: str,
               num_simulations: int = 1000,
               time_limit: float = None) -> Action:
        """
        Run MCTS and return the best action.

        Args:
            root_state: Current game state
            player: Player making the decision
            num_simulations: Number of simulations to run
            time_limit: Optional time limit in seconds (overrides num_simulations)

        Returns:
            Best action found
        """
        # Create root node
        root = MCTSNode(
            state=root_state,
            player=player
        )
        root.untried_actions = self._get_legal_actions(root_state, player)

        # Handle trivial cases
        if len(root.untried_actions) == 0:
            return PassAction(player)
        if len(root.untried_actions) == 1:
            return root.untried_actions[0]

        # Run simulations
        start_time = time.time()
        simulations_run = 0

        while True:
            # Check stopping conditions
            if time_limit:
                if time.time() - start_time >= time_limit:
                    break
            else:
                if simulations_run >= num_simulations:
                    break

            # One iteration of MCTS
            node = self._select(root)

            if not node.is_terminal:
                node = self._expand(node)

            result = self._simulate(node, player)
            self._backpropagate(node, result, player)

            simulations_run += 1

        return root.best_action()

    def _get_legal_actions(self, state: GameState, player: str) -> List[Action]:
        """Get legal actions for a player in the current state."""
        actions = self.action_generator.get_all_legal_actions(state, player)

        # Filter by phase
        if state.current_phase == GamePhase.MOVEMENT:
            actions = [a for a in actions if isinstance(a, MoveAction)]

        # Always allow passing
        actions.append(PassAction(player))

        return actions

    def _select(self, node: MCTSNode) -> MCTSNode:
        """
        Select phase: Walk down tree using UCB1 until we find
        a node that's not fully expanded or is terminal.
        """
        while node.is_fully_expanded and not node.is_terminal:
            if not node.children:
                break
            node = node.best_child(self.exploration_weight)
        return node

    def _expand(self, node: MCTSNode) -> MCTSNode:
        """
        Expand phase: Add a new child node for an untried action.
        """
        if not node.untried_actions:
            return node

        # Pick a random untried action
        action = random.choice(node.untried_actions)
        node.untried_actions.remove(action)

        # Create new state by applying action
        new_state = node.state.clone()

        # Determine which player is acting
        acting_player = node.state.active_player or node.player

        # Execute the action
        if not isinstance(action, PassAction):
            self.action_executor.execute_action(new_state, action)

        # Create child node
        child = MCTSNode(
            state=new_state,
            parent=node,
            action=action,
            player=acting_player
        )

        # Get legal actions for next decision
        # This is simplified - in reality we'd need to track phase transitions
        next_player = "player2" if acting_player == "player1" else "player1"
        child.untried_actions = self._get_legal_actions(new_state, next_player)

        node.children.append(child)
        return child

    def _simulate(self, node: MCTSNode, perspective_player: str) -> float:
        """
        Simulate phase: Play randomly from this node until game ends.

        Returns:
            1.0 if perspective_player wins
            0.0 if perspective_player loses
            0.5 for draw
        """
        # Clone state for simulation
        state = node.state.clone()
        current_player = node.player
        depth = 0

        # Simulate until terminal or max depth
        while depth < self.max_rollout_depth:
            # Check for game end
            winner = state.check_victory_conditions()
            if winner:
                return 1.0 if winner == perspective_player else 0.0

            # Check objective control (turn 7+)
            if state.turn_number >= 7:
                obj_controller = state.check_objective_control()
                if obj_controller:
                    return 1.0 if obj_controller == perspective_player else 0.0

            # Check turn limit
            if state.turn_number > 10:
                # Use points as tiebreaker
                p1_pts = state.get_total_points("player1")
                p2_pts = state.get_total_points("player2")
                if p1_pts > p2_pts:
                    return 1.0 if perspective_player == "player1" else 0.0
                elif p2_pts > p1_pts:
                    return 1.0 if perspective_player == "player2" else 0.0
                else:
                    return 0.5

            # Get legal actions
            actions = self._get_legal_actions(state, current_player)
            if not actions:
                actions = [PassAction(current_player)]

            # Pick action based on rollout policy
            if self.rollout_policy == 'greedy':
                action = self._pick_greedy_action(state, actions, current_player)
            else:
                action = random.choice(actions)

            # Execute
            if not isinstance(action, PassAction):
                self.action_executor.execute_action(state, action)

            # Alternate players (simplified)
            current_player = "player2" if current_player == "player1" else "player1"
            depth += 1

            # Occasionally increment turn (very simplified)
            if depth % 4 == 0:
                state.turn_number += 1

        # Hit max depth - use evaluation if available, otherwise 0.5
        if self.use_evaluation_cutoff and self.evaluator:
            score = self.evaluator.evaluate(state, perspective_player)
            # Normalize to 0-1 range (rough heuristic)
            return max(0.0, min(1.0, 0.5 + score / 1000))

        return 0.5  # Unknown outcome

    def _pick_greedy_action(self, state: GameState, actions: List[Action], player: str) -> Action:
        """
        Pick an action using greedy heuristics (fast, no full evaluation).

        Priority:
        1. Attack disrupted/damaged targets
        2. Attack any target in range
        3. Move toward objective (especially turns 5+)
        4. Move toward cover
        5. Pass
        """
        attacks = [a for a in actions if isinstance(a, AttackAction)]
        moves = [a for a in actions if isinstance(a, MoveAction)]
        passes = [a for a in actions if isinstance(a, PassAction)]

        # Priority 1 & 2: Attacks
        if attacks:
            scored = []
            for attack in attacks:
                score = 10  # Base attack score
                target = state.get_unit_state(attack.target_id)
                if target:
                    if target.is_disrupted:
                        score += 20
                    if target.is_damaged:
                        score += 15
                    score -= attack.distance  # Prefer closer
                scored.append((attack, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            # Pick from top choices with some randomness
            top = scored[:max(1, len(scored)//3 + 1)]
            return random.choice([a for a, s in top])

        # Priority 3 & 4: Moves
        if moves:
            obj_q, obj_r = state.objective_position
            scored = []
            for move in moves:
                score = 0

                # Distance to objective
                obj_dist = state.board.hex_distance(move.to_q, move.to_r, obj_q, obj_r)

                # Late game: prioritize objective
                if state.turn_number >= 5:
                    score += max(0, 10 - obj_dist * 2)  # Closer = better

                # Cover bonus
                dest_hex = state.board.get_hex(move.to_q, move.to_r)
                if dest_hex and dest_hex.terrain in ['forest', 'building', 'hill']:
                    score += 5

                scored.append((move, score))

            scored.sort(key=lambda x: x[1], reverse=True)
            top = scored[:max(1, len(scored)//3 + 1)]
            return random.choice([m for m, s in top])

        # Fallback: pass
        if passes:
            return passes[0]

        return random.choice(actions)

    def _backpropagate(self, node: MCTSNode, result: float, perspective_player: str):
        """
        Backpropagate phase: Update win/visit counts up the tree.
        """
        while node is not None:
            node.visits += 1
            # Flip result for opponent's nodes
            if node.player == perspective_player:
                node.wins += result
            else:
                node.wins += (1.0 - result)
            node = node.parent


class MCTSAgent:
    """
    Agent that uses MCTS to choose actions.

    Integrates with the existing Agent interface from game_runner.
    """

    def __init__(self,
                 name: str = "MCTSAgent",
                 num_simulations: int = 500,
                 time_limit: float = None,
                 exploration_weight: float = 1.414,
                 use_evaluation_cutoff: bool = True,
                 rollout_policy: str = 'random'):
        """
        Initialize MCTS Agent.

        Args:
            name: Display name
            num_simulations: Simulations per decision (default 500)
            time_limit: Optional time limit per decision in seconds
            exploration_weight: UCB1 exploration parameter
            use_evaluation_cutoff: Use evaluator at max depth
            rollout_policy: 'random' or 'greedy' for simulation action selection
        """
        self.name = name
        self.num_simulations = num_simulations
        self.time_limit = time_limit
        self.exploration_weight = exploration_weight
        self.use_evaluation_cutoff = use_evaluation_cutoff
        self.rollout_policy = rollout_policy

        # These will be set by GameRunner
        self.action_generator = None
        self.action_executor = None
        self.evaluator = None
        self.mcts = None

    def set_action_executor(self, executor: ActionExecutor):
        """Called by GameRunner to provide the executor."""
        self.action_executor = executor

    def set_action_generator(self, generator: ActionGenerator):
        """Called by GameRunner to provide the generator."""
        self.action_generator = generator

    def set_evaluator(self, evaluator):
        """Called by GameRunner to provide the evaluator."""
        self.evaluator = evaluator

    def _ensure_mcts(self):
        """Create MCTS instance if not already created."""
        if self.mcts is None and self.action_generator and self.action_executor:
            self.mcts = MCTS(
                action_generator=self.action_generator,
                action_executor=self.action_executor,
                exploration_weight=self.exploration_weight,
                use_evaluation_cutoff=self.use_evaluation_cutoff,
                evaluator=self.evaluator,
                rollout_policy=self.rollout_policy
            )

    def choose_action(self,
                      game_state: GameState,
                      legal_actions: List[Action],
                      player: str) -> Action:
        """
        Choose an action using MCTS.

        Falls back to random if MCTS not configured.
        """
        if not legal_actions:
            return PassAction(player)

        if len(legal_actions) == 1:
            return legal_actions[0]

        self._ensure_mcts()

        if self.mcts:
            return self.mcts.search(
                game_state,
                player,
                num_simulations=self.num_simulations,
                time_limit=self.time_limit
            )
        else:
            # Fallback to random
            return random.choice(legal_actions)


# =============================================================================
# TEST
# =============================================================================

if __name__ == "__main__":
    import os
    import csv
    from board import Board
    from units import Unit
    from game_state import UnitState

    print("=" * 70)
    print("MCTS TEST")
    print("=" * 70)

    # Set up systems
    ability_file = 'Axis_and_Allies_Unit_Data_for_Analysis_-_Special_Abilities.csv'
    if not os.path.exists(ability_file):
        ability_file = 'Axis and Allies Unit Data for Analysis - Special_Abilities.csv'

    ability_system = AbilitySystem(ability_file)
    movement_system = MovementSystem(ability_system)
    action_generator = ActionGenerator(movement_system, None, ability_system)
    action_executor = ActionExecutor(movement_system, None, ability_system)

    # Load units
    unit_file = 'Axis_and_Allies_Unit_Data_for_Analysis_-_Unit_Stats.csv'
    if not os.path.exists(unit_file):
        unit_file = 'Axis and Allies Unit Data for Analysis - Unit_Stats.csv'

    units = []
    with open(unit_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            unit = Unit(
                name=row['Unit Name'], nation=row['Nation'], unit_type=row['Type'],
                year=row['Year'], cost=row['Cost'], defense=row['Def'],
                speed=row['Speed'], veh_short=row['Veh S'], veh_medium=row['Veh M'],
                veh_long=row['Veh L'], per_short=row['Per S'], per_medium=row['Per M'],
                per_long=row['Per L'], abilities=row['Abilities']
            )
            units.append(unit)

    soldiers = [u for u in units if u.unit_type == 'Soldier' and u.per_short > 0]

    # Create simple game
    board = Board(12, 12)

    inf1 = deepcopy(soldiers[0]); inf1.id = 'p1_inf1'
    inf2 = deepcopy(soldiers[1]); inf2.id = 'p1_inf2'
    inf3 = deepcopy(soldiers[2]); inf3.id = 'p2_inf1'
    inf4 = deepcopy(soldiers[3]); inf4.id = 'p2_inf2'

    p1_units = [
        UnitState(inf1, (2, 5), 'player1', inf1.defense_front),
        UnitState(inf2, (2, 7), 'player1', inf2.defense_front),
    ]
    p2_units = [
        UnitState(inf3, (9, 5), 'player2', inf3.defense_front),
        UnitState(inf4, (9, 7), 'player2', inf4.defense_front),
    ]

    game_state = GameState(board, p1_units, p2_units, objective_position=(6, 6))
    game_state.current_phase = GamePhase.MOVEMENT
    game_state.active_player = "player1"

    # Create MCTS
    mcts = MCTS(action_generator, action_executor)

    print(f"\nGame state: {len(p1_units)} vs {len(p2_units)} units")
    print(f"Objective at: {game_state.objective_position}")

    # Run MCTS search
    print(f"\nRunning MCTS with 100 simulations...")
    start = time.time()
    best_action = mcts.search(game_state, "player1", num_simulations=100)
    elapsed = time.time() - start

    print(f"Best action: {best_action}")
    print(f"Time: {elapsed:.2f}s")

    # Run with more simulations
    print(f"\nRunning MCTS with 500 simulations...")
    start = time.time()
    best_action = mcts.search(game_state, "player1", num_simulations=500)
    elapsed = time.time() - start

    print(f"Best action: {best_action}")
    print(f"Time: {elapsed:.2f}s")

    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)
