"""
Game State Evaluation for Axis & Allies Miniatures

Evaluates game states from a player's perspective for AI decision-making.
Higher scores indicate a better position for the evaluated player.
"""

from typing import Optional, Dict, List, Tuple
from copy import deepcopy
import math

from game_state import GameState, UnitState
from action import Action, MoveAction, AttackAction, PassAction
from board import Board


class GameStateEvaluator:
    """
    Evaluates game states from a player's perspective.

    Formula:
    Total = Material + Position + Status + Tactical + Threat

    Terminal states:
    - Win -> +inf
    - Loss -> -inf
    """

    # Scoring weights
    MATERIAL_PER_COST = 10.0  # Per point of unit cost

    # Health multipliers for material
    HEALTH_HEALTHY = 1.0
    HEALTH_DISRUPTED = 0.6
    HEALTH_DAMAGED = 0.7
    HEALTH_DISRUPTED_DAMAGED = 0.4

    # Position bonuses
    COVER_BONUS = 5.0  # In cover terrain
    OPEN_PENALTY = -3.0  # In open terrain
    MEDIUM_RANGE_BONUS = 2.0  # At medium range (2-4 hexes) from enemy

    # Status penalties
    DISRUPTED_PENALTY = -15.0
    DAMAGED_PENALTY = -10.0

    # Tactical bonuses
    ATTACK_POTENTIAL_PER_DIE = 1.5  # Per attack die available
    REAR_EXPOSED_PENALTY = -5.0  # Rear armor exposed to enemy

    # Threat scoring
    ENEMY_IN_RANGE_BONUS = 3.0  # I can shoot them
    IN_ENEMY_RANGE_PENALTY = -2.0  # They can shoot me

    # Objective scoring - PHASED approach
    # Turns 1-4: Focus on material and staging, don't rush objective
    # Turns 5-6: Start positioning for objective
    # Turn 7+: Objective control is critical

    # Control bonus (only matters turn 7+)
    OBJECTIVE_CONTROL_BONUS = 150.0  # Controlling objective (turn 7+)
    OBJECTIVE_DENY_BONUS = 100.0  # Having at least 1 unit adjacent (denies enemy win)

    # Staging area bonus (turns 1-6) - reward being in striking distance, not on objective
    OBJECTIVE_STAGING_BONUS = 8.0  # Being 2-4 hexes away (ready to move in)

    # Fire support bonus - reward units that can ATTACK the objective area
    OBJECTIVE_FIRE_SUPPORT_BONUS = 12.0  # Can attack objective hex from current position
    OBJECTIVE_OVERWATCH_BONUS = 6.0  # In good position to attack enemies near objective

    # Penalty for over-committing to objective
    OBJECTIVE_OVERCOMMIT_PENALTY = -10.0  # Per unit beyond the first on objective

    # Long-range unit threshold (units with this much medium+ range attack prefer overwatch)
    LONG_RANGE_THRESHOLD = 4  # If unit has 4+ dice at medium range, prefer fire support role

    # Cover-granting terrain types
    COVER_TERRAIN = ['forest', 'building', 'hill', 'town']

    def __init__(self):
        """Initialize the evaluator."""
        pass

    def evaluate(self, game_state: GameState, player: str) -> float:
        """
        Evaluate the game state from a player's perspective.

        Args:
            game_state: The game state to evaluate
            player: The player perspective ('player1' or 'player2')

        Returns:
            Score where higher is better for the player.
            Returns +inf for win, -inf for loss.
        """
        # Check terminal states first
        winner = game_state.check_victory_conditions()
        if winner == player:
            return float('inf')
        elif winner is not None:
            return float('-inf')

        my_units = game_state.get_units_by_owner(player)
        enemy_player = "player2" if player == "player1" else "player1"
        enemy_units = game_state.get_units_by_owner(enemy_player)

        # If I have no units, I lose
        if not my_units:
            return float('-inf')

        # If enemy has no units, I win
        if not enemy_units:
            return float('inf')

        total_score = 0.0

        # Calculate each component
        total_score += self._evaluate_material(my_units, enemy_units)
        total_score += self._evaluate_position(my_units, enemy_units, game_state)
        total_score += self._evaluate_status(my_units, enemy_units)
        total_score += self._evaluate_tactical(my_units, enemy_units, game_state)
        total_score += self._evaluate_threat(my_units, enemy_units, game_state)
        total_score += self._evaluate_objective(my_units, enemy_units, game_state, player)

        return total_score

    def evaluate_action(self, game_state: GameState, action: Action,
                        player: str, executor) -> float:
        """
        Evaluate an action by simulating it and scoring the resulting state.

        Args:
            game_state: Current game state
            action: Action to evaluate
            player: Player perspective
            executor: ActionExecutor to simulate the action

        Returns:
            Score of the resulting state, or -inf if action fails.
        """
        # Clone the game state
        cloned_state = game_state.clone()

        try:
            # Execute the action on the clone
            result = executor.execute_action(cloned_state, action)

            if not result.success:
                return float('-inf')

            # Evaluate the resulting state
            return self.evaluate(cloned_state, player)

        except Exception:
            # If anything goes wrong, this is a bad action
            return float('-inf')

    def _evaluate_material(self, my_units: List[UnitState],
                          enemy_units: List[UnitState]) -> float:
        """
        Evaluate material advantage.

        Material = Unit cost * health multiplier * MATERIAL_PER_COST
        """
        my_material = 0.0
        for unit_state in my_units:
            cost = getattr(unit_state.unit, 'cost', 1) or 1
            multiplier = self._get_health_multiplier(unit_state)
            my_material += cost * multiplier * self.MATERIAL_PER_COST

        enemy_material = 0.0
        for unit_state in enemy_units:
            cost = getattr(unit_state.unit, 'cost', 1) or 1
            multiplier = self._get_health_multiplier(unit_state)
            enemy_material += cost * multiplier * self.MATERIAL_PER_COST

        return my_material - enemy_material

    def _get_health_multiplier(self, unit_state: UnitState) -> float:
        """Get health multiplier based on unit status."""
        if unit_state.is_disrupted and unit_state.is_damaged:
            return self.HEALTH_DISRUPTED_DAMAGED
        elif unit_state.is_disrupted:
            return self.HEALTH_DISRUPTED
        elif unit_state.is_damaged:
            return self.HEALTH_DAMAGED
        else:
            return self.HEALTH_HEALTHY

    def _evaluate_position(self, my_units: List[UnitState],
                          enemy_units: List[UnitState],
                          game_state: GameState) -> float:
        """
        Evaluate positional advantage.

        - Cover terrain: +COVER_BONUS
        - Open terrain: +OPEN_PENALTY
        - Medium range (2-4 hexes) from enemies: +MEDIUM_RANGE_BONUS
        """
        score = 0.0

        for unit_state in my_units:
            q, r = unit_state.position
            hex_tile = game_state.board.get_hex(q, r)

            if hex_tile:
                terrain = hex_tile.terrain
                if terrain in self.COVER_TERRAIN:
                    score += self.COVER_BONUS
                elif terrain == 'open':
                    score += self.OPEN_PENALTY

            # Check distance to enemies
            for enemy_state in enemy_units:
                distance = game_state.board.hex_distance(
                    q, r, enemy_state.position[0], enemy_state.position[1]
                )
                # Medium range bonus (2-4 hexes is good tactical position)
                if 2 <= distance <= 4:
                    score += self.MEDIUM_RANGE_BONUS

        return score

    def _evaluate_status(self, my_units: List[UnitState],
                        enemy_units: List[UnitState]) -> float:
        """
        Evaluate status effects.

        My disrupted/damaged units are bad, enemy disrupted/damaged are good.
        """
        score = 0.0

        # My status penalties
        for unit_state in my_units:
            if unit_state.is_disrupted:
                score += self.DISRUPTED_PENALTY
            if unit_state.is_damaged:
                score += self.DAMAGED_PENALTY

        # Enemy status bonuses (their penalties are my advantage)
        for unit_state in enemy_units:
            if unit_state.is_disrupted:
                score -= self.DISRUPTED_PENALTY  # Negate = bonus for me
            if unit_state.is_damaged:
                score -= self.DAMAGED_PENALTY

        return score

    def _evaluate_tactical(self, my_units: List[UnitState],
                          enemy_units: List[UnitState],
                          game_state: GameState) -> float:
        """
        Evaluate tactical factors.

        - Attack potential (dice count): +ATTACK_POTENTIAL_PER_DIE per die
        - Rear armor exposed: +REAR_EXPOSED_PENALTY
        """
        score = 0.0

        for unit_state in my_units:
            unit = unit_state.unit

            # Calculate attack potential against enemies
            for enemy_state in enemy_units:
                distance = game_state.board.hex_distance(
                    unit_state.position[0], unit_state.position[1],
                    enemy_state.position[0], enemy_state.position[1]
                )

                dice = self._get_attack_dice(unit, enemy_state.unit, distance)
                if dice > 0:
                    score += dice * self.ATTACK_POTENTIAL_PER_DIE

            # Check if my rear is exposed (for vehicles)
            if unit.unit_type == 'Vehicle' and unit_state.facing is not None:
                rear_exposed = self._is_rear_exposed(unit_state, enemy_units, game_state)
                if rear_exposed:
                    score += self.REAR_EXPOSED_PENALTY

        # Enemy rear exposure is good for me
        for enemy_state in enemy_units:
            enemy = enemy_state.unit
            if enemy.unit_type == 'Vehicle' and enemy_state.facing is not None:
                rear_exposed = self._is_rear_exposed(enemy_state, my_units, game_state)
                if rear_exposed:
                    score -= self.REAR_EXPOSED_PENALTY  # Bonus for me

        return score

    def _evaluate_threat(self, my_units: List[UnitState],
                        enemy_units: List[UnitState],
                        game_state: GameState) -> float:
        """
        Evaluate threat levels.

        - Enemy in my range: +ENEMY_IN_RANGE_BONUS
        - I'm in enemy range: +IN_ENEMY_RANGE_PENALTY
        """
        score = 0.0

        for unit_state in my_units:
            unit = unit_state.unit
            my_pos = unit_state.position

            for enemy_state in enemy_units:
                enemy = enemy_state.unit
                enemy_pos = enemy_state.position

                distance = game_state.board.hex_distance(
                    my_pos[0], my_pos[1], enemy_pos[0], enemy_pos[1]
                )

                # Can I attack them?
                my_dice = self._get_attack_dice(unit, enemy, distance)
                if my_dice > 0:
                    score += self.ENEMY_IN_RANGE_BONUS

                # Can they attack me?
                enemy_dice = self._get_attack_dice(enemy, unit, distance)
                if enemy_dice > 0:
                    score += self.IN_ENEMY_RANGE_PENALTY

        return score

    def _evaluate_objective(self, my_units: List[UnitState],
                           enemy_units: List[UnitState],
                           game_state: GameState,
                           player: str) -> float:
        """
        Evaluate objective positioning with phased strategy.

        PHASES:
        - Turns 1-4: Focus on material advantage, stay in cover, position in staging area
        - Turns 5-6: Start moving toward objective, maintain fire support positions
        - Turn 7+: Objective control is critical, but don't over-commit

        KEY PRINCIPLES:
        - Only need 1 unit adjacent to deny enemy victory
        - Long-range units should provide fire support, not occupy objective
        - Don't rush into the open early - that's suicidal
        """
        score = 0.0
        turn = game_state.turn_number
        obj_q, obj_r = game_state.objective_position
        enemy_player = "player2" if player == "player1" else "player1"

        # Get adjacent units
        my_adjacent = game_state.get_units_adjacent_to_objective(player)
        enemy_adjacent = game_state.get_units_adjacent_to_objective(enemy_player)
        my_adjacent_count = len(my_adjacent)
        enemy_adjacent_count = len(enemy_adjacent)

        # === PHASE-BASED OBJECTIVE SCORING ===

        if turn >= 7:
            # CRITICAL PHASE: Objective control determines winner
            controller = game_state.check_objective_control()

            if controller == player:
                score += self.OBJECTIVE_CONTROL_BONUS
            elif controller == enemy_player:
                score -= self.OBJECTIVE_CONTROL_BONUS

            # Bonus for having at least 1 unit to deny enemy (diminishing returns)
            if my_adjacent_count >= 1:
                score += self.OBJECTIVE_DENY_BONUS
            if enemy_adjacent_count >= 1:
                score -= self.OBJECTIVE_DENY_BONUS

            # Penalty for over-committing (more than 1 unit on objective wastes firepower)
            if my_adjacent_count > 1:
                score += (my_adjacent_count - 1) * self.OBJECTIVE_OVERCOMMIT_PENALTY

        elif turn >= 5:
            # POSITIONING PHASE: Start valuing objective proximity, but don't over-commit
            # Moderate bonus for having presence
            if my_adjacent_count >= 1:
                score += self.OBJECTIVE_DENY_BONUS * 0.5
            if enemy_adjacent_count >= 1:
                score -= self.OBJECTIVE_DENY_BONUS * 0.5

            # Still penalize over-commitment
            if my_adjacent_count > 1:
                score += (my_adjacent_count - 1) * self.OBJECTIVE_OVERCOMMIT_PENALTY

        # else: Turns 1-4 - No bonus for being on objective (focus on material/positioning)

        # === STAGING AREA BONUS (all phases) ===
        # Reward units in "striking distance" (2-4 hexes) - ready to move in when needed
        for unit_state in my_units:
            unit_q, unit_r = unit_state.position
            distance = game_state.board.hex_distance(obj_q, obj_r, unit_q, unit_r)

            # Staging bonus: being 2-4 hexes away is good positioning
            if 2 <= distance <= 4:
                # Scale staging bonus by turn (more important as we approach turn 7)
                staging_multiplier = 1.0 if turn <= 4 else (1.5 if turn <= 6 else 0.5)
                score += self.OBJECTIVE_STAGING_BONUS * staging_multiplier

        # === FIRE SUPPORT POSITIONING ===
        # Units should be positioned to ATTACK enemies on/near the objective
        for unit_state in my_units:
            unit = unit_state.unit
            unit_q, unit_r = unit_state.position

            # Check if this unit is a "fire support" unit (good at medium+ range)
            medium_dice = max(
                getattr(unit, 'per_medium', 0) or 0,
                getattr(unit, 'veh_medium', 0) or 0
            )
            is_fire_support_unit = medium_dice >= self.LONG_RANGE_THRESHOLD

            # Distance to objective
            dist_to_obj = game_state.board.hex_distance(obj_q, obj_r, unit_q, unit_r)

            if is_fire_support_unit:
                # Fire support units should be 2-4 hexes from objective (medium range)
                if 2 <= dist_to_obj <= 4:
                    score += self.OBJECTIVE_FIRE_SUPPORT_BONUS

                    # Extra bonus if there are enemies near objective to shoot at
                    if enemy_adjacent_count > 0:
                        score += self.OBJECTIVE_OVERWATCH_BONUS

                # Penalty for fire support unit being ON the objective (wasted potential)
                if dist_to_obj <= 1 and turn < 7:
                    score -= self.OBJECTIVE_FIRE_SUPPORT_BONUS

            # Bonus for being able to attack the objective hex itself
            # (can clear enemies off the objective)
            if dist_to_obj <= 4 and dist_to_obj > 0:  # In attack range but not on it
                attack_dice = self._get_attack_dice_at_range(unit, dist_to_obj)
                if attack_dice > 0:
                    score += self.OBJECTIVE_OVERWATCH_BONUS * 0.5

        # === ENEMY FIRE SUPPORT PENALTY ===
        # Penalize if enemy has good fire support positions
        for unit_state in enemy_units:
            unit = unit_state.unit
            unit_q, unit_r = unit_state.position
            dist_to_obj = game_state.board.hex_distance(obj_q, obj_r, unit_q, unit_r)

            if 2 <= dist_to_obj <= 4:
                medium_dice = max(
                    getattr(unit, 'per_medium', 0) or 0,
                    getattr(unit, 'veh_medium', 0) or 0
                )
                if medium_dice >= self.LONG_RANGE_THRESHOLD:
                    score -= self.OBJECTIVE_FIRE_SUPPORT_BONUS * 0.5

        return score

    def _get_attack_dice_at_range(self, unit, distance: int) -> int:
        """Get attack dice for a unit at a given distance (against personnel)."""
        if distance <= 1:
            return getattr(unit, 'per_short', 0) or 0
        elif distance <= 4:
            return getattr(unit, 'per_medium', 0) or 0
        else:
            return getattr(unit, 'per_long', 0) or 0

    def _get_attack_dice(self, attacker, target, distance: int) -> int:
        """Get number of attack dice for an attack."""
        # Determine range category
        if distance <= 1:
            range_cat = 'short'
        elif distance <= 4:
            range_cat = 'medium'
        else:
            range_cat = 'long'

        # Check target type
        target_type = getattr(target, 'unit_type', 'Soldier')
        is_vehicle = target_type == 'Vehicle'

        if is_vehicle:
            if range_cat == 'short':
                return getattr(attacker, 'veh_short', 0) or 0
            elif range_cat == 'medium':
                return getattr(attacker, 'veh_medium', 0) or 0
            else:
                return getattr(attacker, 'veh_long', 0) or 0
        else:
            if range_cat == 'short':
                return getattr(attacker, 'per_short', 0) or 0
            elif range_cat == 'medium':
                return getattr(attacker, 'per_medium', 0) or 0
            else:
                return getattr(attacker, 'per_long', 0) or 0

    def _is_rear_exposed(self, unit_state: UnitState,
                        enemy_units: List[UnitState],
                        game_state: GameState) -> bool:
        """Check if any enemy can hit this unit's rear armor."""
        from facing import HexDirection, is_front_arc_attack

        if unit_state.facing is None:
            return False

        facing = HexDirection(unit_state.facing)
        target_pos = unit_state.position

        for enemy_state in enemy_units:
            enemy_pos = enemy_state.position

            # Calculate distance
            distance = game_state.board.hex_distance(
                enemy_pos[0], enemy_pos[1], target_pos[0], target_pos[1]
            )

            # Check if enemy can attack
            dice = self._get_attack_dice(enemy_state.unit, unit_state.unit, distance)
            if dice > 0:
                # Check if it's a rear attack
                is_front = is_front_arc_attack(enemy_pos, target_pos, facing)
                if not is_front:
                    return True

        return False


