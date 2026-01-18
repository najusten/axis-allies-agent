"""
Defensive Fire System for Axis & Allies Miniatures

Implements the defensive fire mechanic that triggers when units move
between hexes adjacent to enemy units.

Key Rules:
- Triggered when moving from one adjacent hex to another adjacent hex
- Moving AWAY from enemy (out of adjacency) does NOT trigger
- Soldiers don't provoke defensive fire from Vehicles
- Disrupted units cannot make defensive fire attacks
- Each unit can only make ONE defensive fire attack per phase
- Defender chooses which hex (start or destination) to resolve the attack in
- Effect: If attack scores hits, target is DISRUPTED (not damaged/destroyed)
- Cover save success NEGATES the attack entirely (not just reduces to disruption)
- If disrupted by defensive fire, unit stops movement immediately
"""

from typing import List, Tuple, Optional, Dict, Set
from dataclasses import dataclass
from enum import Enum

from game_state import GameState, UnitState
from board import Board
from dice import DiceSystem, UnitCategory


@dataclass
class DefensiveFireOpportunity:
    """Represents a potential defensive fire attack"""
    defender_id: str           # Unit that CAN fire
    defender_state: UnitState
    target_id: str             # Unit that is moving (provoking)
    target_state: UnitState
    from_hex: Tuple[int, int]  # Hex target is moving FROM
    to_hex: Tuple[int, int]    # Hex target is moving TO
    defender_pos: Tuple[int, int]  # Where defender is located


@dataclass 
class DefensiveFireResult:
    """Result of a defensive fire attack"""
    defender_id: str
    target_id: str
    attack_hex: Tuple[int, int]  # Which hex the attack was resolved in
    dice_rolled: int
    rolls: List[int]
    successes: int
    target_defense: int
    hit: bool                    # Did the attack score enough successes?
    cover_roll: Optional[int]    # Cover roll if applicable
    cover_success: bool          # Did cover negate the attack?
    target_disrupted: bool       # Final result: was target disrupted?
    movement_stopped: bool       # Did target have to stop moving?
    message: str


