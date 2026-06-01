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

from typing import List, Tuple, Optional, Dict, Set, TYPE_CHECKING
from dataclasses import dataclass
from enum import Enum

from game_state import GameState, UnitState
from board import Board
from dice import DiceSystem, UnitCategory

if TYPE_CHECKING:
    from units import Unit


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
    target_damaged: bool = False # For Double Shot: second hit on vehicle
    target_destroyed: bool = False  # For Double Shot: second hit on soldier (or already disrupted)
    movement_stopped: bool = False  # Did target have to stop moving?
    message: str = ""
    hits_applied: int = 0        # Number of hits that got through (for Double Shot)


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

    def check_antiair_defensive_fire(
        self,
        game_state: GameState,
        aircraft_id: str,
        aircraft_hex: Tuple[int, int]
    ) -> List['DefensiveFireOpportunity']:
        """
        Check for Antiair/Ace defensive fire opportunities when an Aircraft is placed.

        Antiair: Triggers when Aircraft placed in adjacent hex (distance 1)
        Ace: Triggers when Aircraft placed within 4 hexes

        Args:
            game_state: Current game state
            aircraft_id: ID of the placed Aircraft
            aircraft_hex: Hex where Aircraft is placed

        Returns:
            List of DefensiveFireOpportunity objects
        """
        opportunities = []

        aircraft_state = game_state.get_unit_state(aircraft_id)
        if not aircraft_state:
            return opportunities

        aircraft = aircraft_state.unit
        aircraft_owner = aircraft_state.owner

        # Get enemy units
        enemy_owner = "player2" if aircraft_owner == "player1" else "player1"
        enemy_units = game_state.get_units_by_owner(enemy_owner)

        def hex_distance(a: Tuple[int, int], b: Tuple[int, int]) -> int:
            q1, r1 = a
            q2, r2 = b
            return (abs(q1 - q2) + abs(q1 + r1 - q2 - r2) + abs(r1 - r2)) // 2

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            enemy = enemy_state.unit
            enemy_pos = enemy_state.position
            enemy_id = enemy.id

            # Disrupted units can't make defensive fire
            if enemy_state.is_disrupted:
                continue

            # Check abilities for Limited Ammo and Suppressive Fire
            enemy_abilities = getattr(enemy, 'abilities', []) or []

            # Limited Ammo: can't make defensive fire attacks
            has_limited_ammo = any(a.lower() == 'limited ammo' for a in enemy_abilities)
            if has_limited_ammo:
                continue

            # Each unit can only fire once per phase
            # Exception: Suppressive Fire allows unlimited defensive fire
            has_suppressive_fire = any(a.lower() == 'suppressive fire' for a in enemy_abilities)
            if enemy_id in self._units_fired_this_phase and not has_suppressive_fire:
                continue

            # Check for Antiair or Ace ability
            has_antiair = any(a.lower() == 'antiair' for a in enemy_abilities)
            has_ace = any(a.lower() == 'ace' for a in enemy_abilities)

            # Antiair Support: Gains Antiair when in same hex as friendly Antiair unit
            has_antiair_support = any(a.lower() == 'antiair support' for a in enemy_abilities)
            if has_antiair_support and not has_antiair:
                # Check if there's a friendly Antiair unit in same hex
                units_in_hex = game_state.get_units_at_position(enemy_pos[0], enemy_pos[1])
                for hex_unit_state in units_in_hex:
                    if hex_unit_state.owner == enemy_owner and hex_unit_state.unit.id != enemy_id:
                        hex_abilities = getattr(hex_unit_state.unit, 'abilities', []) or []
                        if any(a.lower() == 'antiair' for a in hex_abilities):
                            has_antiair = True
                            break

            if not has_antiair and not has_ace:
                continue

            distance = hex_distance(enemy_pos, aircraft_hex)

            # Antiair: adjacent (distance <= 1)
            # Ace: within 4 hexes (distance <= 4)
            can_fire = False
            if has_antiair and distance <= 1:
                can_fire = True
            if has_ace and distance <= 4:
                can_fire = True

            if not can_fire:
                continue

            # Check if enemy has attack value vs aircraft (use anti-Soldier values)
            if distance <= 1:
                attack_dice = enemy.per_short
            elif distance <= 4:
                attack_dice = enemy.per_medium
            else:
                attack_dice = enemy.per_long

            if attack_dice <= 0:
                continue

            opportunity = DefensiveFireOpportunity(
                defender_id=enemy_id,
                defender_state=enemy_state,
                target_id=aircraft_id,
                target_state=aircraft_state,
                from_hex=aircraft_hex,  # Aircraft placed here
                to_hex=aircraft_hex,    # Same hex (not moving)
                defender_pos=enemy_pos
            )
            opportunities.append(opportunity)

        return opportunities

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

            # Check for Limited Ammo - can't make defensive fire attacks
            enemy_abilities = getattr(enemy_unit, 'abilities', []) or []
            has_limited_ammo = any(a.lower() == 'limited ammo' for a in enemy_abilities)
            if has_limited_ammo:
                continue

            # Rule: Each unit can only fire defensively once per phase
            # Exception: Suppressive Fire allows unlimited defensive fire
            has_suppressive_fire = any(a.lower() == 'suppressive fire' for a in enemy_abilities)
            if enemy_id in self._units_fired_this_phase and not has_suppressive_fire:
                continue

            # Covering Fire: Units hit by Covering Fire can't make defensive fire this turn
            if enemy_state.covering_fire_target:
                continue
            
            # Check for special abilities
            has_awareness = any(a.lower() == 'awareness' for a in enemy_abilities)
            has_overlapping_fire = any(a.lower() == 'overlapping fire' for a in enemy_abilities)
            has_battlefield_awareness = any(a.lower() == 'battlefield awareness' for a in enemy_abilities)

            # Command Awareness: Vehicles within 2 hexes of Command Awareness unit gain Awareness
            if enemy_unit.unit_type == 'Vehicle' and not has_awareness:
                friendly_units = game_state.get_units_by_owner(enemy_state.owner)
                for friendly_state in friendly_units:
                    if not friendly_state.is_alive or friendly_state.unit.id == enemy_unit.id:
                        continue
                    friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []
                    if any(a.lower() == 'command awareness' for a in friendly_abilities):
                        friendly_pos = friendly_state.position
                        dist = self.board.hex_distance(
                            enemy_pos[0], enemy_pos[1], friendly_pos[0], friendly_pos[1]
                        ) if hasattr(self, 'board') else game_state.board.hex_distance(
                            enemy_pos[0], enemy_pos[1], friendly_pos[0], friendly_pos[1]
                        )
                        if dist <= 2:
                            has_awareness = True
                            break

            # Rule: Soldiers don't provoke defensive fire from Vehicles
            # Exception: Overlapping Fire allows Vehicles to fire on Soldiers
            if moving_is_soldier and enemy_unit.unit_type == 'Vehicle':
                if not has_overlapping_fire:
                    continue

            # Check adjacency
            adjacent_to_from = self.is_adjacent(enemy_pos, from_hex)
            adjacent_to_to = self.is_adjacent(enemy_pos, to_hex)
            entering_enemy_hex = (to_hex == enemy_pos)
            leaving_adjacent = adjacent_to_from and not adjacent_to_to

            # Determine if defensive fire is triggered
            can_fire = False

            # Standard rule: moving between two adjacent hexes
            if adjacent_to_from and adjacent_to_to:
                can_fire = True

            # Awareness: can attack Soldiers entering the unit's hex
            if has_awareness and moving_is_soldier and entering_enemy_hex:
                can_fire = True

            # Battlefield Awareness: can attack Vehicles moving OUT of adjacent hexes
            if has_battlefield_awareness and not moving_is_soldier and leaving_adjacent:
                can_fire = True

            if can_fire:
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
    ) -> Tuple[int, int]:
        """
        Calculate number of attack dice for defensive fire.

        Args:
            attacker: The unit making defensive fire
            target: The unit being shot at
            distance: Distance in hexes (0 = same hex, 1 = adjacent)
            attacker_state: State of attacking unit (for disruption check)

        Returns:
            Tuple of (number of dice, hit modifier for Gung Ho)
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

        # Quick Swivel: +1 attack die when making defensive-fire attacks
        hit_modifier = 0
        if self.ability_system:
            attacker_abilities = getattr(attacker, 'abilities', []) or []
            for ability in attacker_abilities:
                if ability.lower() == 'quick swivel':
                    dice += 1
                    break

            # Gung Ho: +1 on each attack die when making defensive-fire attacks
            for ability in attacker_abilities:
                if ability.lower() == 'gung ho':
                    hit_modifier = -1  # -1 means easier to hit (need 3+ instead of 4+)
                    break

        return dice, hit_modifier

    def check_stalwart_bonus(self, game_state, attacker_state: UnitState) -> bool:
        """
        Check if attacker has a friendly unit with Stalwart adjacent.
        Stalwart: Friendly Soldiers adjacent get +1 on each attack die for defensive fire.
        """
        if attacker_state.unit.unit_type != 'Soldier':
            return False

        aq, ar = attacker_state.position
        friendly_units = game_state.get_units_by_owner(attacker_state.owner)

        for friendly_state in friendly_units:
            if not friendly_state.is_alive:
                continue
            if friendly_state.unit.id == attacker_state.unit.id:
                continue

            # Check if adjacent (distance 1)
            fq, fr = friendly_state.position
            dist = game_state.board.hex_distance(aq, ar, fq, fr)
            if dist > 1:
                continue

            # Check for Stalwart ability
            friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []
            if any(a.lower() == 'stalwart' for a in friendly_abilities):
                return True

        return False
    
    def get_defense_value(
        self,
        target: 'Unit',
        target_state: UnitState,
        is_rear_attack: bool = False,
        game_state=None
    ) -> Tuple[int, List[str]]:
        """
        Get effective defense value for the target during defensive fire.

        Args:
            target: Target unit
            target_state: Target's current state
            is_rear_attack: Whether this is a rear attack (for vehicles)
            game_state: Game state (for checking adjacent auras)

        Returns:
            Tuple of (effective defense value, list of notes)
        """
        notes = []

        # Base defense
        if target.unit_type == 'Vehicle' and is_rear_attack:
            defense = target.defense_rear if target.defense_rear else target.defense_front
        else:
            defense = target.defense_front if target.defense_front else target.defense

        if defense is None:
            defense = 3  # Default

        # Mobility: +1/+1 defense against defensive-fire attacks
        if self.ability_system:
            target_abilities = getattr(target, 'abilities', []) or []
            for ability in target_abilities:
                if ability.lower() == 'mobility':
                    defense += 1
                    notes.append("Mobility: +1/+1 defense vs defensive fire")
                    break

        # Elan/Fearless: Friendly Soldiers adjacent get +1/+1 defense vs defensive fire
        if game_state and target.unit_type == 'Soldier':
            if self._has_adjacent_elan_fearless(game_state, target_state):
                defense += 1
                notes.append("Elan/Fearless: +1/+1 defense vs defensive fire")

        # Disrupted: -1 defense
        if target_state.is_disrupted:
            defense = max(1, defense - 1)

        # Damaged: -1 defense (doesn't stack with disrupted for penalty purposes,
        # but damaged is a separate state that also gives -1)
        if target_state.is_damaged:
            defense = max(1, defense - 1)

        return defense, notes

    def _has_adjacent_elan_fearless(self, game_state, target_state: UnitState) -> bool:
        """Check if target has adjacent friendly unit with Elan or Fearless."""
        tq, tr = target_state.position
        friendly_units = game_state.get_units_by_owner(target_state.owner)

        for friendly_state in friendly_units:
            if not friendly_state.is_alive:
                continue
            if friendly_state.unit.id == target_state.unit.id:
                continue

            fq, fr = friendly_state.position
            dist = game_state.board.hex_distance(tq, tr, fq, fr)
            if dist > 1:
                continue

            friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []
            for ability in friendly_abilities:
                if ability.lower() in ['elan', 'fearless']:
                    return True

        return False
    
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

        # Get attack dice (includes Quick Swivel bonus and Gung Ho modifier)
        num_dice, hit_modifier = self.get_attack_dice(defender, target, distance, defender_state)

        # Stalwart: Friendly Soldiers adjacent get +1 on each attack die for defensive fire
        if self.check_stalwart_bonus(game_state, defender_state):
            hit_modifier -= 1  # -1 means easier to hit

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
        
        # Check for Double Shot (makes two attack rolls during defensive fire)
        defender_abilities = getattr(defender, 'abilities', []) or []
        has_double_shot = any('double shot' in a.lower() for a in defender_abilities)

        # Roll attack (with Gung Ho hit modifier if applicable)
        attack_result = self.dice_system.roll_attack(
            num_dice=num_dice,
            is_disrupted=defender_state.is_disrupted,
            is_damaged=defender_state.is_damaged,
            ability_modifier=hit_modifier
        )

        # Double Shot: Make a second attack roll (both are resolved separately)
        attack_result_2 = None
        if has_double_shot:
            attack_result_2 = self.dice_system.roll_attack(
                num_dice=num_dice,
                is_disrupted=defender_state.is_disrupted,
                is_damaged=defender_state.is_damaged,
                ability_modifier=hit_modifier
            )

        # Get target defense
        # For vehicles, determine if this is front or rear based on movement direction
        # Rule: Vehicle faces toward hex it's entering during defensive fire
        is_rear = False
        if target.unit_type == 'Vehicle':
            # During defensive fire, vehicle faces toward destination hex
            # So attacks come from the front (defender shoots at front armor)
            # But the rule is: use facing toward hex it's entering
            from facing import calculate_facing_for_defensive_fire, is_front_arc_attack
            vehicle_facing = calculate_facing_for_defensive_fire(
                opportunity.from_hex, opportunity.to_hex
            )
            is_front = is_front_arc_attack(
                opportunity.defender_pos, attack_in_hex, vehicle_facing
            )
            is_rear = not is_front

        defense, defense_notes = self.get_defense_value(target, target_state, is_rear, game_state)

        # Check if target hex has cover
        hex_obj = game_state.board.hexes.get(attack_in_hex)
        terrain = hex_obj.terrain if hex_obj else 'open'
        has_cover = terrain in ['forest', 'town', 'hill', 'marsh', 'building']

        # Determine cover roll threshold based on unit type
        if target.unit_type == 'Vehicle':
            unit_category = UnitCategory.VEHICLE
        else:
            unit_category = UnitCategory.SOLDIER

        # Same hex gives -1 penalty to cover
        same_hex = (opportunity.defender_pos == attack_in_hex)

        # Resolve first attack
        hit_1 = attack_result.successes >= defense
        cover_roll = None
        cover_success_1 = False

        if hit_1 and has_cover:
            cover_result = self.dice_system.roll_cover_save(
                unit_category=unit_category,
                attacker_same_hex=same_hex
            )
            cover_roll = cover_result.roll
            cover_success_1 = cover_result.success

        # Resolve second attack (Double Shot)
        hit_2 = False
        cover_success_2 = False
        if attack_result_2 is not None:
            hit_2 = attack_result_2.successes >= defense
            if hit_2 and has_cover:
                cover_result_2 = self.dice_system.roll_cover_save(
                    unit_category=unit_category,
                    attacker_same_hex=same_hex
                )
                cover_success_2 = cover_result_2.success

        # Count total hits that got through (not negated by cover)
        hits_applied = 0
        if hit_1 and not cover_success_1:
            hits_applied += 1
        if hit_2 and not cover_success_2:
            hits_applied += 1

        # Determine final result based on hits applied
        # Each hit causes a disruption. Two disruptions:
        # - Soldier: destroyed (healthy->disrupted->destroyed)
        # - Vehicle: damaged (healthy->disrupted->damaged)
        target_disrupted = False
        target_damaged = False
        target_destroyed = False
        movement_stopped = False

        if hits_applied >= 1:
            movement_stopped = True
            if target_state.is_disrupted:
                # Already disrupted - next hit is worse
                if target.unit_type == 'Vehicle':
                    target_damaged = True
                else:
                    target_destroyed = True
            else:
                target_disrupted = True
                # Check for second hit (Double Shot)
                if hits_applied >= 2:
                    if target.unit_type == 'Vehicle':
                        target_damaged = True
                    else:
                        target_destroyed = True

        # Use first attack's cover roll for reporting (simplification)
        cover_success = cover_success_1
        
        # Build message with ability notes
        ability_notes_str = ""
        if defense_notes or hit_modifier != 0:
            notes_list = defense_notes.copy()
            if hit_modifier < 0:
                notes_list.append("Gung Ho: +1 on attack dice")
            if has_double_shot:
                notes_list.append("Double Shot")
            if notes_list:
                ability_notes_str = " [" + ", ".join(notes_list) + "]"

        # Build result message
        any_hit = hit_1 or hit_2
        if num_dice == 0:
            message = f"{defender.name} has no attack against {target.name}"
        elif hits_applied == 0:
            if not any_hit:
                message = (f"{defender.name} defensive fire vs {target.name}: "
                          f"{attack_result.successes} successes vs defense {defense} - MISS{ability_notes_str}")
            else:
                message = (f"{defender.name} defensive fire vs {target.name}: "
                          f"HIT but cover saves succeeded - NEGATED{ability_notes_str}")
        elif target_destroyed:
            message = (f"{defender.name} defensive fire vs {target.name}: "
                      f"{hits_applied} hits - DESTROYED! Movement stopped.{ability_notes_str}")
        elif target_damaged:
            message = (f"{defender.name} defensive fire vs {target.name}: "
                      f"{hits_applied} hits - DISRUPTED and DAMAGED! Movement stopped.{ability_notes_str}")
        else:
            message = (f"{defender.name} defensive fire vs {target.name}: "
                      f"{attack_result.successes} successes vs defense {defense} - "
                      f"DISRUPTED! Movement stopped.{ability_notes_str}")

        return DefensiveFireResult(
            defender_id=opportunity.defender_id,
            target_id=opportunity.target_id,
            attack_hex=attack_in_hex,
            dice_rolled=num_dice,
            rolls=attack_result.rolls,
            successes=attack_result.successes,
            target_defense=defense,
            hit=any_hit,
            cover_roll=cover_roll,
            cover_success=cover_success,
            target_disrupted=target_disrupted,
            target_damaged=target_damaged,
            target_destroyed=target_destroyed,
            movement_stopped=movement_stopped,
            message=message,
            hits_applied=hits_applied
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
        target_state = game_state.get_unit_state(result.target_id)
        if target_state:
            if result.target_destroyed:
                # Unit is destroyed - remove from game
                game_state.remove_unit(result.target_id)
            elif result.target_damaged:
                # Unit is disrupted AND damaged (Double Shot on vehicle)
                target_state.is_disrupted = True
                target_state.is_damaged = True
            elif result.target_disrupted:
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