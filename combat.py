"""
Combat System for Axis & Allies Miniatures

Handles combat resolution between units using the authentic dice mechanics.
Integrates with the dice.py module for all rolling and damage resolution.
"""

from typing import Tuple, Dict, List, Optional
from board import Board
from movement import MovementSystem
from abilities import AbilitySystem
from dice import (
    DiceSystem, UnitStatus, UnitCategory, 
    AttackResult, DamageResult, CoverResult,
    get_unit_category, get_unit_status
)


class CombatSystem:
    """
    Handles combat resolution between units.
    
    Uses the DiceSystem for authentic A&A Miniatures mechanics:
    - Attack dice hit on 4, 5, 6 (50% per die)
    - Disrupted/Damaged units: -1 penalty (hit on 5, 6 only)
    - Cover saves: Infantry 4+, Vehicles 5+
    - Damage thresholds based on successes vs defense
    """
    
    # Cover-granting terrain types
    COVER_TERRAIN = ['forest', 'building', 'hill', 'town']
    
    def __init__(self, ability_system: AbilitySystem, random_seed: Optional[int] = None):
        """
        Initialize combat system.
        
        Args:
            ability_system: AbilitySystem for special ability lookups
            random_seed: Optional seed for reproducible dice rolls (testing)
        """
        self.ability_system = ability_system
        self.dice = DiceSystem(random_seed)
    
    def get_attack_value(self, attacker, range_category: str) -> int:
        """
        Get attack value (number of dice) for a unit at a given range.
        
        This is the BASE attack value before any ability modifiers.
        
        Args:
            attacker: Unit performing the attack
            range_category: 'short', 'medium', or 'long'
        
        Returns:
            Number of attack dice
        """
        # This will be overridden by get_attack_dice which considers target type
        return 0
    
    def get_attack_dice(self, attacker, target, distance: int, 
                        ability_mods: Dict = None) -> int:
        """
        Get number of attack dice based on target type, range, and abilities.
        
        Args:
            attacker: Attacking unit
            target: Target unit
            distance: Distance in hexes
            ability_mods: Dictionary of ability modifiers
        
        Returns:
            Number of dice to roll
        """
        ability_mods = ability_mods or {}
        
        # Check for Close Assault first (overrides normal attack)
        if ability_mods.get('close_assault_dice') and distance == 0:
            return ability_mods['close_assault_dice']
        
        # Determine range category
        range_category = MovementSystem.get_range_category(distance)
        
        # Check if target is a vehicle
        target_type = getattr(target, 'unit_type', 'Soldier')
        is_vehicle = target_type == 'Vehicle'
        
        if is_vehicle:
            if range_category == 'short':
                dice = getattr(attacker, 'veh_short', 0)
            elif range_category == 'medium':
                dice = getattr(attacker, 'veh_medium', 0)
            else:
                dice = getattr(attacker, 'veh_long', 0)
        else:
            if range_category == 'short':
                dice = getattr(attacker, 'per_short', 0)
            elif range_category == 'medium':
                dice = getattr(attacker, 'per_medium', 0)
            else:
                dice = getattr(attacker, 'per_long', 0)
        
        # Apply any bonus dice from abilities
        dice += ability_mods.get('bonus_dice', 0)
        
        return max(0, dice)
    
    def get_defense_value(self, target, is_rear_attack: bool = False,
                          is_disrupted: bool = False, 
                          is_damaged: bool = False,
                          ability_mods: Dict = None) -> int:
        """
        Get effective defense value for a target.
        
        Args:
            target: Target unit
            is_rear_attack: True if attacking from rear arc
            is_disrupted: True if target is disrupted
            is_damaged: True if target is damaged
            ability_mods: Dictionary of ability modifiers
        
        Returns:
            Effective defense value
        """
        ability_mods = ability_mods or {}
        
        # Base defense (front or rear)
        if is_rear_attack:
            defense = getattr(target, 'defense_rear', getattr(target, 'defense_front', 3))
        else:
            defense = getattr(target, 'defense_front', 3)
        
        # Apply disrupted/damaged penalty (-1, non-stacking)
        if is_disrupted or is_damaged:
            defense = max(1, defense - 1)
        
        # Apply ability modifiers
        defense += ability_mods.get('defense_bonus', 0)
        
        return max(1, defense)
    
    def terrain_provides_cover(self, terrain: str) -> bool:
        """Check if terrain provides cover"""
        return terrain in self.COVER_TERRAIN
    
    def resolve_attack(self, attacker, target, distance: int,
                       attacker_state: Dict = None,
                       target_state: Dict = None,
                       target_terrain: str = 'open',
                       is_rear_attack: bool = False,
                       attacker_same_hex: bool = False) -> Dict:
        """
        Resolve a complete attack using authentic A&A Miniatures mechanics.
        
        Args:
            attacker: Attacking unit
            target: Target unit
            distance: Distance in hexes
            attacker_state: Dict with 'is_disrupted', 'is_damaged' keys
            target_state: Dict with 'is_disrupted', 'is_damaged' keys
            target_terrain: Terrain type target is in
            is_rear_attack: True if attacking from rear arc
            attacker_same_hex: True if attacker is in same hex as target
        
        Returns:
            Dictionary with complete attack resolution details
        """
        attacker_state = attacker_state or {}
        target_state = target_state or {}
        
        result = {
            'attacker': getattr(attacker, 'name', 'Unknown'),
            'target': getattr(target, 'name', 'Unknown'),
            'distance': distance,
            'range_category': MovementSystem.get_range_category(distance),
            'attack_dice': 0,
            'attack_result': None,
            'cover_result': None,
            'defense_value': 0,
            'effective_defense': 0,
            'hits': 0,
            'damage_result': None,
            'outcome': 'no_attack',
            'notes': [],
            'target_new_status': None,
            'target_destroyed': False
        }
        
        # Get ability modifiers
        attack_mods = self.ability_system.get_attack_modifiers(
            attacker, target, distance, target_terrain, is_rear_attack, target_state
        )
        
        # Check if attack is possible
        if not attack_mods.get('can_attack', True):
            result['notes'].extend(attack_mods.get('notes', []))
            return result
        
        # Get attack dice count
        attack_dice = self.get_attack_dice(attacker, target, distance, attack_mods)
        result['attack_dice'] = attack_dice
        
        if attack_dice <= 0:
            result['notes'].append("No attack value at this range")
            return result
        
        # Close Assault always targets rear armor
        if attack_mods.get('close_assault_dice') and distance == 0:
            is_rear_attack = True
            result['notes'].append("Close Assault: Targeting rear armor")
        
        # Determine attacker penalties
        attacker_disrupted = attacker_state.get('is_disrupted', False)
        attacker_damaged = attacker_state.get('is_damaged', False)
        
        # Determine target status
        target_disrupted = target_state.get('is_disrupted', False)
        target_damaged = target_state.get('is_damaged', False)
        
        # Get target's unit category
        target_category = get_unit_category(target)
        
        # Get current status for damage resolution
        if target_disrupted and target_damaged:
            current_status = UnitStatus.DISRUPTED_AND_DAMAGED
        elif target_damaged:
            current_status = UnitStatus.DAMAGED
        elif target_disrupted:
            current_status = UnitStatus.DISRUPTED
        else:
            current_status = UnitStatus.HEALTHY
        
        # Get defense modifiers
        defense_mods = self.ability_system.get_defense_modifiers(
            target, target_terrain, is_rear_attack, attacker, distance
        )
        
        # Calculate defense value
        base_defense = self.get_defense_value(
            target, is_rear_attack, 
            target_disrupted, target_damaged,
            defense_mods
        )
        result['defense_value'] = base_defense
        result['effective_defense'] = base_defense
        result['is_rear_attack'] = is_rear_attack
        
        # Check for cover
        has_cover = self.terrain_provides_cover(target_terrain)
        ignore_cover = attack_mods.get('ignore_cover', False)
        
        if ignore_cover:
            has_cover = False
            result['notes'].append("Attacker ignores cover")
        
        # Roll cover save if applicable (defender rolls first to know if they have protection)
        cover_success = False
        if has_cover:
            cover_ability_mod = defense_mods.get('cover_bonus', 0)
            cover_result = self.dice.roll_cover_save(
                target_category,
                attacker_same_hex,
                cover_ability_mod
            )
            result['cover_result'] = {
                'roll': cover_result.roll,
                'threshold': cover_result.threshold,
                'modifier': cover_result.modifier,
                'success': cover_result.success
            }
            cover_success = cover_result.success
        
        # Roll attack
        attack_ability_mod = attack_mods.get('hit_modifier', 0)
        attack_result = self.dice.roll_attack(
            attack_dice,
            attacker_disrupted,
            attacker_damaged,
            attack_ability_mod
        )
        result['attack_result'] = {
            'dice_rolled': attack_result.dice_rolled,
            'successes': attack_result.successes,
            'hit_threshold': attack_result.hit_threshold,
            'rolls': attack_result.rolls
        }
        
        # Calculate hits based on successes vs defense
        hits = self.dice.calculate_hits(attack_result.successes, base_defense)
        result['hits'] = hits
        
        if hits == 0:
            result['outcome'] = 'miss'
            result['notes'].append(f"Scored {attack_result.successes} successes, needed {base_defense} to hit")
            return result
        
        # Resolve damage
        damage_result = self.dice.resolve_damage(
            hits, target_category, current_status, cover_success
        )
        result['damage_result'] = {
            'hits_scored': damage_result.hits_scored,
            'new_status': damage_result.new_status.value,
            'status_change': damage_result.status_change,
            'counters_placed': damage_result.counters_placed
        }
        result['target_new_status'] = damage_result.new_status
        result['target_destroyed'] = damage_result.new_status == UnitStatus.DESTROYED
        
        # Set outcome based on damage result
        if damage_result.new_status == UnitStatus.DESTROYED:
            result['outcome'] = 'destroyed'
        elif damage_result.new_status == UnitStatus.DISRUPTED_AND_DAMAGED:
            result['outcome'] = 'disrupted_and_damaged'
        elif damage_result.new_status == UnitStatus.DAMAGED:
            result['outcome'] = 'damaged'
        elif damage_result.new_status == UnitStatus.DISRUPTED:
            result['outcome'] = 'disrupted'
        else:
            result['outcome'] = 'no_effect'
        
        # Add notes from abilities
        result['notes'].extend(attack_mods.get('notes', []))
        result['notes'].extend(defense_mods.get('notes', []))
        
        return result
    
    def get_damage_modifiers(self, attacker, target, 
                             range_category: str, distance: int) -> Dict:
        """
        Get damage modifiers from abilities.
        
        Note: In A&A Miniatures, damage is determined by hits vs defense,
        not by a separate damage value. This method is for compatibility
        with the existing action_executor interface.
        
        Returns:
            Dictionary with 'bonus_damage' key (usually 0)
        """
        # Most abilities don't add bonus damage in A&A Miniatures
        # Damage is determined by hits vs defense thresholds
        return {'bonus_damage': 0}
    
    @staticmethod
    def format_attack_result(result: Dict) -> str:
        """Format attack result for display"""
        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"⚔️  {result['attacker']} attacks {result['target']}")
        lines.append(f"{'='*60}")
        lines.append(f"Range: {result['distance']} hexes ({result['range_category']})")
        
        if result.get('is_rear_attack'):
            lines.append("  [REAR ATTACK]")
        
        if result.get('notes'):
            lines.append("\n✨ Notes:")
            for note in result['notes']:
                lines.append(f"   • {note}")
        
        if result['outcome'] == 'no_attack':
            lines.append("\n❌ Cannot attack")
            return '\n'.join(lines)
        
        # Attack roll details
        ar = result.get('attack_result', {})
        if ar:
            lines.append(f"\n🎲 Attack: {ar.get('dice_rolled', 0)} dice (need {ar.get('hit_threshold', 4)}+)")
            lines.append(f"   Rolls: {ar.get('rolls', [])}")
            lines.append(f"   Successes: {ar.get('successes', 0)}")
        
        lines.append(f"\n🛡️  Defense: {result.get('effective_defense', 0)}")
        
        # Cover result
        cr = result.get('cover_result')
        if cr:
            status = "SUCCESS ✓" if cr['success'] else "FAILED ✗"
            lines.append(f"🌲 Cover: Roll {cr['roll']} vs {cr['threshold']}+ → {status}")
        
        # Damage result
        lines.append(f"\n💥 Hits: {result.get('hits', 0)}")
        
        dr = result.get('damage_result', {})
        if dr:
            lines.append(f"   Result: {dr.get('status_change', 'Unknown')}")
        
        # Outcome
        outcome_icons = {
            'miss': '❌',
            'no_effect': '➖',
            'disrupted': '⚠️',
            'damaged': '💔',
            'disrupted_and_damaged': '💔⚠️',
            'destroyed': '💥'
        }
        icon = outcome_icons.get(result['outcome'], '❓')
        lines.append(f"\n{icon} Outcome: {result['outcome'].upper()}")
        
        lines.append(f"{'='*60}")
        return '\n'.join(lines)


