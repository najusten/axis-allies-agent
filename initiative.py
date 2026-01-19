"""
Initiative System for Axis & Allies Miniatures

Handles the initiative phase at the start of each turn.
- Both players roll 2d6
- Add commander bonuses (highest only, don't stack)
- Add Recon bonus if applicable
- Winner chooses who goes first
"""

import random
from typing import Tuple, Optional, Dict, List
from dataclasses import dataclass


@dataclass
class InitiativeResult:
    """Result of an initiative roll"""
    player: str
    dice: Tuple[int, int]  # The two d6 rolls
    base_total: int  # Sum of dice
    commander_bonus: int  # Best commander bonus (don't stack)
    recon_bonus: int  # +1 if Recon unit has LOS to enemy
    organization_reroll: bool  # Whether Organization was used
    final_total: int  # base + commander + recon

    def __str__(self):
        bonuses = []
        if self.commander_bonus > 0:
            bonuses.append(f"Commander +{self.commander_bonus}")
        if self.recon_bonus > 0:
            bonuses.append(f"Recon +{self.recon_bonus}")
        if self.organization_reroll:
            bonuses.append("(rerolled)")

        bonus_str = f" [{', '.join(bonuses)}]" if bonuses else ""
        return f"{self.player}: {self.dice[0]}+{self.dice[1]}={self.base_total}{bonus_str} → {self.final_total}"


class InitiativeSystem:
    """
    Handles initiative rolls and determines turn order.

    Rules:
    - Each player rolls 2d6
    - Add highest commander bonus (Initiative +X abilities don't stack)
    - Add +1 for each Recon unit with LOS to an enemy
    - Organization ability allows rerolling both dice
    - Winner chooses who goes first (usually themselves)
    """

    def __init__(self, ability_system=None, random_seed: Optional[int] = None):
        """
        Initialize the initiative system.

        Args:
            ability_system: AbilitySystem for checking unit abilities
            random_seed: Optional seed for reproducible rolls
        """
        self.ability_system = ability_system
        if random_seed is not None:
            random.seed(random_seed)

    def roll_initiative(self, game_state, player: str,
                       use_organization: bool = True) -> InitiativeResult:
        """
        Roll initiative for a player.

        Args:
            game_state: Current game state
            player: Player rolling ('player1' or 'player2')
            use_organization: Whether to automatically use Organization if available

        Returns:
            InitiativeResult with all roll details
        """
        # Roll 2d6
        dice = (random.randint(1, 6), random.randint(1, 6))
        base_total = dice[0] + dice[1]

        # Check for Organization ability (reroll option)
        has_organization = self._has_organization(game_state, player)
        used_organization = False

        if has_organization and use_organization:
            # AI decision: reroll if below average (7)
            if base_total < 7:
                dice = (random.randint(1, 6), random.randint(1, 6))
                base_total = dice[0] + dice[1]
                used_organization = True

        # Get commander bonus (highest only, don't stack)
        commander_bonus = self._get_commander_bonus(game_state, player)

        # Get Recon bonus
        recon_bonus = self._get_recon_bonus(game_state, player)

        final_total = base_total + commander_bonus + recon_bonus

        return InitiativeResult(
            player=player,
            dice=dice,
            base_total=base_total,
            commander_bonus=commander_bonus,
            recon_bonus=recon_bonus,
            organization_reroll=used_organization,
            final_total=final_total
        )

    def determine_first_player(self, game_state,
                               p1_chooses_second: bool = False,
                               p2_chooses_second: bool = False) -> Tuple[str, InitiativeResult, InitiativeResult]:
        """
        Roll initiative for both players and determine who goes first.

        Args:
            game_state: Current game state
            p1_chooses_second: If P1 wins, do they want to go second?
            p2_chooses_second: If P2 wins, do they want to go second?

        Returns:
            Tuple of (first_player, p1_result, p2_result)
        """
        p1_result = self.roll_initiative(game_state, "player1")
        p2_result = self.roll_initiative(game_state, "player2")

        # Determine winner
        if p1_result.final_total > p2_result.final_total:
            winner = "player1"
            # Winner chooses - usually go first, but might choose second
            first_player = "player2" if p1_chooses_second else "player1"
        elif p2_result.final_total > p1_result.final_total:
            winner = "player2"
            first_player = "player1" if p2_chooses_second else "player2"
        else:
            # Tie - per rules: player with better commander bonus wins
            # If commander bonuses also equal, reroll
            if p1_result.commander_bonus > p2_result.commander_bonus:
                winner = "player1"
                first_player = "player2" if p1_chooses_second else "player1"
            elif p2_result.commander_bonus > p1_result.commander_bonus:
                winner = "player2"
                first_player = "player1" if p2_chooses_second else "player2"
            else:
                # Both tied on total AND commander bonus - reroll recursively
                return self.determine_first_player(
                    game_state, p1_chooses_second, p2_chooses_second
                )

        return first_player, p1_result, p2_result

    def _get_commander_bonus(self, game_state, player: str) -> int:
        """
        Get the best commander initiative bonus for a player.

        Commander bonuses don't stack - only use the highest.

        Format in data: "COMMANDER ABILITIES:1", "COMMANDER ABILITIES:2", "COMMANDER ABILITIES:3"
        These correspond to Initiative +1, +2, +3
        """
        best_bonus = 0

        for unit_state in game_state.get_units_by_owner(player):
            if not unit_state.is_alive:
                continue

            unit = unit_state.unit
            abilities = getattr(unit, 'abilities', []) or []

            # Handle both list and string formats
            if isinstance(abilities, str):
                abilities_str = abilities
            else:
                abilities_str = ', '.join(abilities)

            # Check for COMMANDER ABILITIES:X format
            if 'COMMANDER ABILITIES:3' in abilities_str:
                best_bonus = max(best_bonus, 3)
            elif 'COMMANDER ABILITIES:2' in abilities_str:
                best_bonus = max(best_bonus, 2)
            elif 'COMMANDER ABILITIES:1' in abilities_str:
                best_bonus = max(best_bonus, 1)

            # Also check for "Initiative +X" format (in case it appears)
            if 'Initiative +3' in abilities_str:
                best_bonus = max(best_bonus, 3)
            elif 'Initiative +2' in abilities_str:
                best_bonus = max(best_bonus, 2)
            elif 'Initiative +1' in abilities_str:
                best_bonus = max(best_bonus, 1)

        return best_bonus

    def _get_recon_bonus(self, game_state, player: str) -> int:
        """
        Get Recon bonus: +1 for each Recon unit with LOS to an enemy.

        Note: Based on rules text "While this unit has line of sight to an enemy unit,
        add +1 to your initiative rolls" - this seems to be per unit with Recon that
        has LOS, but typically capped or just +1 total. Using +1 total for simplicity.
        """
        enemy_player = "player2" if player == "player1" else "player1"
        enemy_units = game_state.get_units_by_owner(enemy_player)

        if not enemy_units:
            return 0

        # Check each unit for Recon ability
        for unit_state in game_state.get_units_by_owner(player):
            if not unit_state.is_alive:
                continue

            unit = unit_state.unit
            abilities = getattr(unit, 'abilities', []) or []
            abilities_str = ', '.join(abilities) if isinstance(abilities, list) else abilities

            if 'Recon' in abilities_str:
                # Check if this unit has LOS to any enemy
                for enemy_state in enemy_units:
                    if not enemy_state.is_alive:
                        continue

                    # Simple LOS check - would need proper LOS system
                    # For now, assume LOS if within 8 hexes and no blocking terrain
                    distance = game_state.board.hex_distance(
                        unit_state.position[0], unit_state.position[1],
                        enemy_state.position[0], enemy_state.position[1]
                    )

                    if distance <= 8:
                        # Has LOS to at least one enemy
                        return 1

        return 0

    def _has_organization(self, game_state, player: str) -> bool:
        """Check if player has a unit with Organization ability."""
        for unit_state in game_state.get_units_by_owner(player):
            if not unit_state.is_alive:
                continue

            unit = unit_state.unit
            abilities = getattr(unit, 'abilities', []) or []
            abilities_str = ', '.join(abilities) if isinstance(abilities, list) else abilities

            if 'Organization' in abilities_str:
                return True

        return False