# Test the evaluator
if __name__ == "__main__":
    from board import Board
    from game_state import GameState, GamePhase
    from copy import deepcopy
    import csv
    import os

    print("=" * 70)
    print("GAME STATE EVALUATOR TEST")
    print("=" * 70)

    # Load units
    unit_file = 'Axis_and_Allies_Unit_Data_for_Analysis_-_Unit_Stats.csv'
    if not os.path.exists(unit_file):
        unit_file = 'Axis and Allies Unit Data for Analysis - Unit_Stats.csv'

    from units import Unit
    units = []
    with open(unit_file, 'r') as file:
        reader = csv.DictReader(file)
        for row in reader:
            unit = Unit(
                name=row['Unit Name'],
                nation=row['Nation'],
                unit_type=row['Type'],
                year=row['Year'],
                cost=row['Cost'],
                defense=row['Def'],
                speed=row['Speed'],
                veh_short=row['Veh S'],
                veh_medium=row['Veh M'],
                veh_long=row['Veh L'],
                per_short=row['Per S'],
                per_medium=row['Per M'],
                per_long=row['Per L'],
                abilities=row['Abilities']
            )
            units.append(unit)

    soldiers = [u for u in units if u.unit_type == 'Soldier' and u.per_short > 0][:4]

    if len(soldiers) >= 4:
        # Create test scenario
        board = Board(15, 15)
        board.set_terrain(5, 5, 'forest')

        # Create unit states
        inf1 = deepcopy(soldiers[0])
        inf1.id = "p1_inf1"
        inf2 = deepcopy(soldiers[1])
        inf2.id = "p1_inf2"
        inf3 = deepcopy(soldiers[2])
        inf3.id = "p2_inf1"
        inf4 = deepcopy(soldiers[3])
        inf4.id = "p2_inf2"

        # Player 1 units
        p1_units = [
            UnitState(inf1, (3, 3), "player1", inf1.defense_front),
            UnitState(inf2, (5, 5), "player1", inf2.defense_front),  # In forest
        ]

        # Player 2 units
        p2_units = [
            UnitState(inf3, (6, 6), "player2", inf3.defense_front),
            UnitState(inf4, (8, 8), "player2", inf4.defense_front),
        ]

        # Create game state
        game_state = GameState(board, p1_units, p2_units)
        game_state.current_phase = GamePhase.ASSAULT

        # Evaluate
        evaluator = GameStateEvaluator()

        print("\nInitial State:")
        print(f"  Player 1 units: {len(p1_units)}")
        print(f"  Player 2 units: {len(p2_units)}")

        score_p1 = evaluator.evaluate(game_state, "player1")
        score_p2 = evaluator.evaluate(game_state, "player2")

        print(f"\n  Player 1 score: {score_p1:.1f}")
        print(f"  Player 2 score: {score_p2:.1f}")

        # Test with disrupted unit
        print("\n--- After disrupting p2_inf1 ---")
        p2_units[0].is_disrupted = True
        game_state2 = GameState(board, p1_units, p2_units)

        score_p1_2 = evaluator.evaluate(game_state2, "player1")
        score_p2_2 = evaluator.evaluate(game_state2, "player2")

        print(f"  Player 1 score: {score_p1_2:.1f} (change: {score_p1_2 - score_p1:+.1f})")
        print(f"  Player 2 score: {score_p2_2:.1f} (change: {score_p2_2 - score_p2:+.1f})")

    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)