class DefensiveFireSystem:
    """
    Handles all defensive fire logic.
    
    This system is called during movement to check if moving units
    provoke defensive fire from enemy units.
    """
    
    def __init__(self, ability_system=None, random_seed: int = None):
        """
        Initialize the defensive fire system.
        
        Args:
            ability_system: Optional AbilitySystem for checking special abilities
            random_seed: Optional seed for reproducible dice rolls
        """
        self.ability_system = ability_system
        self.dice_system = DiceSystem(random_seed)
        
        # Track which units have already fired defensively this phase
        # Reset this at the start of each movement phase
        self._units_fired_this_phase: Set[str] = set()
    
    def reset_phase(self):
        """Reset tracking for a new phase. Call at start of each movement phase."""
        self._units_fired_this_phase.clear()
    
    def get_adjacent_hexes(self, q: int, r: int) -> List[Tuple[int, int]]:
        """Get all 6 adjacent hexes for a given position."""
        # Axial coordinate neighbors
        directions = [
            (1, 0), (1, -1), (0, -1),
            (-1, 0), (-1, 1), (0, 1)
        ]
        return [(q + dq, r + dr) for dq, dr in directions]
    
    def is_adjacent(self, pos1: Tuple[int, int], pos2: Tuple[int, int]) -> bool:
        """Check if two positions are adjacent (including same hex)."""
        q1, r1 = pos1
        q2, r2 = pos2
        
        # Same hex counts as adjacent per rules
        if pos1 == pos2:
            return True
        
        # Check if pos2 is in the 6 neighbors of pos1
        return pos2 in self.get_adjacent_hexes(q1, r1)
    
    def check_defensive_fire_triggered(
        self,
        game_state: GameState,
        moving_unit_id: str,
        from_hex: Tuple[int, int],
        to_hex: Tuple[int, int]
    ) -> List[DefensiveFireOpportunity]:
        """
        Check if a move triggers defensive fire from any enemy units.
        
        Defensive fire is triggered when:
        1. Unit moves from one hex adjacent to enemy to another hex adjacent to same enemy
        2. The enemy unit is not disrupted
        3. The enemy unit hasn't already fired defensively this phase
        4. Soldiers moving don't trigger defensive fire from Vehicles
        
        Args:
            game_state: Current game state
            moving_unit_id: ID of the unit that is moving
            from_hex: Starting position
            to_hex: Destination position
        
        Returns:
            List of DefensiveFireOpportunity objects for each enemy that can fire
        """
        opportunities = []
        
        # Get the moving unit
        moving_unit_state = game_state.get_unit_state(moving_unit_id)
        if not moving_unit_state or not moving_unit_state.is_alive:
            return opportunities
        
        moving_unit = moving_unit_state.unit
        moving_is_soldier = moving_unit.unit_type == 'Soldier'
        
        # Determine owner of moving unit to find enemies
        moving_owner = moving_unit_state.owner
        enemy_owner = "player2" if moving_owner == "player1" else "player1"
        
        # Check each enemy unit
        enemy_units = game_state.get_units_by_owner(enemy_owner)
        
        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue
            
            enemy_unit = enemy_state.unit
            enemy_pos = enemy_state.position
            enemy_id = enemy_unit.id
            
            # Rule: Disrupted units cannot make defensive fire attacks
            if enemy_state.is_disrupted:
                continue
            
            # Rule: Each unit can only fire defensively once per phase
            if enemy_id in self._units_fired_this_phase:
                continue
            
            # Rule: Soldiers don't provoke defensive fire from Vehicles
            if moving_is_soldier and enemy_unit.unit_type == 'Vehicle':
                continue
            
            # Check adjacency: must be adjacent to BOTH from_hex and to_hex
            adjacent_to_from = self.is_adjacent(enemy_pos, from_hex)
            adjacent_to_to = self.is_adjacent(enemy_pos, to_hex)
            
            # Defensive fire triggers when moving between two adjacent hexes
            if adjacent_to_from and adjacent_to_to:
                opportunity = DefensiveFireOpportunity(
                    defender_id=enemy_id,
                    defender_state=enemy_state,
                    target_id=moving_unit_id,
                    target_state=moving_unit_state,
                    from_hex=from_hex,
                    to_hex=to_hex,
                    defender_pos=enemy_pos
                )
                opportunities.append(opportunity)
        
        return opportunities
    
    def get_attack_dice(
        self,
        attacker: 'Unit',
        target: 'Unit', 
        distance: int,
        attacker_state: UnitState
    ) -> int:
        """
        Calculate number of attack dice for defensive fire.
        
        Args:
            attacker: The unit making defensive fire
            target: The unit being shot at
            distance: Distance in hexes (0 = same hex, 1 = adjacent)
            attacker_state: State of attacking unit (for disruption check)
        
        Returns:
            Number of dice to roll
        """
        # Determine which attack value to use based on target type
        target_is_vehicle = target.unit_type == 'Vehicle'
        
        if target_is_vehicle:
            if distance <= 1:
                dice = attacker.veh_short
            elif distance <= 4:
                dice = attacker.veh_medium
            else:
                dice = attacker.veh_long
        else:
            if distance <= 1:
                dice = attacker.per_short
            elif distance <= 4:
                dice = attacker.per_medium
            else:
                dice = attacker.per_long
        
        # Disrupted/damaged attackers get -1 die (already checked in trigger,
        # but included for completeness if called directly)
        if attacker_state.is_disrupted or attacker_state.is_damaged:
            dice = max(0, dice - 1)
        
        return dice
    
    def get_defense_value(
        self,
        target: 'Unit',
        target_state: UnitState,
        is_rear_attack: bool = False
    ) -> int:
        """
        Get effective defense value for the target.
        
        Args:
            target: Target unit
            target_state: Target's current state
            is_rear_attack: Whether this is a rear attack (for vehicles)
        
        Returns:
            Effective defense value
        """
        # Base defense
        if target.unit_type == 'Vehicle' and is_rear_attack:
            defense = target.defense_rear if target.defense_rear else target.defense_front
        else:
            defense = target.defense_front if target.defense_front else target.defense
        
        if defense is None:
            defense = 3  # Default
        
        # Disrupted: -1 defense
        if target_state.is_disrupted:
            defense = max(1, defense - 1)
        
        # Damaged: -1 defense (doesn't stack with disrupted for penalty purposes,
        # but damaged is a separate state that also gives -1)
        if target_state.is_damaged:
            defense = max(1, defense - 1)
        
        return defense
    
    def resolve_defensive_fire(
        self,
        game_state: GameState,
        opportunity: DefensiveFireOpportunity,
        attack_in_hex: Tuple[int, int] = None
    ) -> DefensiveFireResult:
        """
        Resolve a defensive fire attack.
        
        Args:
            game_state: Current game state
            opportunity: The defensive fire opportunity
            attack_in_hex: Which hex to resolve attack in (from_hex or to_hex)
                          If None, defaults to to_hex (usually better for defender)
        
        Returns:
            DefensiveFireResult with all details
        """
        defender_state = opportunity.defender_state
        target_state = opportunity.target_state
        defender = defender_state.unit
        target = target_state.unit
        
        # Default to attacking in destination hex (defender's choice in real game)
        if attack_in_hex is None:
            attack_in_hex = opportunity.to_hex
        
        # Calculate distance from defender to attack hex
        def hex_distance(a: Tuple[int, int], b: Tuple[int, int]) -> int:
            q1, r1 = a
            q2, r2 = b
            return (abs(q1 - q2) + abs(q1 + r1 - q2 - r2) + abs(r1 - r2)) // 2
        
        distance = hex_distance(opportunity.defender_pos, attack_in_hex)
        
        # Get attack dice
        num_dice = self.get_attack_dice(defender, target, distance, defender_state)
        
        if num_dice <= 0:
            # Can't attack - no dice
            return DefensiveFireResult(
                defender_id=opportunity.defender_id,
                target_id=opportunity.target_id,
                attack_hex=attack_in_hex,
                dice_rolled=0,
                rolls=[],
                successes=0,
                target_defense=0,
                hit=False,
                cover_roll=None,
                cover_success=False,
                target_disrupted=False,
                movement_stopped=False,
                message=f"{defender.name} cannot attack {target.name} (no attack value)"
            )
        
        # Mark this unit as having fired defensively
        self._units_fired_this_phase.add(opportunity.defender_id)
        
        # Roll attack
        attack_result = self.dice_system.roll_attack(
            num_dice=num_dice,
            is_disrupted=defender_state.is_disrupted,
            is_damaged=defender_state.is_damaged
        )
        
        # Get target defense
        # For vehicles, determine if this is front or rear based on movement direction
        # (Vehicle faces toward hex it's entering during defensive fire)
        is_rear = False  # Simplified - would need facing system for full implementation
        defense = self.get_defense_value(target, target_state, is_rear)
        
        # Check if attack hits (successes >= defense)
        hit = attack_result.successes >= defense
        
        # Handle cover
        cover_roll = None
        cover_success = False
        
        if hit:
            # Check if target hex has cover
            hex_obj = game_state.board.hexes.get(attack_in_hex)
            terrain = hex_obj.terrain if hex_obj else 'open'
            has_cover = terrain in ['forest', 'town', 'hill', 'marsh', 'building']
            
            if has_cover:
                # Determine cover roll threshold based on unit type
                if target.unit_type == 'Vehicle':
                    unit_category = UnitCategory.VEHICLE
                else:
                    unit_category = UnitCategory.SOLDIER
                
                # Same hex gives -1 penalty to cover
                same_hex = (opportunity.defender_pos == attack_in_hex)
                
                cover_result = self.dice_system.roll_cover_save(
                    unit_category=unit_category,
                    attacker_same_hex=same_hex
                )
                cover_roll = cover_result.roll
                cover_success = cover_result.success
        
        # Determine final result
        # Defensive fire special rule: cover success NEGATES attack entirely
        # (not just reduces to disruption like normal combat)
        target_disrupted = False
        movement_stopped = False
        
        if hit and not cover_success:
            target_disrupted = True
            movement_stopped = True
        
        # Build message
        if num_dice == 0:
            message = f"{defender.name} has no attack against {target.name}"
        elif not hit:
            message = (f"{defender.name} defensive fire vs {target.name}: "
                      f"{attack_result.successes} successes vs defense {defense} - MISS")
        elif cover_success:
            message = (f"{defender.name} defensive fire vs {target.name}: "
                      f"HIT but cover save succeeds (rolled {cover_roll}) - NEGATED")
        else:
            message = (f"{defender.name} defensive fire vs {target.name}: "
                      f"{attack_result.successes} successes vs defense {defense} - "
                      f"DISRUPTED! Movement stopped.")
        
        return DefensiveFireResult(
            defender_id=opportunity.defender_id,
            target_id=opportunity.target_id,
            attack_hex=attack_in_hex,
            dice_rolled=num_dice,
            rolls=attack_result.rolls,
            successes=attack_result.successes,
            target_defense=defense,
            hit=hit,
            cover_roll=cover_roll,
            cover_success=cover_success,
            target_disrupted=target_disrupted,
            movement_stopped=movement_stopped,
            message=message
        )
    
    def apply_defensive_fire_result(
        self,
        game_state: GameState,
        result: DefensiveFireResult
    ) -> Tuple[int, int]:
        """
        Apply the results of defensive fire to the game state.
        
        Args:
            game_state: Game state to modify
            result: The defensive fire result
        
        Returns:
            The hex where the unit ended up (may be different from intended destination)
        """
        if result.target_disrupted:
            target_state = game_state.get_unit_state(result.target_id)
            if target_state:
                target_state.is_disrupted = True
        
        # Return the hex where unit stopped
        # If movement was stopped, they stop in the attack hex
        if result.movement_stopped:
            return result.attack_hex
        else:
            # Movement continues normally - return None to indicate no change
            return None