# For AI decision-making: should winner choose to go second?
def should_choose_second(game_state, player: str) -> bool:
    """
    Decide if the initiative winner should choose to go second.

    Generally go first, but might want second if:
    - Have Strike and Fade units (attack then retreat)
    - Defensive position and want to react to enemy movement
    - Have units that benefit from seeing enemy positions first

    For now, always choose to go first (simple heuristic).
    """
    # TODO: Implement smarter logic based on army composition
    return False


# Test the initiative system
if __name__ == "__main__":
    from board import Board
    from game_state import GameState, UnitState
    from units import Unit
    from copy import deepcopy
    import csv
    import os

    print("=" * 70)
    print("INITIATIVE SYSTEM TEST")
    print("=" * 70)

    # Load units
    unit_file = 'Axis_and_Allies_Unit_Data_for_Analysis_-_Unit_Stats.csv'
    if not os.path.exists(unit_file):
        unit_file = 'Axis and Allies Unit Data for Analysis - Unit_Stats.csv'

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

    # Find units with initiative abilities (abilities is a list)
    def has_commander_ability(u):
        abilities = u.abilities if u.abilities else []
        return any('COMMANDER ABILITIES:' in a for a in abilities)

    def has_recon(u):
        abilities = u.abilities if u.abilities else []
        return any('Recon' in a for a in abilities)

    commanders = [u for u in units if has_commander_ability(u)]
    recon_units = [u for u in units if has_recon(u)]

    print(f"\nFound {len(commanders)} units with Commander abilities:")
    for u in commanders[:10]:
        print(f"  {u.name}: {u.abilities}")

    print(f"\nFound {len(recon_units)} units with Recon:")
    for u in recon_units[:5]:
        print(f"  {u.name}")

    # Create test game
    board = Board(15, 15)
    soldiers = [u for u in units if u.unit_type == 'Soldier' and u.per_short > 0][:4]

    if len(soldiers) >= 4 and len(commanders) >= 1:
        inf1 = deepcopy(soldiers[0]); inf1.id = 'p1_inf1'
        # Give P1 a commander
        cmd1 = deepcopy(commanders[0]); cmd1.id = 'p1_cmd'
        inf3 = deepcopy(soldiers[2]); inf3.id = 'p2_inf1'
        inf4 = deepcopy(soldiers[3]); inf4.id = 'p2_inf2'

        p1_units = [
            UnitState(inf1, (3, 7), 'player1', inf1.defense_front),
            UnitState(cmd1, (3, 8), 'player1', cmd1.defense_front),
        ]
        p2_units = [
            UnitState(inf3, (11, 7), 'player2', inf3.defense_front),
            UnitState(inf4, (11, 8), 'player2', inf4.defense_front),
        ]

        game_state = GameState(board, p1_units, p2_units)

        # Test initiative rolls
        init_system = InitiativeSystem()

        print(f"\n--- P1 has commander: {cmd1.name} ---")
        print("\n--- Rolling Initiative (10 times) ---")
        for i in range(10):
            first, p1_res, p2_res = init_system.determine_first_player(game_state)
            print(f"Roll {i+1}: {p1_res} vs {p2_res} → {first} goes first")

    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)
