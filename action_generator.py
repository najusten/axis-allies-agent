"""
Action Generator for Axis & Allies Miniatures

Generates all legal actions available to a player in a given game state.
This is the core of the AI decision-making process.
"""

from typing import List, Tuple, Set, Optional
from board import Board
from game_state import GameState, UnitState, GamePhase
from action import (
    Action, MoveAction, AttackAction, MoveAndAttackAction,
    UseAbilityAction, PassAction, EndPhaseAction, DeployAction,
    PlaceAircraftAction, BoardTransportAction, DismountTransportAction,
    ActionValidator, create_move_action, create_attack_action
)
from movement import MovementSystem
from combat import CombatSystem
from abilities import AbilitySystem
from facing import HexDirection, is_target_in_front_arc


class ActionGenerator:
    """
    Generates all legal actions for a player in the current game state.
    This is used for:
    - Human player action selection
    - AI decision making
    - Validation and verification
    """

    def __init__(self, movement_system: MovementSystem,
                 combat_system: CombatSystem,
                 ability_system: AbilitySystem):
        self.movement_system = movement_system
        self.combat_system = combat_system
        self.ability_system = ability_system

    def _get_strike_and_fade_speed(self, unit) -> int:
        """
        Get Strike and Fade speed for a unit.
        Returns 0 if unit doesn't have Strike and Fade.

        Strike and Fade ability format: "Strike and Fade X" where X is speed.
        """
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            ability_lower = ability.lower()
            if 'strike and fade' in ability_lower:
                # Extract speed number from ability name
                # Format: "Strike and Fade 2" or "Strike and Fade 3"
                import re
                match = re.search(r'strike and fade\s*(\d+)', ability_lower)
                if match:
                    return int(match.group(1))
                # If no number found, default to speed 1
                return 1
        return 0

    def _has_no_turret(self, unit) -> bool:
        """Check if a unit has the No Turret ability (can only attack in front arc)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if 'no turret' in ability.lower():
                return True
        return False

    def _has_indirect_fire(self, unit) -> bool:
        """Check if a unit has Indirect Fire ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if 'indirect fire' in ability.lower():
                return True
        return False

    def _has_improved_indirect_fire(self, unit) -> bool:
        """Check if a unit has Improved Indirect Fire ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if 'improved indirect fire' in ability.lower():
                return True
        return False

    def _has_spotter_ability(self, unit) -> bool:
        """Check if a unit has the Spotter ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'spotter':
                return True
        return False

    def _is_us_commander(self, unit) -> bool:
        """Check if a unit is a U.S. Commander."""
        abilities = getattr(unit, 'abilities', []) or []
        nationality = getattr(unit, 'nationality', '').lower()
        is_commander = any('commander abilities' in a.lower() for a in abilities)
        return is_commander and nationality == 'us'

    def _has_improvisation(self, unit) -> bool:
        """Check if a unit has Improvisation ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if 'improvisation' in ability.lower():
                return True
        return False

    def _has_friendly_spotter_for_target(self, game_state: 'GameState', owner: str,
                                         target_q: int, target_r: int,
                                         check_improved: bool = False) -> bool:
        """
        Check if there's a friendly Spotter within 8 hexes of target with LOS.
        Required for Indirect Fire to work.

        If check_improved=True, also checks for U.S. Commanders within 4 hexes (for Improved Indirect Fire).
        """
        friendly_units = game_state.get_units_by_owner(owner)
        for unit_state in friendly_units:
            if not unit_state.is_alive:
                continue

            spotter_q, spotter_r = unit_state.position
            distance = game_state.board.hex_distance(spotter_q, spotter_r, target_q, target_r)

            # Standard Spotter: within 8 hexes with LOS
            if self._has_spotter_ability(unit_state.unit):
                if distance <= 8:
                    has_los, _ = self.movement_system.has_line_of_sight(
                        game_state.board, unit_state.unit, spotter_q, spotter_r, target_q, target_r,
                        smoke_screens=game_state.smoke_screens
                    )
                    if has_los:
                        return True

            # Improved Indirect Fire: U.S. Commander within 4 hexes with LOS
            if check_improved and self._is_us_commander(unit_state.unit):
                if distance <= 4:
                    has_los, _ = self.movement_system.has_line_of_sight(
                        game_state.board, unit_state.unit, spotter_q, spotter_r, target_q, target_r,
                        smoke_screens=game_state.smoke_screens
                    )
                    if has_los:
                        return True

        return False

    def _has_antiair(self, unit) -> bool:
        """Check if a unit has native Antiair ability (can attack Aircraft)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            # Match "Antiair" but not "Antiair Support"
            if ability.lower() == 'antiair':
                return True
        return False

    def _has_antiair_support(self, unit) -> bool:
        """Check if a unit has Antiair Support ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if 'antiair support' in ability.lower():
                return True
        return False

    def _has_effective_antiair(self, game_state: GameState, unit_state: UnitState) -> bool:
        """
        Check if a unit effectively has Antiair ability.
        Either natively, or via Antiair Support (in same hex as friendly Antiair unit).
        """
        unit = unit_state.unit

        # Native Antiair
        if self._has_antiair(unit):
            return True

        # Antiair Support - check if in same hex as friendly Antiair unit
        if self._has_antiair_support(unit):
            units_at_pos = game_state.get_units_at_position(
                unit_state.position[0], unit_state.position[1]
            )
            for other_state in units_at_pos:
                # Must be friendly (same owner)
                if other_state.owner != unit_state.owner:
                    continue
                # Skip self
                if other_state.unit.id == unit.id:
                    continue
                # Check if the other unit has native Antiair
                if self._has_antiair(other_state.unit):
                    return True

        return False

    def _has_bombardment(self, unit) -> bool:
        """Check if a unit has Bombardment ability (ignores cover, can't attack Aircraft)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if 'bombardment' in ability.lower():
                return True
        return False

    def _has_bombs(self, unit) -> bool:
        """Check if a unit has Bombs ability (once per game, same hex attack)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'bombs':
                return True
        return False

    def _has_speed_boost(self, unit) -> bool:
        """Check if a unit has Speed Boost ability (once per game, attack vs Aircraft resolves immediately)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'speed boost':
                return True
        return False

    def _has_he_round(self, unit) -> bool:
        """Check if a unit has HE Round ability (once per game, 15 dice vs Soldier)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'he round':
                return True
        return False

    def _has_headshot(self, unit) -> bool:
        """Check if a unit has Headshot ability (once per game, 6 dice, 3+ successes = disrupt Vehicle)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'headshot':
                return True
        return False

    def _has_armor_piercing_rounds(self, unit) -> bool:
        """Check if a unit has Armor-Piercing Rounds (once per game, 2 hits = +1 hit vs Vehicle)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'armor-piercing rounds':
                return True
        return False

    def _has_firepower(self, unit) -> bool:
        """Check if a unit has Firepower (extra attack vs unit in same hex)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'firepower':
                return True
        return False

    def _has_remote_control(self, unit) -> bool:
        """Check if a unit has Remote Control ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'remote control':
                return True
        return False

    def _has_rocket_salvo(self, unit) -> bool:
        """Check if a unit has Rocket Salvo ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'rocket salvo':
                return True
        return False

    def _has_rockets_8(self, unit) -> bool:
        """Check if a unit has Rockets 8 ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'rockets 8':
                return True
        return False

    def _has_top_mounted_rockets(self, unit) -> bool:
        """Check if a unit has Top-Mounted Rockets ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'top-mounted rockets':
                return True
        return False

    def _has_additional_hull_mounted_cannon(self, unit) -> bool:
        """Check if a unit has Additional Hull-Mounted Cannon ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'additional hull-mounted cannon':
                return True
        return False

    def _has_extra_hull_mounted_cannon(self, unit) -> bool:
        """Check if a unit has Extra Hull-Mounted Cannon ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'extra hull-mounted cannon':
                return True
        return False

    def _get_hero_nationality(self, unit) -> Optional[str]:
        """
        Get the nationality requirement for a Hero unit.
        Returns the nationality string if unit has a Hero ability, None otherwise.
        """
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            ability_lower = ability.lower()
            if ability_lower == 'canadian hero':
                return 'Canada'
            elif ability_lower == 'german hero':
                return 'Germany'
            elif ability_lower == 'italian hero':
                return 'Italy'
            elif ability_lower == 'japanese hero':
                return 'Japan'
            elif ability_lower == 'soviet hero':
                return 'USSR'
            elif ability_lower == 'u.k. hero':
                return 'UK'
            elif ability_lower == 'u.s. hero':
                return 'US'
            elif ability_lower == 'hero':
                # Generic Hero - matches unit's own nationality
                return getattr(unit, 'nationality', None)
        return None

    def _has_hero_ability(self, unit) -> bool:
        """Check if unit has any Hero ability (ignores disruption)."""
        return self._get_hero_nationality(unit) is not None

    def _has_extra_machine_guns(self, unit) -> bool:
        """Check if a unit has Extra Machine Guns ability (extra attack vs Soldier in assault phase)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'extra machine guns':
                return True
        return False

    def _has_multiturreted(self, unit) -> bool:
        """Check if a unit has Multiturreted (two attacks with arc restrictions)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if 'multiturreted' in ability.lower():
                return True
        return False

    def _can_multiturreted_attack_arc(self, unit_state: UnitState, is_front_arc: bool) -> bool:
        """Check if a Multiturreted unit can attack in the given arc."""
        if is_front_arc:
            return not unit_state.multiturreted_front_used
        else:
            return not unit_state.multiturreted_rear_used

    def _has_rapid_fire(self, unit) -> bool:
        """Check if a unit has Rapid Fire (extra attack with jam risk)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'rapid fire':
                return True
        return False

    def _has_transport(self, unit) -> bool:
        """Check if a unit has Transport ability (can carry a soldier)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'transport':
                return True
        return False

    def _get_limited_range(self, unit) -> int:
        """
        Get Limited Range value for a unit.
        Limited Range X means the unit cannot attack beyond X hexes.
        Returns 0 if unit doesn't have Limited Range (no limit).
        """
        import re
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            match = re.match(r'Limited Range\s+(\d+)', ability, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 0  # No limit

    def _has_fixed_howitzer(self, unit) -> bool:
        """Check if a unit has Fixed Howitzer ability (can only attack units in front)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if 'fixed howitzer' in ability.lower():
                return True
        return False

    def _has_fixed_gun(self, unit) -> bool:
        """Check if a unit has Fixed Gun ability (can only attack Vehicles in front)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'fixed gun':
                return True
        return False

    def _has_fixed_rear_gun(self, unit) -> bool:
        """Check if a unit has Fixed Rear Gun ability (can only attack Vehicles in rear)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'fixed rear gun':
                return True
        return False

    def _has_turret_lock(self, unit) -> bool:
        """Check if a unit has Turret Lock ability (can't attack in hill hex)."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'turret lock':
                return True
        return False

    def _get_minimum_range(self, unit) -> int:
        """
        Get minimum attack range for a unit.
        Returns 0 if no minimum range restriction (most units).
        """
        # Could be used for future abilities that require minimum range
        return 0

    def _get_extended_range(self, unit) -> int:
        """
        Get Extended Range value for a unit.
        Extended Range X means long range against Vehicles is 5-X hexes.
        Returns 0 if unit doesn't have Extended Range.
        """
        import re
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            match = re.match(r'Extended Range\s+(\d+)', ability, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 0

    def _get_enhanced_range(self, unit) -> int:
        """
        Get Enhanced Range value for a unit.
        Enhanced Range X means long range is 5-X hexes (for ALL targets).
        Returns 0 if unit doesn't have Enhanced Range.
        """
        import re
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            match = re.match(r'Enhanced Range\s+(\d+)', ability, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 0

    def _get_hand_to_hand(self, unit) -> int:
        """
        Get Hand to Hand value for a unit.
        Hand to Hand X gives attack value X against Soldiers in same hex.
        Returns 0 if unit doesn't have Hand to Hand.
        """
        import re
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            match = re.match(r'Hand to Hand\s+(\d+)', ability, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 0

    def _get_relocate_speed(self, unit) -> int:
        """
        Get Relocate speed for a unit.
        Relocate X means unit has speed X during assault phase.
        Returns 0 if unit doesn't have Relocate.
        """
        import re
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            match = re.match(r'Relocate\s+(\d+)', ability, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 0

    def _get_unit_max_range(self, unit, target_type: str) -> int:
        """
        Calculate maximum range a unit can attack based on its attack values.

        Args:
            unit: The attacking unit
            target_type: 'Soldier' or 'Vehicle'

        Returns:
            Maximum range in hexes (0-1 for short, 2-4 for medium, 5-8 for long)
            Extended Range X increases long range against Vehicles to 5-X hexes.
        """
        # Determine which attack values to check based on target type
        if target_type == 'Soldier':
            short_val = getattr(unit, 'per_short', 0)
            medium_val = getattr(unit, 'per_medium', 0)
            long_val = getattr(unit, 'per_long', 0)
        else:  # Vehicle
            short_val = getattr(unit, 'veh_short', 0)
            medium_val = getattr(unit, 'veh_medium', 0)
            long_val = getattr(unit, 'veh_long', 0)

        # Determine max range based on non-zero attack values
        # Long range = 5-8 hexes (or Extended/Enhanced Range X)
        if long_val > 0:
            base_long_range = 8

            # Check for Enhanced Range (applies to ALL targets)
            enhanced = self._get_enhanced_range(unit)
            if enhanced > 0:
                return enhanced

            # Check for Extended Range (only applies to Vehicles)
            if 'Vehicle' in target_type:
                extended = self._get_extended_range(unit)
                if extended > 0:
                    return extended  # Extended Range X means max range is X

            max_range = base_long_range
        # Medium range = 2-4 hexes
        elif medium_val > 0:
            max_range = 4
        # Short range = 0-1 hexes
        elif short_val > 0:
            max_range = 1
        # Can't attack this target type
        else:
            return 0

        # Apply Limited Range restriction
        limited = self._get_limited_range(unit)
        if limited > 0:
            max_range = min(max_range, limited)

        return max_range
    
    def get_all_legal_actions(self, game_state: GameState, player: str) -> List[Action]:
        """
        Get ALL legal actions available to a player in the current game state.
        This is the master function for generating the complete action space.
        """
        actions = []
        
        # Get all units owned by this player
        player_units = game_state.get_units_by_owner(player)
        
        # Generate actions based on current phase
        if game_state.current_phase == GamePhase.MOVEMENT:
            actions.extend(self._get_movement_phase_actions(game_state, player_units))

        elif game_state.current_phase == GamePhase.FLIGHT:
            actions.extend(self._get_flight_phase_actions(game_state, player_units))

        elif game_state.current_phase == GamePhase.ASSAULT:
            actions.extend(self._get_assault_phase_actions(game_state, player_units))

        elif game_state.current_phase == GamePhase.AIRSTRIKE:
            actions.extend(self._get_airstrike_phase_actions(game_state, player_units))

        # Always allow ending the phase
        actions.append(EndPhaseAction(game_state.current_phase))
        
        # Always allow passing
        actions.append(PassAction(player))
        
        return actions
    
    def _get_movement_phase_actions(self, game_state: GameState, 
                                    player_units: List[UnitState]) -> List[Action]:
        """Generate all legal actions during the movement phase"""
        actions = []
        
        # Paratrooper deployment actions for undeployed units
        actions.extend(self._get_paratrooper_deploy_actions(game_state, player_units))

        # Hero deployment actions for undeployed Hero units
        actions.extend(self._get_hero_deploy_actions(game_state, player_units))

        for unit_state in player_units:
            if not unit_state.is_alive:
                continue

            # Skip undeployed units (they can only be deployed, not moved)
            if not unit_state.is_deployed:
                continue

            unit = unit_state.unit
            q, r = unit_state.position

            # Skip if already moved
            if unit_state.has_moved:
                continue

            unit_abilities = getattr(unit, 'abilities', []) or []

            # Skip if Dug In (can't move)
            has_dug_in = any(a.lower() == 'dug in' for a in unit_abilities)
            if has_dug_in:
                continue

            # Skip if Prone to Breakdown and damaged (can't move)
            has_prone_to_breakdown = any(a.lower() == 'prone to breakdown' for a in unit_abilities)
            if has_prone_to_breakdown and unit_state.is_damaged:
                continue

            # Command Dependent: Can't move unless starting adjacent to a friendly Commander
            has_command_dependent = any(a.lower() == 'command dependent' for a in unit_abilities)
            if has_command_dependent:
                # Check for adjacent friendly Commander
                has_adjacent_commander = False
                friendly_units = game_state.get_units_by_owner(unit_state.owner)
                for friendly_state in friendly_units:
                    if not friendly_state.is_alive or friendly_state.unit.id == unit.id:
                        continue
                    friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []
                    is_commander = any('commander abilities' in a.lower() for a in friendly_abilities)
                    if not is_commander:
                        continue
                    # Check if adjacent
                    friendly_pos = friendly_state.position
                    dist = game_state.board.hex_distance(q, r, friendly_pos[0], friendly_pos[1])
                    if dist <= 1:
                        has_adjacent_commander = True
                        break
                if not has_adjacent_commander:
                    continue

            # Skip if disrupted (disrupted units can't move) unless they have special abilities
            if unit_state.is_disrupted:
                # Check for abilities that allow movement while disrupted
                has_robust = any(a.lower() == 'robust' for a in unit_abilities)
                has_hardened_veteran = any(a.lower() == 'hardened veteran' for a in unit_abilities)
                has_ss_determination = any(a.lower() == 'ss determination' for a in unit_abilities)
                has_courage = any(a.lower() == 'courage' for a in unit_abilities)
                has_veteran_crew = any(a.lower() == 'veteran crew' for a in unit_abilities)
                has_veteran_guard = any(a.lower() == 'veteran guard' for a in unit_abilities)
                # Charge: Can move while disrupted if moving closer to an enemy Soldier
                has_charge = any(a.lower() == 'charge' for a in unit_abilities)
                # Hero: Ignores face-up Disrupted counters
                has_hero = self._has_hero_ability(unit)

                can_move_disrupted = (has_robust or has_hardened_veteran or has_ss_determination
                                      or has_courage or has_veteran_crew or has_veteran_guard
                                      or has_charge or has_hero)
                if not can_move_disrupted:
                    continue

            # Calculate bonus speed from Tally-Ho! (non-Artillery Soldiers adjacent to Tally-Ho! get +1 speed)
            bonus_speed = 0
            is_non_artillery_soldier = (
                unit.unit_type == 'Soldier' and
                not any('artillery' in a.lower() for a in unit_abilities)
            )
            if is_non_artillery_soldier:
                friendly_units = game_state.get_units_by_owner(unit_state.owner)
                for friendly_state in friendly_units:
                    if not friendly_state.is_alive or friendly_state.unit.id == unit.id:
                        continue
                    friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []
                    has_tally_ho = any(a.lower() == 'tally-ho!' for a in friendly_abilities)
                    if has_tally_ho:
                        friendly_pos = friendly_state.position
                        dist = game_state.board.hex_distance(q, r, friendly_pos[0], friendly_pos[1])
                        if dist <= 1:  # Adjacent
                            bonus_speed = 1
                            break

            # Extra Fuel: Friendly Vehicles with base speed 3+ get +1 speed (not cumulative)
            if unit.unit_type == 'Vehicle':
                base_speed = getattr(unit, 'speed', 2)
                if base_speed >= 3 and bonus_speed == 0:  # Not already getting Tally-Ho bonus
                    friendly_units = game_state.get_units_by_owner(unit_state.owner)
                    for friendly_state in friendly_units:
                        if not friendly_state.is_alive:
                            continue
                        friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []
                        has_extra_fuel = any(a.lower() == 'extra fuel' for a in friendly_abilities)
                        if has_extra_fuel:
                            bonus_speed = 1
                            break  # Not cumulative

            # Heavy Rifle: While adjacent to a friendly Soldier with base speed 1, this unit gets speed 1
            has_heavy_rifle = any(a.lower() == 'heavy rifle' for a in unit_abilities)
            heavy_rifle_speed = 0
            if has_heavy_rifle:
                friendly_units = game_state.get_units_by_owner(unit_state.owner)
                for friendly_state in friendly_units:
                    if not friendly_state.is_alive or friendly_state.unit.id == unit.id:
                        continue
                    friendly_unit = friendly_state.unit
                    if friendly_unit.unit_type != 'Soldier':
                        continue
                    friendly_base_speed = getattr(friendly_unit, 'speed', 0)
                    if friendly_base_speed == 1:
                        friendly_pos = friendly_state.position
                        dist = game_state.board.hex_distance(q, r, friendly_pos[0], friendly_pos[1])
                        if dist <= 1:  # Adjacent
                            heavy_rifle_speed = 1
                            break

            # Get all reachable hexes (with Tally-Ho!/Extra Fuel/Heavy Rifle bonus if applicable)
            base_speed = getattr(unit, 'speed', 2)
            # Heavy Rifle: If base speed is 0 and adjacent to speed-1 Soldier, use speed 1
            if heavy_rifle_speed > 0 and base_speed == 0:
                base_speed = heavy_rifle_speed
            if bonus_speed > 0:
                reachable = self.movement_system.get_reachable_hexes(
                    game_state.board, q, r, unit, max_speed=base_speed + bonus_speed
                )
            else:
                reachable = self.movement_system.get_reachable_hexes(
                    game_state.board, q, r, unit
                )

            # High Gear: If unit has High Gear, also calculate road-only moves with bonus
            movement_mods = self.ability_system.get_movement_modifiers(unit)
            high_gear_bonus = movement_mods.get('high_gear_bonus', 0)
            if high_gear_bonus > 0:
                high_gear_reachable = self.movement_system.get_reachable_hexes(
                    game_state.board, q, r, unit,
                    max_speed=base_speed + bonus_speed + high_gear_bonus,
                    road_only=True
                )
                reachable = reachable.union(high_gear_reachable)
            
            # Create a move action for each reachable hex
            for (dest_q, dest_r) in reachable:
                # Skip current position
                if (dest_q, dest_r) == (q, r):
                    continue
                
                # Create move action
                move_action = MoveAction(
                    unit_id=unit.id,
                    from_q=q,
                    from_r=r,
                    to_q=dest_q,
                    to_r=dest_r,
                    path=[(q, r), (dest_q, dest_r)],  # Simplified path
                    movement_cost=0  # Would calculate actual cost
                )
                
                actions.append(move_action)

            # Transport actions: Board or Dismount instead of moving
            if unit.unit_type == 'Soldier' and not unit_state.has_moved:
                # Check if being carried - can dismount
                if unit_state.carried_by_id:
                    transport_state = game_state.get_unit_state(unit_state.carried_by_id)
                    if transport_state:
                        # Can dismount to same hex as transport
                        actions.append(DismountTransportAction(
                            unit_id=unit.id,
                            transport_id=unit_state.carried_by_id,
                            to_q=transport_state.position[0],
                            to_r=transport_state.position[1]
                        ))
                else:
                    # Not being carried - can board a transport in same hex
                    units_in_hex = game_state.get_units_at_position(q, r)
                    for other_state in units_in_hex:
                        if other_state.owner != unit_state.owner:
                            continue  # Must be friendly
                        if other_state.unit.id == unit.id:
                            continue  # Skip self
                        if self._has_transport(other_state.unit):
                            # Check if transport is not already carrying someone
                            if other_state.carried_unit_id is None:
                                actions.append(BoardTransportAction(
                                    unit_id=unit.id,
                                    transport_id=other_state.unit.id,
                                    position_q=q,
                                    position_r=r
                                ))

        # Mechanized Tactics: Soldiers with this ability can dismount from a
        # transport even after the transport has moved this phase.
        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            unit = unit_state.unit
            if unit.unit_type != 'Soldier':
                continue
            if not unit_state.carried_by_id:
                continue
            unit_abilities = getattr(unit, 'abilities', []) or []
            has_mech_tactics = any(
                a.lower() == 'mechanized tactics' for a in unit_abilities
            )
            if not has_mech_tactics:
                continue
            # Already has a dismount action from normal generation? Skip duplicate
            has_dismount = any(
                isinstance(a, DismountTransportAction) and a.unit_id == unit.id
                for a in actions
            )
            if has_dismount:
                continue
            transport_state = game_state.get_unit_state(unit_state.carried_by_id)
            if transport_state:
                actions.append(DismountTransportAction(
                    unit_id=unit.id,
                    transport_id=unit_state.carried_by_id,
                    to_q=transport_state.position[0],
                    to_r=transport_state.position[1]
                ))

        # Generate ability actions available during movement phase (e.g., Smoke Screen)
        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            unit = unit_state.unit
            if unit.abilities:
                actions.extend(self._get_ability_actions(
                    game_state, unit, unit_state
                ))

        return actions

    def _get_flight_phase_actions(self, game_state: GameState,
                                  player_units: List[UnitState]) -> List[Action]:
        """Generate all legal actions during the flight phase (Aircraft placement)"""
        actions = []

        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            # Only Aircraft can be placed during flight phase
            if unit_state.unit.unit_type != 'Aircraft':
                continue
            # Skip if already on map
            if unit_state.is_aircraft_on_map:
                continue

            # Aircraft can be placed in any hex on the map
            for q in range(game_state.board.width):
                for r in range(game_state.board.height):
                    hex_tile = game_state.board.get_hex(q, r)
                    if not hex_tile:
                        continue
                    # Aircraft ignore terrain restrictions

                    actions.append(PlaceAircraftAction(
                        unit_id=unit_state.unit.id,
                        to_q=q,
                        to_r=r
                    ))

        return actions

    def _get_airstrike_phase_actions(self, game_state: GameState,
                                     player_units: List[UnitState]) -> List[Action]:
        """Generate all legal actions during the airstrike phase (Aircraft attacks)"""
        actions = []

        # Get enemy units
        enemy_owner = "player2" if game_state.active_player == "player1" else "player1"
        enemy_units = game_state.get_units_by_owner(enemy_owner)

        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            # Only Aircraft that are on the map can attack
            if unit_state.unit.unit_type != 'Aircraft':
                continue
            if not unit_state.is_aircraft_on_map:
                continue
            # Check if already attacked
            if not game_state.can_unit_attack(unit_state.unit.id):
                continue

            unit = unit_state.unit
            q, r = unit_state.position

            # Generate attack actions for Aircraft
            actions.extend(self._get_attack_actions(
                game_state, unit, unit_state, (q, r), enemy_units
            ))

        return actions

    def _get_assault_phase_actions(self, game_state: GameState,
                                   player_units: List[UnitState]) -> List[Action]:
        """Generate all legal actions during the assault phase"""
        actions = []
        
        # Get enemy units
        enemy_owner = "player2" if game_state.active_player == "player1" else "player1"
        enemy_units = game_state.get_units_by_owner(enemy_owner)
        
        for unit_state in player_units:
            if not unit_state.is_alive:
                continue

            # Skip undeployed units (Paratroopers not yet on the map)
            if not unit_state.is_deployed:
                continue

            # Skip Aircraft (they attack in Airstrike phase, not Assault phase)
            if unit_state.unit.unit_type == 'Aircraft':
                continue

            unit = unit_state.unit
            q, r = unit_state.position

            # Generate attack actions (uses can_unit_attack for Double Shot support)
            if game_state.can_unit_attack(unit.id):
                actions.extend(self._get_attack_actions(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))
            
            # Generate move-and-attack actions (for units with special abilities)
            if self.movement_system.can_unit_move_and_attack(unit):
                actions.extend(self._get_move_and_attack_actions(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))
            
            # Generate ability actions
            actions.extend(self._get_ability_actions(
                game_state, unit, unit_state
            ))

            # Generate Strike and Fade movement (available after attacking)
            if unit_state.strike_and_fade_available:
                actions.extend(self._get_strike_and_fade_moves(
                    game_state, unit, unit_state
                ))

            # Generate Relocate movement (can move during assault phase)
            relocate_speed = self._get_relocate_speed(unit)
            if relocate_speed > 0 and not unit_state.has_moved:
                actions.extend(self._get_relocate_moves(
                    game_state, unit, unit_state, relocate_speed
                ))

            # Generate All Guns Blazing attack (extra attack vs Soldier after attacking)
            if unit_state.all_guns_blazing_available:
                actions.extend(self._get_all_guns_blazing_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Strafe attack (attack Soldier adjacent to original target)
            if unit_state.strafe_available:
                actions.extend(self._get_strafe_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Bombs attack (once per game, same hex only)
            if self._has_bombs(unit) and not unit_state.bombs_used:
                actions.extend(self._get_bombs_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Speed Boost attacks vs Aircraft (once per game)
            if self._has_speed_boost(unit) and not unit_state.speed_boost_used:
                actions.extend(self._get_speed_boost_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Extra Machine Guns attacks (once per turn, vs Soldier)
            if self._has_extra_machine_guns(unit) and not unit_state.extra_mg_used:
                actions.extend(self._get_extra_mg_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Rapid Fire attacks (once per turn, extra attack with jam risk)
            if self._has_rapid_fire(unit) and not unit_state.rapid_fire_used:
                actions.extend(self._get_rapid_fire_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate HE Round attacks (once per game, 15 dice vs Soldier)
            if self._has_he_round(unit) and not unit_state.he_round_used:
                actions.extend(self._get_he_round_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Headshot attacks (once per game, 6 dice, 3+ successes = disrupt Vehicle)
            if self._has_headshot(unit) and not unit_state.headshot_used:
                actions.extend(self._get_headshot_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Armor-Piercing Rounds attacks (once per game, 2 hits = +1 hit vs Vehicle)
            if self._has_armor_piercing_rounds(unit) and not unit_state.armor_piercing_used:
                actions.extend(self._get_armor_piercing_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Firepower attacks (extra attack vs unit in same hex)
            if self._has_firepower(unit) and not unit_state.firepower_used:
                actions.extend(self._get_firepower_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Remote Control attacks (once per game, roll 2 dice vs range then attack)
            if self._has_remote_control(unit) and not unit_state.remote_control_used:
                actions.extend(self._get_remote_control_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Rocket Salvo attacks (once per game, area attack vs target and adjacent units)
            if self._has_rocket_salvo(unit) and not unit_state.rocket_salvo_used:
                actions.extend(self._get_rocket_salvo_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Rockets 8 attacks (once per game, 8 dice vs target within 4 hexes)
            if self._has_rockets_8(unit) and not unit_state.rockets_8_used:
                actions.extend(self._get_rockets_8_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Top-Mounted Rockets attacks (once per game, area attack)
            if self._has_top_mounted_rockets(unit) and not unit_state.top_mounted_rockets_used:
                actions.extend(self._get_top_mounted_rockets_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))

            # Generate Additional Hull-Mounted Cannon attacks (once per turn, 12/10/8 vs Vehicle in front)
            if self._has_additional_hull_mounted_cannon(unit) and not unit_state.additional_hull_cannon_used:
                actions.extend(self._get_hull_cannon_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units,
                    is_additional=True
                ))

            # Generate Extra Hull-Mounted Cannon attacks (once per turn, 8/7/5 vs Vehicle/Soldier in front)
            if self._has_extra_hull_mounted_cannon(unit) and not unit_state.extra_hull_cannon_used:
                actions.extend(self._get_hull_cannon_attacks(
                    game_state, unit, unit_state, (q, r), enemy_units,
                    is_additional=False
                ))

            # Generate facing change actions (Command Quick Reactions)
            if unit.unit_type == 'Vehicle' and unit_state.quick_reactions_available:
                actions.extend(self._get_facing_change_actions(unit, unit_state))

            # Generate Command Demolition actions (Soldiers adjacent to Command Demolition unit)
            if unit.unit_type == 'Soldier':
                actions.extend(self._get_command_demolition_actions(
                    game_state, unit, unit_state
                ))

            # Generate Bridge Demolition actions (units with Bridge Demolition ability)
            actions.extend(self._get_bridge_demolition_actions(
                game_state, unit, unit_state
            ))

        # Generate Angriff actions (adjacent Soldiers can move into enemy hex and attack with +1)
        actions.extend(self._get_angriff_actions(game_state, player_units, enemy_units))

        # Generate Banzai Charge actions (adjacent Soldiers can move into enemy hex and attack with +1)
        actions.extend(self._get_banzai_charge_actions(game_state, player_units, enemy_units))

        return actions

    def _has_paratrooper(self, unit) -> bool:
        """Check if unit has Paratrooper ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if ability.lower() == 'paratrooper':
                return True
        return False

    def _has_command_demolition_aura(self, game_state: GameState, unit_state: UnitState) -> bool:
        """Check if unit has adjacent friendly unit with Command Demolition ability."""
        if unit_state.unit.unit_type != 'Soldier':
            return False  # Only Soldiers benefit from Command Demolition
        friendly_units = game_state.get_units_by_owner(unit_state.owner)
        unit_pos = unit_state.position
        for friendly_state in friendly_units:
            if not friendly_state.is_alive or friendly_state.unit.id == unit_state.unit.id:
                continue
            friendly_abilities = getattr(friendly_state.unit, 'abilities', []) or []
            if any(a.lower() == 'command demolition' for a in friendly_abilities):
                friendly_pos = friendly_state.position
                dist = game_state.board.hex_distance(
                    unit_pos[0], unit_pos[1], friendly_pos[0], friendly_pos[1]
                )
                if dist <= 1:  # Adjacent
                    return True
        return False

    def _get_command_demolition_actions(self, game_state: GameState,
                                         unit, unit_state: UnitState) -> List[UseAbilityAction]:
        """
        Generate demolition actions for Soldiers adjacent to a unit with Command Demolition.
        These can be used instead of moving or attacking in assault phase.
        """
        actions = []

        # Only in assault phase
        if game_state.current_phase != GamePhase.ASSAULT:
            return actions

        # Check if unit has the aura and hasn't already attacked or moved this turn
        if not self._has_command_demolition_aura(game_state, unit_state):
            return actions

        # Check if unit hasn't used its attack (demolition is instead of attacking)
        if unit_state.attacks_this_turn > 0:
            return actions

        q, r = unit_state.position

        # Find obstacles in the same hex
        for other_state in game_state.get_units_at_position(q, r):
            if other_state.unit.unit_type == 'Obstacle':
                ability_action = UseAbilityAction(
                    unit_id=unit.id,
                    ability_name='command_demolition',
                    target_id=other_state.unit.id,
                    target_q=q,
                    target_r=r
                )
                actions.append(ability_action)

        return actions

    def _has_bridge_demolition(self, unit) -> bool:
        """Check if a unit has Bridge Demolition ability."""
        abilities = getattr(unit, 'abilities', []) or []
        for ability in abilities:
            if 'bridge demolition' in ability.lower():
                return True
        return False

    def _get_bridge_demolition_actions(self, game_state: GameState,
                                       unit, unit_state: UnitState) -> List[UseAbilityAction]:
        """
        Generate Bridge Demolition actions for units with the ability.
        Can destroy bridges or obstacles in the unit's hex instead of moving or attacking.
        """
        actions = []

        # Only in assault phase
        if game_state.current_phase != GamePhase.ASSAULT:
            return actions

        # Unit must have Bridge Demolition ability
        if not self._has_bridge_demolition(unit):
            return actions

        # Check if unit hasn't used its attack (demolition is instead of attacking)
        if unit_state.attacks_this_turn > 0:
            return actions

        q, r = unit_state.position

        # Find obstacles in the same hex
        for other_state in game_state.get_units_at_position(q, r):
            if other_state.unit.unit_type == 'Obstacle':
                ability_action = UseAbilityAction(
                    unit_id=unit.id,
                    ability_name='bridge_demolition',
                    target_id=other_state.unit.id,
                    target_q=q,
                    target_r=r
                )
                actions.append(ability_action)

        # Also allow destroying edge obstacles (bridges/barbed wire) at hex edges
        for dq, dr in [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]:
            adj_q, adj_r = q + dq, r + dr
            edge_obstacle = game_state.board.get_edge_obstacle(q, r, adj_q, adj_r)
            if edge_obstacle:
                ability_action = UseAbilityAction(
                    unit_id=unit.id,
                    ability_name='bridge_demolition',
                    target_q=q,
                    target_r=r,
                    parameters={'edge_obstacle': (adj_q, adj_r), 'obstacle_type': edge_obstacle}
                )
                actions.append(ability_action)

        return actions

    def _get_paratrooper_deploy_actions(self, game_state: GameState,
                                        player_units: List[UnitState]) -> List[DeployAction]:
        """
        Generate deploy actions for undeployed Paratrooper units.
        Paratroopers can be deployed at end of movement phase to any hex
        not adjacent to enemy units.
        """
        actions = []

        # Get enemy positions for adjacency check
        enemy_owner = "player2" if player_units and player_units[0].owner == "player1" else "player1"
        enemy_units = game_state.get_units_by_owner(enemy_owner)
        enemy_adjacent_hexes = set()
        for enemy_state in enemy_units:
            if enemy_state.is_alive:
                eq, er = enemy_state.position
                enemy_adjacent_hexes.add((eq, er))  # Enemy's own hex
                # Add all adjacent hexes
                for neighbor in game_state.board.get_neighbors(eq, er):
                    enemy_adjacent_hexes.add((neighbor.q, neighbor.r))

        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            if unit_state.is_deployed:
                continue  # Already deployed
            if not self._has_paratrooper(unit_state.unit):
                continue

            # Generate deploy action for each valid hex
            for q in range(game_state.board.width):
                for r in range(game_state.board.height):
                    hex_tile = game_state.board.get_hex(q, r)
                    if not hex_tile:
                        continue
                    # Can't deploy in impassable terrain
                    if hex_tile.terrain == 'impassable':
                        continue
                    # Can't deploy adjacent to enemies
                    if (q, r) in enemy_adjacent_hexes:
                        continue
                    # Can't deploy on occupied hex
                    if hex_tile.unit is not None:
                        continue

                    actions.append(DeployAction(
                        unit_id=unit_state.unit.id,
                        to_q=q,
                        to_r=r
                    ))

        return actions

    def _get_hero_deploy_actions(self, game_state: GameState,
                                  player_units: List[UnitState]) -> List[DeployAction]:
        """
        Generate deploy actions for undeployed Hero units.
        Heroes deploy in hexes containing a friendly Soldier of matching nationality.
        """
        actions = []

        # Build a mapping of positions with friendly soldiers by nationality
        soldier_positions_by_nationality = {}
        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            if not unit_state.is_deployed:
                continue
            if unit_state.unit.unit_type != 'Soldier':
                continue
            nationality = getattr(unit_state.unit, 'nationality', None)
            if nationality:
                if nationality not in soldier_positions_by_nationality:
                    soldier_positions_by_nationality[nationality] = set()
                soldier_positions_by_nationality[nationality].add(unit_state.position)

        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            if unit_state.is_deployed:
                continue  # Already deployed

            hero_nationality = self._get_hero_nationality(unit_state.unit)
            if not hero_nationality:
                continue  # Not a Hero unit

            # Get positions with matching nationality Soldiers
            valid_positions = soldier_positions_by_nationality.get(hero_nationality, set())

            for pos in valid_positions:
                actions.append(DeployAction(
                    unit_id=unit_state.unit.id,
                    to_q=pos[0],
                    to_r=pos[1]
                ))

        return actions

    def _get_strafe_attacks(self, game_state: GameState, unit,
                            unit_state: UnitState, position: Tuple[int, int],
                            enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate Strafe attack actions.
        Strafe attacks target Soldiers adjacent to the original target hex.
        """
        actions = []
        q, r = position

        # Need the original target hex
        target_hex = unit_state.strafe_target_hex
        if not target_hex:
            return actions

        # Get hexes adjacent to the original target
        adjacent_hexes = game_state.board.get_neighbors(target_hex[0], target_hex[1])
        adjacent_positions = set((h.q, h.r) for h in adjacent_hexes)

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # Strafe only targets Soldiers
            if enemy_state.unit.unit_type != 'Soldier':
                continue

            # Must be adjacent to original target hex
            enemy_pos = enemy_state.position
            if enemy_pos not in adjacent_positions:
                continue

            enemy_q, enemy_r = enemy_pos

            # Check range and LOS from attacker position
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)
            max_range = self._get_unit_max_range(unit, 'Soldier')

            if distance > max_range:
                continue

            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), enemy_pos
            )
            if not has_los:
                continue

            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            attack_action.is_strafe = True

            actions.append(attack_action)

        return actions

    def _get_rapid_fire_attacks(self, game_state: GameState, unit,
                                 unit_state: UnitState, position: Tuple[int, int],
                                 enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate Rapid Fire attack actions.
        Rapid Fire: Extra attack (any target), but if any 1s rolled, unit is disrupted (sticky).
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            enemy_q, enemy_r = enemy_state.position
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)

            # Check range
            max_range = self._get_unit_max_range(unit, enemy_state.unit.unit_type)
            if distance > max_range or max_range == 0:
                continue

            # Check LOS
            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), (enemy_q, enemy_r)
            )
            if not has_los:
                continue

            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            # Mark this as a Rapid Fire attack
            attack_action.is_rapid_fire = True

            actions.append(attack_action)

        return actions

    def _get_extra_mg_attacks(self, game_state: GameState, unit,
                              unit_state: UnitState, position: Tuple[int, int],
                              enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate Extra Machine Guns attack actions.
        Extra Machine Guns: Extra attack vs Soldier in assault phase.
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # Extra Machine Guns only targets Soldiers
            if enemy_state.unit.unit_type != 'Soldier':
                continue

            enemy_q, enemy_r = enemy_state.position
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)

            # Check range
            max_range = self._get_unit_max_range(unit, 'Soldier')
            if distance > max_range or max_range == 0:
                continue

            # Check LOS
            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), (enemy_q, enemy_r)
            )
            if not has_los:
                continue

            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            # Mark this as an Extra Machine Guns attack
            attack_action.is_extra_mg = True

            actions.append(attack_action)

        return actions

    def _get_speed_boost_attacks(self, game_state: GameState, unit,
                                  unit_state: UnitState, position: Tuple[int, int],
                                  enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate Speed Boost attack actions.
        Speed Boost: Once per game, attack vs Aircraft resolves immediately.
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # Speed Boost only applies to Aircraft targets
            if enemy_state.unit.unit_type != 'Aircraft':
                continue

            enemy_q, enemy_r = enemy_state.position
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)

            # Check range (use anti-Soldier values vs Aircraft)
            max_range = self._get_unit_max_range(unit, 'Soldier')
            if distance > max_range or max_range == 0:
                continue

            # Check LOS
            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), (enemy_q, enemy_r)
            )
            if not has_los:
                continue

            # Check if unit can attack Aircraft (needs Antiair)
            if not self._has_effective_antiair(game_state, unit_state):
                continue

            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            # Mark this as a Speed Boost attack
            attack_action.is_speed_boost = True

            actions.append(attack_action)

        return actions

    def _get_he_round_attacks(self, game_state: GameState, unit,
                              unit_state: UnitState, position: Tuple[int, int],
                              enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate HE Round attack actions.
        HE Round: Once per game, 15 dice vs Soldier.
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # HE Round only applies to Soldier targets
            if enemy_state.unit.unit_type != 'Soldier':
                continue

            enemy_q, enemy_r = enemy_state.position
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)

            # Check range (use anti-Soldier values)
            max_range = self._get_unit_max_range(unit, 'Soldier')
            if distance > max_range or max_range == 0:
                continue

            # Check LOS
            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), (enemy_q, enemy_r)
            )
            if not has_los:
                continue

            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            # Mark this as an HE Round attack
            attack_action.is_he_round = True

            actions.append(attack_action)

        return actions

    def _get_headshot_attacks(self, game_state: GameState, unit,
                              unit_state: UnitState, position: Tuple[int, int],
                              enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate Headshot attack actions.
        Headshot: Once per game, 6 dice, 3+ successes = disrupt Vehicle.
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # Headshot only applies to Vehicle targets
            if enemy_state.unit.unit_type != 'Vehicle':
                continue

            enemy_q, enemy_r = enemy_state.position
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)

            # Check range (use anti-Vehicle values)
            max_range = self._get_unit_max_range(unit, 'Vehicle')
            if distance > max_range or max_range == 0:
                continue

            # Check LOS
            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), (enemy_q, enemy_r)
            )
            if not has_los:
                continue

            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            # Mark this as a Headshot attack
            attack_action.is_headshot = True

            actions.append(attack_action)

        return actions

    def _get_armor_piercing_attacks(self, game_state: GameState, unit,
                                    unit_state: UnitState, position: Tuple[int, int],
                                    enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate Armor-Piercing Rounds attack actions.
        Armor-Piercing Rounds: Once per game, if 2+ hits vs Vehicle, score additional hit.
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # AP Rounds only applies to Vehicle targets
            if enemy_state.unit.unit_type != 'Vehicle':
                continue

            enemy_q, enemy_r = enemy_state.position
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)

            # Check range (use anti-Vehicle values)
            max_range = self._get_unit_max_range(unit, 'Vehicle')
            if distance > max_range or max_range == 0:
                continue

            # Check LOS
            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), (enemy_q, enemy_r)
            )
            if not has_los:
                continue

            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            # Mark this as an Armor-Piercing Rounds attack
            attack_action.is_armor_piercing = True

            actions.append(attack_action)

        return actions

    def _get_firepower_attacks(self, game_state: GameState, unit,
                               unit_state: UnitState, position: Tuple[int, int],
                               enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate Firepower attack actions.
        Firepower: Extra attack vs unit in same hex.
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # Firepower only targets units in the same hex
            enemy_q, enemy_r = enemy_state.position
            if enemy_q != q or enemy_r != r:
                continue

            # Can target any unit type in same hex
            range_category = MovementSystem.get_range_category(0)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=0,
                has_los=True
            )
            # Mark this as a Firepower attack
            attack_action.is_firepower = True

            actions.append(attack_action)

        return actions

    def _get_remote_control_attacks(self, game_state: GameState, unit,
                                    unit_state: UnitState, position: Tuple[int, int],
                                    enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate Remote Control attack actions.
        Remote Control: Once per game, roll 2 dice - if both > range, attack with 12 dice vs Vehicle
        or 8 dice vs Soldier. The dice roll happens during execution.
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # Remote Control only targets Soldiers and Vehicles
            if enemy_state.unit.unit_type not in ('Soldier', 'Vehicle'):
                continue

            enemy_q, enemy_r = enemy_state.position
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)

            # Range must be at least 1 (need something to roll against)
            # Maximum range is 5 (need both dice > 5 which is impossible with d6)
            if distance < 1 or distance > 5:
                continue

            # Check LOS
            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), (enemy_q, enemy_r)
            )
            if not has_los:
                continue

            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            # Mark this as a Remote Control attack
            attack_action.is_remote_control = True

            actions.append(attack_action)

        return actions

    def _get_rocket_salvo_attacks(self, game_state: GameState, unit,
                                   unit_state: UnitState, position: Tuple[int, int],
                                   enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate Rocket Salvo attack actions.
        Rocket Salvo: Once per game, instead of normal attack, roll 10 dice vs each Soldier
        adjacent to target (including target) and 5 dice vs each Vehicle adjacent to target.
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # Rocket Salvo targets Soldiers or Vehicles (as center of area effect)
            if enemy_state.unit.unit_type not in ('Soldier', 'Vehicle'):
                continue

            enemy_q, enemy_r = enemy_state.position
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)

            # Check range (use normal attack range)
            max_range = self._get_unit_max_range(unit, enemy_state.unit.unit_type)
            if distance > max_range or max_range == 0:
                continue

            # Check LOS
            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), (enemy_q, enemy_r)
            )
            if not has_los:
                continue

            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            # Mark this as a Rocket Salvo attack
            attack_action.is_rocket_salvo = True

            actions.append(attack_action)

        return actions

    def _get_rockets_8_attacks(self, game_state: GameState, unit,
                                unit_state: UnitState, position: Tuple[int, int],
                                enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate Rockets 8 attack actions.
        Rockets 8: Once per game, roll 8 attack dice against a Soldier or Vehicle within 4 hexes.
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # Rockets 8 only targets Soldiers and Vehicles
            if enemy_state.unit.unit_type not in ('Soldier', 'Vehicle'):
                continue

            enemy_q, enemy_r = enemy_state.position
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)

            # Rockets 8 has a maximum range of 4 hexes
            if distance > 4 or distance < 1:
                continue

            # Check LOS
            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), (enemy_q, enemy_r)
            )
            if not has_los:
                continue

            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            # Mark this as a Rockets 8 attack
            attack_action.is_rockets_8 = True

            actions.append(attack_action)

        return actions

    def _get_top_mounted_rockets_attacks(self, game_state: GameState, unit,
                                          unit_state: UnitState, position: Tuple[int, int],
                                          enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate Top-Mounted Rockets attack actions.
        Top-Mounted Rockets: Once per game, area attack - 8 dice vs each Soldier adjacent to target,
        4 dice vs each Vehicle adjacent to target (includes target and friendly units).
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # Top-Mounted Rockets only targets Soldiers and Vehicles (not Aircraft)
            if enemy_state.unit.unit_type not in ('Soldier', 'Vehicle'):
                continue

            enemy_q, enemy_r = enemy_state.position
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)

            # Check range (use normal attack range)
            max_range = self._get_unit_max_range(unit, enemy_state.unit.unit_type)
            if distance > max_range or max_range == 0:
                continue

            # Check LOS
            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), (enemy_q, enemy_r)
            )
            if not has_los:
                continue

            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            # Mark this as a Top-Mounted Rockets attack
            attack_action.is_top_mounted_rockets = True

            actions.append(attack_action)

        return actions

    def _get_hull_cannon_attacks(self, game_state: GameState, unit,
                                  unit_state: UnitState, position: Tuple[int, int],
                                  enemy_units: List[UnitState],
                                  is_additional: bool) -> List[AttackAction]:
        """
        Generate Hull-Mounted Cannon attack actions.
        Additional Hull-Mounted Cannon: 12/10/8 vs Vehicle in front
        Extra Hull-Mounted Cannon: 8/7/5 vs Vehicle or Soldier in front
        """
        actions = []
        q, r = position

        # Must be a Vehicle with facing to determine "in front"
        if unit.unit_type != 'Vehicle' or unit_state.facing is None:
            return actions

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # Additional Hull-Mounted Cannon: only Vehicles
            # Extra Hull-Mounted Cannon: Vehicles or Soldiers
            if is_additional:
                if enemy_state.unit.unit_type != 'Vehicle':
                    continue
            else:
                if enemy_state.unit.unit_type not in ('Soldier', 'Vehicle'):
                    continue

            enemy_q, enemy_r = enemy_state.position
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)

            # Check range (use Soldier attack values since hull cannon has its own)
            max_range = self._get_unit_max_range(unit, 'Soldier')
            if distance > max_range or max_range == 0:
                continue

            # Check if target is in front (front arc)
            from facing import HexDirection, is_front_arc_attack
            attacker_facing = HexDirection(unit_state.facing)
            target_pos = (enemy_q, enemy_r)
            attacker_pos = (q, r)
            if not is_front_arc_attack(target_pos, attacker_pos, attacker_facing):
                continue  # Target not in front

            # Check LOS
            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), (enemy_q, enemy_r)
            )
            if not has_los:
                continue

            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            # Mark which hull cannon type
            if is_additional:
                attack_action.is_additional_hull_cannon = True
            else:
                attack_action.is_extra_hull_cannon = True

            actions.append(attack_action)

        return actions

    def _get_facing_change_actions(self, unit, unit_state: UnitState) -> List[UseAbilityAction]:
        """
        Generate facing change actions for Vehicles with Command Quick Reactions available.
        """
        actions = []

        if unit.unit_type != 'Vehicle':
            return actions

        current_facing = unit_state.facing
        if current_facing is None:
            return actions

        # Generate actions for each possible facing direction (0-5)
        for new_facing in range(6):
            if new_facing == current_facing:
                continue  # Don't generate action for current facing

            actions.append(UseAbilityAction(
                unit_id=unit.id,
                ability_name="change_facing",
                parameters={'new_facing': new_facing}
            ))

        return actions

    def _get_angriff_actions(self, game_state: GameState,
                             player_units: List[UnitState],
                             enemy_units: List[UnitState]) -> List[MoveAndAttackAction]:
        """
        Generate Angriff actions.
        Angriff: Friendly non-disrupted non-Artillery Soldiers adjacent to unit with Angriff
        can move into an adjacent hex and attack an enemy in that hex with +1 on each die.
        """
        actions = []

        # Find units with Angriff ability
        angriff_positions = []
        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            abilities = getattr(unit_state.unit, 'abilities', []) or []
            has_angriff = any(a.lower() == 'angriff' for a in abilities)
            if has_angriff:
                angriff_positions.append(unit_state.position)

        if not angriff_positions:
            return actions

        # Find eligible Soldiers (non-disrupted, non-Artillery, adjacent to Angriff unit)
        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            if unit_state.unit.unit_type != 'Soldier':
                continue
            # Check disruption (but Heroes ignore disruption)
            if unit_state.is_disrupted and not self._has_hero_ability(unit_state.unit):
                continue
            # Check if already attacked
            if not game_state.can_unit_attack(unit_state.unit.id):
                continue

            abilities = getattr(unit_state.unit, 'abilities', []) or []
            is_artillery = any('artillery' in a.lower() for a in abilities)
            if is_artillery:
                continue

            q, r = unit_state.position

            # Check if adjacent to any Angriff unit
            is_adjacent_to_angriff = False
            for angriff_pos in angriff_positions:
                dist = game_state.board.hex_distance(q, r, angriff_pos[0], angriff_pos[1])
                if dist <= 1:
                    is_adjacent_to_angriff = True
                    break

            if not is_adjacent_to_angriff:
                continue

            # Find adjacent hexes with enemies
            neighbors = game_state.board.get_neighbors(q, r)
            for neighbor in neighbors:
                nq, nr = neighbor.q, neighbor.r
                # Check for enemies in this hex
                for enemy_state in enemy_units:
                    if not enemy_state.is_alive:
                        continue
                    if enemy_state.unit.unit_type not in ('Soldier', 'Vehicle'):
                        continue
                    if enemy_state.position != (nq, nr):
                        continue

                    # Create move action
                    move_action = MoveAction(
                        unit_id=unit_state.unit.id,
                        from_q=q, from_r=r,
                        to_q=nq, to_r=nr,
                        path=[(q, r), (nq, nr)],
                        movement_cost=1
                    )

                    # Create attack action
                    attack_action = AttackAction(
                        unit_id=unit_state.unit.id,
                        attacker_q=nq, attacker_r=nr,
                        target_id=enemy_state.unit.id,
                        target_q=nq, target_r=nr,
                        range_category='short',
                        distance=0,
                        has_los=True
                    )
                    attack_action.is_angriff = True

                    # Create combined move-and-attack action
                    move_attack = MoveAndAttackAction(
                        unit_id=unit_state.unit.id,
                        move_action=move_action,
                        attack_action=attack_action
                    )
                    move_attack.is_angriff = True

                    actions.append(move_attack)

        return actions

    def _get_banzai_charge_actions(self, game_state: GameState,
                                   player_units: List[UnitState],
                                   enemy_units: List[UnitState]) -> List[MoveAndAttackAction]:
        """
        Generate Banzai Charge actions.
        Banzai Charge: Friendly non-Artillery Soldiers adjacent to unit with Banzai Charge
        can move into an enemy unit's hex and attack that unit with +1 on each die.
        """
        actions = []

        # Find units with Banzai Charge ability
        banzai_positions = []
        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            abilities = getattr(unit_state.unit, 'abilities', []) or []
            has_banzai = any(a.lower() == 'banzai charge' for a in abilities)
            if has_banzai:
                banzai_positions.append(unit_state.position)

        if not banzai_positions:
            return actions

        # Find eligible Soldiers (non-Artillery, adjacent to Banzai Charge unit)
        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            if unit_state.unit.unit_type != 'Soldier':
                continue
            # Check if already attacked
            if not game_state.can_unit_attack(unit_state.unit.id):
                continue

            abilities = getattr(unit_state.unit, 'abilities', []) or []
            is_artillery = any('artillery' in a.lower() for a in abilities)
            if is_artillery:
                continue

            q, r = unit_state.position

            # Check if adjacent to any Banzai Charge unit
            is_adjacent_to_banzai = False
            for banzai_pos in banzai_positions:
                dist = game_state.board.hex_distance(q, r, banzai_pos[0], banzai_pos[1])
                if dist <= 1:
                    is_adjacent_to_banzai = True
                    break

            if not is_adjacent_to_banzai:
                continue

            # Find enemy hexes that this unit can move into
            for enemy_state in enemy_units:
                if not enemy_state.is_alive:
                    continue
                eq, er = enemy_state.position
                # Must be adjacent
                dist = game_state.board.hex_distance(q, r, eq, er)
                if dist != 1:
                    continue

                # Create move action
                move_action = MoveAction(
                    unit_id=unit_state.unit.id,
                    from_q=q, from_r=r,
                    to_q=eq, to_r=er,
                    path=[(q, r), (eq, er)],
                    movement_cost=1
                )

                # Create attack action
                attack_action = AttackAction(
                    unit_id=unit_state.unit.id,
                    attacker_q=eq, attacker_r=er,
                    target_id=enemy_state.unit.id,
                    target_q=eq, target_r=er,
                    range_category='short',
                    distance=0,
                    has_los=True
                )
                attack_action.is_banzai_charge = True

                # Create combined move-and-attack action
                move_attack = MoveAndAttackAction(
                    unit_id=unit_state.unit.id,
                    move_action=move_action,
                    attack_action=attack_action
                )
                move_attack.is_banzai_charge = True

                actions.append(move_attack)

        return actions

    def _get_bombs_attacks(self, game_state: GameState, unit,
                           unit_state: UnitState, position: Tuple[int, int],
                           enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate Bombs attack actions.
        Bombs: Once per game, 12 dice vs Soldier or 8 dice vs Vehicle in same hex.
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # Bombs can only target Soldiers and Vehicles
            if enemy_state.unit.unit_type not in ('Soldier', 'Vehicle'):
                continue

            # Must be in the same hex
            enemy_q, enemy_r = enemy_state.position
            if (enemy_q, enemy_r) != (q, r):
                continue

            # Create Bombs attack action
            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category='short',
                distance=0,
                has_los=True
            )
            # Mark this as a Bombs attack
            attack_action.is_bombs = True

            actions.append(attack_action)

        return actions

    def _get_all_guns_blazing_attacks(self, game_state: GameState, unit,
                                       unit_state: UnitState, position: Tuple[int, int],
                                       enemy_units: List[UnitState]) -> List[AttackAction]:
        """
        Generate All Guns Blazing extra attack actions.
        Only targets Soldiers, available after the unit has attacked.
        """
        actions = []
        q, r = position

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            # All Guns Blazing only targets Soldiers
            if enemy_state.unit.unit_type != 'Soldier':
                continue

            enemy_pos = enemy_state.position
            enemy_q, enemy_r = enemy_pos

            # Check range and LOS
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)
            max_range = self._get_unit_max_range(unit, 'Soldier')

            if distance > max_range:
                continue

            has_los = self.movement_system.has_line_of_sight(
                game_state.board, (q, r), enemy_pos
            )
            if not has_los:
                continue

            # Determine range category
            range_category = MovementSystem.get_range_category(distance)

            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy_state.unit.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_category,
                distance=distance,
                has_los=has_los
            )
            # Mark this as an All Guns Blazing attack
            attack_action.is_all_guns_blazing = True

            actions.append(attack_action)

        return actions

    def _get_relocate_moves(self, game_state: GameState, unit,
                            unit_state: UnitState, relocate_speed: int) -> List[MoveAction]:
        """
        Generate Relocate movement actions.
        Relocate X means unit can move at speed X during assault phase.
        """
        actions = []
        q, r = unit_state.position

        # Get reachable hexes at the relocate speed
        reachable = self.movement_system.get_reachable_hexes(
            game_state.board, q, r, unit, max_speed=relocate_speed
        )

        # Create move actions
        for (dest_q, dest_r) in reachable:
            if (dest_q, dest_r) == (q, r):
                continue  # Skip current position

            move_action = MoveAction(
                unit_id=unit.id,
                from_q=q,
                from_r=r,
                to_q=dest_q,
                to_r=dest_r,
                path=[(q, r), (dest_q, dest_r)],
                movement_cost=0
            )
            # Mark this as a Relocate move
            move_action.is_relocate = True

            actions.append(move_action)

        return actions

    def _get_strike_and_fade_moves(self, game_state: GameState, unit,
                                   unit_state: UnitState) -> List[MoveAction]:
        """
        Generate Strike and Fade movement actions.
        Only available after a unit with Strike and Fade has attacked.
        """
        actions = []
        q, r = unit_state.position

        # Get Strike and Fade speed
        fade_speed = self._get_strike_and_fade_speed(unit)
        if fade_speed <= 0:
            return actions

        # Get reachable hexes at the fade speed
        reachable = self.movement_system.get_reachable_hexes(
            game_state.board, q, r, unit, max_speed=fade_speed
        )

        # Create move actions (these are special "fade" moves)
        for (dest_q, dest_r) in reachable:
            if (dest_q, dest_r) == (q, r):
                continue  # Skip current position

            move_action = MoveAction(
                unit_id=unit.id,
                from_q=q,
                from_r=r,
                to_q=dest_q,
                to_r=dest_r,
                path=[(q, r), (dest_q, dest_r)],
                movement_cost=0
            )
            # Mark this as a Strike and Fade move (for action executor)
            move_action.is_strike_and_fade = True

            actions.append(move_action)

        return actions
    
    def _get_attack_actions(self, game_state: GameState, unit, unit_state: UnitState,
                           position: Tuple[int, int],
                           enemy_units: List[UnitState]) -> List[AttackAction]:
        """Generate all legal attack actions for a unit"""
        actions = []
        q, r = position

        # Check for special abilities that affect targeting
        has_no_turret = self._has_no_turret(unit)
        has_fixed_howitzer = self._has_fixed_howitzer(unit)
        has_fixed_gun = self._has_fixed_gun(unit)
        has_fixed_rear_gun = self._has_fixed_rear_gun(unit)
        has_turret_lock = self._has_turret_lock(unit)
        has_indirect_fire = self._has_indirect_fire(unit)
        has_improved_indirect_fire = self._has_improved_indirect_fire(unit)
        has_antiair = self._has_effective_antiair(game_state, unit_state)
        has_bombardment = self._has_bombardment(unit)

        # Turret Lock: Can't attack while in a hill hex
        if has_turret_lock:
            attacker_hex = game_state.board.get_hex(q, r)
            if attacker_hex and attacker_hex.terrain == 'hill':
                return []  # Can't attack at all

        # No Turret: can attack Vehicles ONLY if in front (Soldiers can be attacked from any direction)
        # Fixed Howitzer: can attack ALL units ONLY if in front
        # Fixed Gun: can attack Vehicles ONLY if in front
        # Fixed Rear Gun: can attack Vehicles ONLY if in rear
        # Bombardment: can't attack Aircraft

        # For front-arc restriction, we need the unit's facing (applies to vehicles)
        attacker_facing = None
        needs_facing_check = has_no_turret or has_fixed_howitzer or has_fixed_gun or has_fixed_rear_gun
        if needs_facing_check and 'Vehicle' in unit.unit_type:
            facing_val = unit_state.facing
            if facing_val is not None:
                attacker_facing = HexDirection(facing_val)

        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue

            enemy = enemy_state.unit
            enemy_q, enemy_r = enemy_state.position

            # Antiair check - can only attack Aircraft if has Antiair ability
            # Bombardment check - units with Bombardment can't attack Aircraft
            if enemy.unit_type == 'Aircraft':
                if not has_antiair or has_bombardment:
                    continue

            # Calculate distance
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)

            # Check if in range (both max and minimum)
            max_range = self._get_unit_max_range(unit, enemy.unit_type)

            # Dismounted Attack: extend short range vs Vehicles to 0-2 when no
            # adjacent enemy Soldiers
            unit_abilities_list = getattr(unit, 'abilities', []) or []
            has_dismounted_attack = any(
                a.lower() == 'dismounted attack' for a in unit_abilities_list
            )
            if (has_dismounted_attack and enemy.unit_type == 'Vehicle'
                    and max_range < 2 and max_range > 0):
                # Check for adjacent enemy Soldiers
                has_adj_enemy_soldier = False
                enemy_owner_check = "player2" if unit_state.owner == "player1" else "player1"
                for check_state in game_state.get_units_by_owner(enemy_owner_check):
                    if (check_state.is_alive
                            and check_state.unit.unit_type == 'Soldier'
                            and game_state.board.hex_distance(
                                q, r, check_state.position[0], check_state.position[1]
                            ) <= 1):
                        has_adj_enemy_soldier = True
                        break
                if not has_adj_enemy_soldier:
                    max_range = max(max_range, 2)

            if distance > max_range or max_range == 0:
                continue

            # Arc restrictions:
            # - No Turret: can attack Vehicles ONLY if in front
            # - Fixed Howitzer: can attack ALL units ONLY if in front
            # - Fixed Gun: can attack Vehicles ONLY if in front
            # - Fixed Rear Gun: can attack Vehicles ONLY if in rear
            if attacker_facing is not None:
                target_is_vehicle = 'Vehicle' in enemy.unit_type
                is_in_front = is_target_in_front_arc((q, r), (enemy_q, enemy_r), attacker_facing)

                # Fixed Howitzer: all targets must be in front
                if has_fixed_howitzer and not is_in_front:
                    continue

                # No Turret / Fixed Gun: vehicles must be in front
                if target_is_vehicle and (has_no_turret or has_fixed_gun) and not is_in_front:
                    continue

                # Fixed Rear Gun: vehicles must be in rear (not front)
                if target_is_vehicle and has_fixed_rear_gun and is_in_front:
                    continue

            # Multiturreted arc restrictions:
            # - Can attack twice, but one must be front arc, one must be rear arc
            if self._has_multiturreted(unit) and attacker_facing is not None:
                is_front = is_target_in_front_arc((q, r), (enemy_q, enemy_r), attacker_facing)
                if not self._can_multiturreted_attack_arc(unit_state, is_front):
                    continue  # This arc already used

            # Check line of sight
            has_los, _ = self.movement_system.has_line_of_sight(
                game_state.board, unit, q, r, enemy_q, enemy_r,
                smoke_screens=game_state.smoke_screens
            )

            # Indirect Fire: "If a friendly Spotter is within eight hexes of an enemy
            # Soldier and has line of sight to it, this unit's attack against that
            # Soldier ignores line of sight."
            # Improved Indirect Fire: Also allows U.S. Commanders within 4 hexes to act as spotters.
            if not has_los:
                # Can only ignore LOS with Indirect Fire + Spotter + Soldier target
                if (has_indirect_fire or has_improved_indirect_fire) and enemy.unit_type == 'Soldier':
                    owner = game_state.get_unit_owner(unit.id)
                    check_improved = has_improved_indirect_fire
                    if self._has_friendly_spotter_for_target(game_state, owner, enemy_q, enemy_r, check_improved):
                        has_los = True  # Spotter/Commander allows Indirect Fire to work
                if not has_los:
                    continue

            # Get range category
            range_cat = self.movement_system.get_range_category(distance)

            # Create attack action
            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_cat,
                distance=distance,
                has_los=has_los
            )

            actions.append(attack_action)

            # Improvisation: If unit has Improvisation and is in a hex with a destroyed
            # Soldier/Vehicle wreck, it can use that wreck's attack values
            if self._has_improvisation(unit):
                wrecks = game_state.get_destroyed_wrecks_at_position(q, r)
                for wreck in wrecks:
                    # Check if the wreck has attack values at this range
                    wreck_attack = None
                    if range_cat == 'short':
                        wreck_attack = wreck.get('attack_close', 0)
                    elif range_cat == 'medium':
                        wreck_attack = wreck.get('attack_medium', 0)
                    elif range_cat == 'long':
                        wreck_attack = wreck.get('attack_long', 0)

                    # Only create improvised attack if wreck has better attack at this range
                    if wreck_attack and wreck_attack > 0:
                        improvised_action = AttackAction(
                            unit_id=unit.id,
                            attacker_q=q,
                            attacker_r=r,
                            target_id=enemy.id,
                            target_q=enemy_q,
                            target_r=enemy_r,
                            range_category=range_cat,
                            distance=distance,
                            has_los=has_los,
                            improvised_attack=wreck
                        )
                        actions.append(improvised_action)

        # Exposed Transport: Can attack soldiers that are aboard enemy Exposed Transports
        # The soldier uses the transport's position for targeting
        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue
            # Check if this is a transport with Exposed Transport ability
            enemy = enemy_state.unit
            if enemy.unit_type != 'Vehicle':
                continue
            enemy_abilities = getattr(enemy, 'abilities', []) or []
            has_exposed_transport = any('exposed transport' in a.lower() for a in enemy_abilities)
            if not has_exposed_transport:
                continue
            # Check if there's a unit aboard
            if not enemy_state.carried_unit_id:
                continue
            # Get the carried unit
            carried_state = game_state.get_unit_state(enemy_state.carried_unit_id)
            if not carried_state or not carried_state.is_alive:
                continue
            carried_unit = carried_state.unit
            # Use the transport's position for targeting
            transport_q, transport_r = enemy_state.position
            distance = game_state.board.hex_distance(q, r, transport_q, transport_r)
            # Check range (use anti-Soldier values since carried units are Soldiers)
            max_range = self._get_unit_max_range(unit, 'Soldier')
            if distance > max_range or max_range == 0:
                continue
            # Check LOS to transport's position
            has_los, _ = self.movement_system.has_line_of_sight(
                game_state.board, unit, q, r, transport_q, transport_r,
                smoke_screens=game_state.smoke_screens
            )
            if not has_los:
                continue
            # Create attack action targeting the carried unit at the transport's position
            range_cat = self.movement_system.get_range_category(distance)
            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=carried_unit.id,
                target_q=transport_q,
                target_r=transport_r,
                range_category=range_cat,
                distance=distance,
                has_los=has_los
            )
            # Mark this as an attack on an exposed transport passenger
            attack_action.is_exposed_transport_attack = True
            attack_action.transport_id = enemy.id
            actions.append(attack_action)

        return actions

    def _get_move_and_attack_actions(self, game_state: GameState, unit, 
                                     unit_state: UnitState,
                                     current_pos: Tuple[int, int],
                                     enemy_units: List[UnitState]) -> List[MoveAndAttackAction]:
        """
        Generate all legal move-and-attack actions.
        For units with abilities like "All Guns Blazing" or "Aggression".
        """
        actions = []
        q, r = current_pos
        
        # Get assault movement range (usually smaller than full movement)
        assault_range = self.movement_system.get_assault_move_range(unit)
        
        if assault_range == 0:
            return actions
        
        # Get reachable positions for assault move
        reachable = self.movement_system.get_reachable_hexes(
            game_state.board, q, r, unit, max_speed=assault_range
        )
        
        # For each reachable position, check what we can attack from there
        for (move_q, move_r) in reachable:
            if (move_q, move_r) == (q, r):
                continue  # Skip staying in place
            
            # Create move action
            move_action = MoveAction(
                unit_id=unit.id,
                from_q=q,
                from_r=r,
                to_q=move_q,
                to_r=move_r
            )
            
            # Check what we can attack from this new position
            for enemy_state in enemy_units:
                if not enemy_state.is_alive:
                    continue
                
                enemy = enemy_state.unit
                enemy_q, enemy_r = enemy_state.position
                
                # Calculate distance from new position
                distance = game_state.board.hex_distance(move_q, move_r, enemy_q, enemy_r)
                
                max_range = self._get_unit_max_range(unit, enemy.unit_type)
                if distance > max_range or max_range == 0:
                    continue
                
                # Check LOS from new position
                has_los, _ = self.movement_system.has_line_of_sight(
                    game_state.board, unit, move_q, move_r, enemy_q, enemy_r,
                    smoke_screens=game_state.smoke_screens
                )
                
                if not has_los:
                    continue
                
                range_cat = self.movement_system.get_range_category(distance)
                
                # Create attack action from new position
                attack_action = AttackAction(
                    unit_id=unit.id,
                    attacker_q=move_q,
                    attacker_r=move_r,
                    target_id=enemy.id,
                    target_q=enemy_q,
                    target_r=enemy_r,
                    range_category=range_cat,
                    distance=distance,
                    has_los=has_los
                )
                
                # Create combined move-and-attack action
                combined_action = MoveAndAttackAction(
                    unit_id=unit.id,
                    move_action=move_action,
                    attack_action=attack_action
                )
                
                actions.append(combined_action)
        
        return actions
    
    def _get_ability_actions(self, game_state: GameState, unit,
                            unit_state: UnitState) -> List[UseAbilityAction]:
        """Generate all legal ability use actions for a unit"""
        actions = []

        if not unit.abilities:
            return actions

        for ability_name in unit.abilities:
            # Skip if already used this turn
            if ability_name in unit_state.abilities_used:
                continue

            # Smoke Screen: Once per game at end of movement phase
            if ability_name.lower() == 'smoke screen':
                # Only available at end of movement phase
                if game_state.current_phase != GamePhase.MOVEMENT:
                    continue
                # Once per game check
                if unit_state.smoke_screen_used:
                    continue
                # Add the action
                ability_action = UseAbilityAction(
                    unit_id=unit.id,
                    ability_name=ability_name
                )
                actions.append(ability_action)
                continue

            # Demolitions: Destroy obstacles at end of movement/assault phase
            if ability_name.lower() == 'demolitions':
                # Only at end of movement or assault phase
                if game_state.current_phase not in [GamePhase.MOVEMENT, GamePhase.ASSAULT]:
                    continue
                # Find obstacles in same hex or adjacent
                q, r = unit_state.position
                obstacle_targets = []
                # Check same hex
                for other_state in game_state.get_units_at_position(q, r):
                    if other_state.unit.unit_type == 'Obstacle':
                        obstacle_targets.append(other_state)
                # Check adjacent hexes
                for neighbor in game_state.board.get_neighbors(q, r):
                    for other_state in game_state.get_units_at_position(neighbor.q, neighbor.r):
                        if other_state.unit.unit_type == 'Obstacle':
                            obstacle_targets.append(other_state)

                # Create action for each obstacle
                for obstacle_state in obstacle_targets:
                    oq, oR = obstacle_state.position
                    ability_action = UseAbilityAction(
                        unit_id=unit.id,
                        ability_name=ability_name,
                        target_id=obstacle_state.unit.id,
                        target_q=oq,
                        target_r=oR
                    )
                    actions.append(ability_action)
                continue

            # Create ability action
            # Note: This is simplified - real implementation would need
            # to determine valid targets and parameters for each ability
            ability_action = UseAbilityAction(
                unit_id=unit.id,
                ability_name=ability_name
            )

            actions.append(ability_action)

        return actions
    
    def get_legal_moves(self, game_state: GameState, unit_id: str) -> List[MoveAction]:
        """Get all legal moves for a specific unit"""
        unit_state = game_state.get_unit_state(unit_id)
        if not unit_state or not unit_state.is_alive or unit_state.has_moved:
            return []
        
        unit = unit_state.unit
        q, r = unit_state.position
        
        reachable = self.movement_system.get_reachable_hexes(
            game_state.board, q, r, unit
        )
        
        moves = []
        for (dest_q, dest_r) in reachable:
            if (dest_q, dest_r) == (q, r):
                continue
            
            move = MoveAction(
                unit_id=unit.id,
                from_q=q,
                from_r=r,
                to_q=dest_q,
                to_r=dest_r
            )
            moves.append(move)
        
        return moves
    
    def get_legal_attacks(self, game_state: GameState, unit_id: str) -> List[AttackAction]:
        """Get all legal attacks for a specific unit"""
        unit_state = game_state.get_unit_state(unit_id)
        if not unit_state or not unit_state.is_alive:
            return []
        # Use can_unit_attack for Double Shot support
        if not game_state.can_unit_attack(unit_id):
            return []
        
        unit = unit_state.unit
        owner = unit_state.owner
        enemy_owner = "player2" if owner == "player1" else "player1"
        enemy_units = game_state.get_units_by_owner(enemy_owner)
        
        return self._get_attack_actions(
            game_state, unit, unit_state, unit_state.position, enemy_units
        )
    
    def count_legal_actions(self, game_state: GameState, player: str) -> int:
        """Count total number of legal actions (useful for complexity analysis)"""
        return len(self.get_all_legal_actions(game_state, player))
    
    def get_action_summary(self, game_state: GameState, player: str) -> str:
        """Get a human-readable summary of available actions"""
        actions = self.get_all_legal_actions(game_state, player)
        
        move_count = sum(1 for a in actions if isinstance(a, MoveAction))
        attack_count = sum(1 for a in actions if isinstance(a, AttackAction))
        move_attack_count = sum(1 for a in actions if isinstance(a, MoveAndAttackAction))
        ability_count = sum(1 for a in actions if isinstance(a, UseAbilityAction))
        
        summary = f"Available Actions for {player}:\n"
        summary += f"  Movement: {move_count} options\n"
        summary += f"  Attacks: {attack_count} options\n"
        summary += f"  Move+Attack: {move_attack_count} options\n"
        summary += f"  Abilities: {ability_count} options\n"
        summary += f"  Total: {len(actions)} actions\n"
        
        return summary


# Test/Demo function
def demo_action_generation():
    """Demonstrate action generation"""
    from game_state import create_test_game_state
    from abilities import AbilitySystem
    
    # Create test game
    game_state = create_test_game_state()
    
    # Create systems
    ability_system = AbilitySystem('Axis and Allies Unit Data for Analysis - Special_Abilities.csv')
    movement_system = MovementSystem(ability_system)
    combat_system = CombatSystem(ability_system)
    
    # Create action generator
    action_gen = ActionGenerator(movement_system, combat_system, ability_system)
    
    print("=== ACTION GENERATION DEMO ===\n")
    print(game_state)
    print()
    
    # Get all legal actions for player 1
    print(action_gen.get_action_summary(game_state, "player1"))
    print()
    
    # Show first few actions in detail
    actions = action_gen.get_all_legal_actions(game_state, "player1")
    print("Sample actions:")
    for action in actions[:10]:
        print(f"  {action}")
    
    print(f"\n... and {len(actions) - 10} more actions")


if __name__ == "__main__":
    demo_action_generation()