def demo_defensive_fire():
    """Demonstrate defensive fire mechanics"""
    import csv
    import os
    from board import Board
    from units import Unit
    from game_state import GameState, UnitState, GamePhase
    from abilities import AbilitySystem
    
    print("=" * 70)
    print("DEFENSIVE FIRE SYSTEM DEMONSTRATION")
    print("=" * 70)
    
    # Load abilities
    ability_file = 'Axis_and_Allies_Unit_Data_for_Analysis_-_Special_Abilities.csv'
    if not os.path.exists(ability_file):
        ability_file = 'Axis and Allies Unit Data for Analysis - Special_Abilities.csv'
    
    ability_system = AbilitySystem(ability_file)
    
    # Create board
    board = Board(10, 10)
    board.set_terrain(5, 5, 'forest')  # Add some cover
    
    # Create test units
    # Defender: German soldier at (5, 4)
    defender = Unit(
        name="Mauser Kar 98k",
        nation="Germany",
        unit_type="Soldier",
        year="1939",
        cost="4",
        defense="4",
        speed="1",
        veh_short="1", veh_medium="", veh_long="",
        per_short="6", per_medium="5", per_long="",
        abilities=""
    )
    defender.id = "defender_1"
    defender_state = UnitState(defender, (5, 4), "player1", 4)
    
    # Target: US soldier moving from (6, 4) to (5, 5)
    target = Unit(
        name="M1 Garand Rifleman",
        nation="USA",
        unit_type="Soldier",
        year="1941",
        cost="4",
        defense="4",
        speed="1",
        veh_short="1", veh_medium="", veh_long="",
        per_short="6", per_medium="4", per_long="",
        abilities=""
    )
    target.id = "target_1"
    target_state = UnitState(target, (6, 4), "player2", 4)
    
    # Create game state
    game_state = GameState(board, [defender_state], [target_state])
    game_state.current_phase = GamePhase.MOVEMENT
    
    # Create defensive fire system
    df_system = DefensiveFireSystem(ability_system, random_seed=42)
    
    print("\nScenario:")
    print(f"  Defender: {defender.name} at (5, 4)")
    print(f"  Target: {target.name} moving from (6, 4) to (5, 5)")
    print(f"  Target hex (5, 5) has forest cover")
    print()
    
    # Check for defensive fire opportunities
    opportunities = df_system.check_defensive_fire_triggered(
        game_state,
        "target_1",
        from_hex=(6, 4),
        to_hex=(5, 5)
    )
    
    print(f"Defensive fire opportunities found: {len(opportunities)}")
    
    for opp in opportunities:
        print(f"\n  {opp.defender_state.unit.name} can fire!")
        
        # Resolve the defensive fire
        result = df_system.resolve_defensive_fire(game_state, opp)
        
        print(f"  Dice rolled: {result.dice_rolled}")
        print(f"  Rolls: {result.rolls}")
        print(f"  Successes: {result.successes}")
        print(f"  Target defense: {result.target_defense}")
        print(f"  Hit: {result.hit}")
        if result.cover_roll:
            print(f"  Cover roll: {result.cover_roll} ({'SUCCESS' if result.cover_success else 'FAIL'})")
        print(f"  Result: {result.message}")
        
        # Apply result
        stopped_hex = df_system.apply_defensive_fire_result(game_state, result)
        if stopped_hex:
            print(f"  Unit stopped at: {stopped_hex}")
    
    # Test case 2: Soldier moving near a Vehicle (should NOT trigger)
    print("\n" + "=" * 70)
    print("Test Case 2: Soldier moving near Vehicle (should NOT trigger)")
    print("=" * 70)
    
    vehicle = Unit(
        name="Panzer IV",
        nation="Germany",
        unit_type="Vehicle",
        year="1941",
        cost="28",
        defense="5/4",
        speed="4",
        veh_short="9", veh_medium="8", veh_long="",
        per_short="6", per_medium="5", per_long="",
        abilities=""
    )
    vehicle.id = "vehicle_1"
    vehicle.defense_front = 5
    vehicle.defense_rear = 4
    vehicle_state = UnitState(vehicle, (3, 3), "player1", 5)
    
    us_soldier = Unit(
        name="BAR Gunner",
        nation="USA", 
        unit_type="Soldier",
        year="1941",
        cost="8",
        defense="4",
        speed="1",
        veh_short="1", veh_medium="", veh_long="",
        per_short="8", per_medium="6", per_long="",
        abilities=""
    )
    us_soldier.id = "us_soldier_1"
    us_soldier_state = UnitState(us_soldier, (4, 3), "player2", 4)
    
    game_state2 = GameState(board, [vehicle_state], [us_soldier_state])
    game_state2.current_phase = GamePhase.MOVEMENT
    
    df_system.reset_phase()
    
    opportunities2 = df_system.check_defensive_fire_triggered(
        game_state2,
        "us_soldier_1",
        from_hex=(4, 3),
        to_hex=(3, 3)  # Moving into vehicle's hex
    )
    
    print(f"\nSoldier moving from (4,3) to (3,3) [into Panzer IV hex]")
    print(f"Defensive fire opportunities: {len(opportunities2)}")
    print("(Should be 0 - Soldiers don't provoke defensive fire from Vehicles)")
    
    # Test case 3: Vehicle moving near Soldier (SHOULD trigger)
    print("\n" + "=" * 70)
    print("Test Case 3: Vehicle moving near Soldier (SHOULD trigger)")
    print("=" * 70)
    
    moving_tank = Unit(
        name="Sherman",
        nation="USA",
        unit_type="Vehicle",
        year="1942",
        cost="25",
        defense="5/4",
        speed="4",
        veh_short="8", veh_medium="7", veh_long="",
        per_short="6", per_medium="5", per_long="",
        abilities=""
    )
    moving_tank.id = "sherman_1"
    moving_tank.defense_front = 5
    moving_tank.defense_rear = 4
    sherman_state = UnitState(moving_tank, (7, 5), "player2", 5)
    
    german_at = Unit(
        name="Panzerfaust 30",
        nation="Germany",
        unit_type="Soldier",
        year="1943",
        cost="5",
        defense="3",
        speed="1",
        veh_short="10", veh_medium="", veh_long="",
        per_short="3", per_medium="", per_long="",
        abilities=""
    )
    german_at.id = "panzerfaust_1"
    german_at_state = UnitState(german_at, (6, 5), "player1", 3)
    
    game_state3 = GameState(board, [german_at_state], [sherman_state])
    game_state3.current_phase = GamePhase.MOVEMENT
    
    df_system.reset_phase()
    
    opportunities3 = df_system.check_defensive_fire_triggered(
        game_state3,
        "sherman_1",
        from_hex=(7, 5),
        to_hex=(6, 6)  # Moving to hex still adjacent to Panzerfaust
    )
    
    print(f"\nSherman moving from (7,5) to (6,6) [passing Panzerfaust at (6,5)]")
    print(f"Defensive fire opportunities: {len(opportunities3)}")
    
    for opp in opportunities3:
        print(f"\n  {opp.defender_state.unit.name} fires at Sherman!")
        result = df_system.resolve_defensive_fire(game_state3, opp)
        print(f"  Attack dice: {result.dice_rolled} (Panzerfaust has 10 vs vehicles at short range!)")
        print(f"  Rolls: {result.rolls}")
        print(f"  Successes: {result.successes} vs defense {result.target_defense}")
        print(f"  Result: {result.message}")
    
    print("\n" + "=" * 70)
    print("DEFENSIVE FIRE DEMONSTRATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    demo_defensive_fire()