# Test the combat system
if __name__ == "__main__":
    from units import load_units
    
    print("=" * 70)
    print("AXIS & ALLIES MINIATURES - COMBAT SYSTEM TEST")
    print("=" * 70)
    
    # Load systems
    # Handle both filename formats
    import os
    ability_file = 'Axis_and_Allies_Unit_Data_for_Analysis_-_Special_Abilities.csv'
    if not os.path.exists(ability_file):
        ability_file = 'Axis and Allies Unit Data for Analysis - Special_Abilities.csv'
    
    ability_system = AbilitySystem(ability_file)
    combat_system = CombatSystem(ability_system)
    
    # Load units - need to handle filename
    unit_file = 'Axis_and_Allies_Unit_Data_for_Analysis_-_Unit_Stats.csv'
    if not os.path.exists(unit_file):
        unit_file = 'Axis and Allies Unit Data for Analysis - Unit_Stats.csv'
    
    import csv
    units = []
    from units import Unit
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
    
    # Get test units
    soldiers = [u for u in units if u.unit_type == 'Soldier' and u.per_short > 0]
    vehicles = [u for u in units if u.unit_type == 'Vehicle' and u.veh_short > 0]
    
    if soldiers and vehicles:
        infantry = soldiers[0]
        tank = vehicles[0]
        
        print(f"\nTest Units:")
        print(f"  Infantry: {infantry.name} (Defense: {infantry.defense_front})")
        print(f"  Tank: {tank.name} (Defense: {tank.defense_front}/{tank.defense_rear})")
        
        # Test 1: Infantry vs Infantry (open terrain)
        print("\n" + "=" * 70)
        print("TEST 1: Infantry vs Infantry (Open Terrain)")
        print("=" * 70)
        
        result = combat_system.resolve_attack(
            infantry, soldiers[1] if len(soldiers) > 1 else infantry,
            distance=1,
            target_terrain='open'
        )
        print(CombatSystem.format_attack_result(result))
        
        # Test 2: Infantry vs Infantry (Forest - Cover)
        print("\n" + "=" * 70)
        print("TEST 2: Infantry vs Infantry (Forest - Cover)")
        print("=" * 70)
        
        result = combat_system.resolve_attack(
            infantry, soldiers[1] if len(soldiers) > 1 else infantry,
            distance=1,
            target_terrain='forest'
        )
        print(CombatSystem.format_attack_result(result))
        
        # Test 3: Tank vs Infantry
        print("\n" + "=" * 70)
        print("TEST 3: Tank vs Infantry")
        print("=" * 70)
        
        result = combat_system.resolve_attack(
            tank, infantry,
            distance=2,
            target_terrain='open'
        )
        print(CombatSystem.format_attack_result(result))
        
        # Test 4: Infantry vs Tank (rear attack)
        print("\n" + "=" * 70)
        print("TEST 4: Infantry vs Tank (Rear Attack)")
        print("=" * 70)
        
        result = combat_system.resolve_attack(
            infantry, tank,
            distance=1,
            target_terrain='open',
            is_rear_attack=True
        )
        print(CombatSystem.format_attack_result(result))
        
        # Test 5: Disrupted attacker
        print("\n" + "=" * 70)
        print("TEST 5: Disrupted Attacker (-1 penalty, needs 5+ to hit)")
        print("=" * 70)
        
        result = combat_system.resolve_attack(
            infantry, soldiers[1] if len(soldiers) > 1 else infantry,
            distance=1,
            attacker_state={'is_disrupted': True},
            target_terrain='open'
        )
        print(CombatSystem.format_attack_result(result))
        
        # Test 6: Disrupted target (defense reduced by 1)
        print("\n" + "=" * 70)
        print("TEST 6: Disrupted Target (Defense -1)")
        print("=" * 70)
        
        result = combat_system.resolve_attack(
            infantry, soldiers[1] if len(soldiers) > 1 else infantry,
            distance=1,
            target_state={'is_disrupted': True},
            target_terrain='open'
        )
        print(CombatSystem.format_attack_result(result))
    
    print("\n" + "=" * 70)
    print("COMBAT SYSTEM TEST COMPLETE")
    print("=" * 70)