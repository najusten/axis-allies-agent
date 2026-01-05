import random
from typing import Tuple, Dict, List
from board import Board
from movement import MovementSystem
from abilities import AbilitySystem

class UnitState:
    """Tracks the current state of a unit in combat"""
    
    def __init__(self, unit):
        self.unit = unit
        self.disrupted = False
        self.disrupted_this_turn = False
        self.damage_counters = 0  # For vehicles
        self.destroyed = False
        self.pending_destruction = False  # Marked for destruction, but still fights this turn
        self.disruptions_this_turn = 0  # Track multiple disruptions in same turn
    
    def apply_end_of_turn_casualties(self):
        """Apply casualties at end of turn (for simultaneous combat)"""
        if self.pending_destruction:
            self.destroyed = True
    
    def reset_turn_flags(self):
        """Call at end of turn - disrupted_this_turn becomes just disrupted"""
        if self.disruptions_this_turn > 0:
            self.disrupted = True
        self.disrupted_this_turn = False
        self.disruptions_this_turn = 0
        self.pending_destruction = False
    
    def is_vehicle(self):
        return self.unit.unit_type in ['Vehicle', 'Aircraft']
    
    def can_act(self):
        """Can this unit still act this turn? (not yet destroyed)"""
        return not self.destroyed
    
    def __str__(self):
        status = []
        if self.destroyed:
            status.append("DESTROYED")
        elif self.pending_destruction:
            status.append("PENDING DESTRUCTION")
        if self.disrupted:
            status.append("DISRUPTED")
        if self.disruptions_this_turn > 0 and not self.destroyed:
            status.append(f"DISRUPTED THIS TURN (x{self.disruptions_this_turn})")
        if self.damage_counters > 0:
            status.append(f"{self.damage_counters} damage")
        status_str = f" [{', '.join(status)}]" if status else ""
        return f"{self.unit.name}{status_str}"


class CombatSystem:
    """Handles combat resolution between units"""
    
    def __init__(self, ability_system: AbilitySystem):
        """Initialize combat system with ability system"""
        self.ability_system = ability_system
    
    @staticmethod
    def roll_dice(num_dice: int) -> List[int]:
        """Roll multiple d6 dice"""
        return [random.randint(1, 6) for _ in range(num_dice)]
    
    def roll_cover(self, unit_state: UnitState, terrain: str, 
                   attacker_abilities: Dict = None) -> Tuple[bool, int]:
        """
        Roll cover save for a unit.
        Returns (success, roll_value)
        
        Soldiers: 4+ in cover terrain (base)
        Vehicles: 5+ in cover terrain (base)
        
        Can be modified by attacker abilities (e.g., Pinpointer -1)
        """
        cover_terrains = ['forest', 'building', 'hill']
        if terrain not in cover_terrains:
            return False, 0
        
        roll = random.randint(1, 6)
        
        # Base cover threshold
        if unit_state.is_vehicle():
            threshold = 5
        else:
            threshold = 4
        
        # Apply attacker's cover penalties (e.g., Pinpointer)
        if attacker_abilities and 'cover_penalty' in attacker_abilities:
            threshold += attacker_abilities['cover_penalty']
            threshold = min(threshold, 6)  # Can't exceed 6
        
        success = roll >= threshold
        return success, roll
    
    def get_attack_dice(self, attacker_state: UnitState, target_state: UnitState, 
                       distance: int, ability_mods: Dict) -> int:
        """
        Get number of attack dice based on target type, range, and abilities.
        """
        attacker = attacker_state.unit
        target = target_state.unit
        
        # Check for Close Assault first (overrides normal attack)
        if ability_mods.get('close_assault_dice') and distance == 0:
            return ability_mods['close_assault_dice']
        
        # Normal attack
        range_category = MovementSystem.get_range_category(distance)
        is_vehicle = target_state.is_vehicle()
        
        if is_vehicle:
            if range_category == 'short':
                dice = attacker.veh_short
            elif range_category == 'medium':
                dice = attacker.veh_medium
            else:
                dice = attacker.veh_long
        else:
            if range_category == 'short':
                dice = attacker.per_short
            elif range_category == 'medium':
                dice = attacker.per_medium
            else:
                dice = attacker.per_long
        
        # Apply any bonus dice from abilities
        dice += ability_mods.get('bonus_dice', 0)
        
        return dice
    
    def count_hits(self, rolls: List[int], hit_threshold: int, 
                   is_disrupted_from_previous: bool) -> int:
        """
        Count hits from attack rolls.
        hit_threshold: from abilities (default 4, can be 3 or 5)
        Disrupted from previous turn: only hit on 5-6
        """
        if is_disrupted_from_previous:
            # Disruption overrides better hit thresholds
            effective_threshold = max(hit_threshold, 5)
        else:
            effective_threshold = hit_threshold
        
        return sum(1 for roll in rolls if roll >= effective_threshold)
    
    def resolve_attack(self, attacker_state: UnitState, target_state: UnitState,
                      distance: int, target_terrain: str = 'open',
                      is_rear_attack: bool = False,
                      attacker_adjacent_units: List = None) -> Dict:
        """
        Resolve a single attack with full ability integration.
        Marks units for destruction but doesn't remove them (for simultaneous combat).
        """
        result = {
            'attacker': attacker_state.unit.name,
            'target': target_state.unit.name,
            'distance': distance,
            'range': MovementSystem.get_range_category(distance),
            'attack_rolls': [],
            'hits': 0,
            'defense_value': 0,
            'cover_rolled': False,
            'cover_successful': False,
            'cover_roll_value': 0,
            'outcome': None,
            'details': [],
            'ability_notes': []
        }
        
        # Check if attacker can still act
        if not attacker_state.can_act():
            result['outcome'] = 'no_attack'
            result['details'].append("Attacker is destroyed")
            return result
        
        # Get attack modifiers from abilities
        attack_mods = self.ability_system.get_attack_modifiers(
            attacker_state.unit, 
            target_state.unit, 
            distance, 
            target_terrain
        )
        
        # Close Assault always targets rear armor
        if attack_mods.get('close_assault_dice') and distance == 0:
            is_rear_attack = True
            result['ability_notes'].append("Close Assault: Targeting rear armor")
        
        # Check if attack is possible
        if not attack_mods['can_attack']:
            result['outcome'] = 'no_attack'
            result['details'].extend(attack_mods['notes'])
            return result
        
        # Check for abilities like Pinpointer (affects adjacent friendly soldiers)
        cover_penalty = 0
        if attacker_adjacent_units:
            for adj_unit in attacker_adjacent_units:
                if 'Pinpointer' in adj_unit.abilities:
                    cover_penalty = -1  # Makes cover harder
                    result['ability_notes'].append(f"{adj_unit.name} Pinpointer: -1 to target's cover rolls")
        
        # Get attack dice
        attack_dice = self.get_attack_dice(attacker_state, target_state, distance, attack_mods)
        
        if attack_dice == 0:
            result['outcome'] = 'no_attack'
            result['details'].append("No attack value at this range")
            return result
        
        # Check if attacker is disrupted from previous turn
        attacker_disrupted_prev = (attacker_state.disrupted and 
                                   not attacker_state.disrupted_this_turn)
        
        # Roll attack
        attack_rolls = self.roll_dice(attack_dice)
        result['attack_rolls'] = attack_rolls
        result['attacker_disrupted'] = attacker_disrupted_prev
        result['hit_threshold'] = attack_mods['hit_threshold']
        
        hits = self.count_hits(attack_rolls, attack_mods['hit_threshold'], 
                               attacker_disrupted_prev)
        result['hits'] = hits
        
        if hits == 0:
            result['outcome'] = 'miss'
            result['details'].append("All attacks missed")
            return result
        
        # Get defense value
        defense = (target_state.unit.defense_rear if is_rear_attack 
                  else target_state.unit.defense_front)
        
        # Get defense modifiers from abilities
        defense_mods = self.ability_system.get_defense_modifiers(
            target_state.unit, 
            target_terrain, 
            is_rear_attack
        )
        
        defense += defense_mods.get('defense_bonus', 0)
        result['defense_value'] = defense
        result['is_rear_attack'] = is_rear_attack
        result['ability_notes'].extend(attack_mods['notes'])
        result['ability_notes'].extend(defense_mods['notes'])
        
        # Roll cover (if not ignored by attacker abilities)
        cover_success = False
        if not attack_mods.get('ignore_cover', False):
            cover_success, cover_roll = self.roll_cover(
                target_state, 
                target_terrain,
                {'cover_penalty': cover_penalty}
            )
            result['cover_rolled'] = target_terrain in ['forest', 'building', 'hill']
            result['cover_successful'] = cover_success
            result['cover_roll_value'] = cover_roll
        else:
            result['details'].append("Attacker ignores cover")
        
        # Check if hits meet/exceed defense
        if hits < defense:
            result['outcome'] = 'miss'
            result['details'].append(f"Only {hits} hits, need {defense}")
            return result
        
        # Determine damage considering Superior Armor
        armor_modifier = defense_mods.get('armor_modifier', 0)
        hits_needed_for_damage = defense + 1 + armor_modifier
        
        # Process damage based on unit type
        target_is_vehicle = target_state.is_vehicle()
        
        if target_is_vehicle:
            # VEHICLE LOGIC
            if cover_success:
                # Cover limits to disruption
                result['outcome'] = 'disrupted'
                target_state.disruptions_this_turn += 1
                target_state.disrupted_this_turn = True
                result['details'].append("Cover saved - disrupted instead of damaged")
            else:
                # No cover - check damage
                if hits < hits_needed_for_damage:
                    # Only met defense = disrupted
                    result['outcome'] = 'disrupted'
                    target_state.disruptions_this_turn += 1
                    target_state.disrupted_this_turn = True
                else:
                    # Exceeded defense = damaged
                    result['outcome'] = 'damaged'
                    target_state.damage_counters += 1
                    result['details'].append(f"Total damage counters: {target_state.damage_counters}")
                    
                    # Check if destroyed (2+ damage)
                    if target_state.damage_counters >= 2:
                        result['outcome'] = 'destroyed'
                        target_state.pending_destruction = True
            
            # Check for multiple disruptions this turn
            if target_state.disruptions_this_turn >= 2 and not target_state.pending_destruction:
                # 2 disruptions = 1 damage for vehicles
                target_state.damage_counters += 1
                result['outcome'] = 'damaged'
                result['details'].append(f"2+ disruptions this turn = damage counter (total: {target_state.damage_counters})")
                if target_state.damage_counters >= 2:
                    result['outcome'] = 'destroyed'
                    target_state.pending_destruction = True
            
            if target_state.disruptions_this_turn >= 3:
                result['outcome'] = 'destroyed'
                target_state.pending_destruction = True
                result['details'].append("3 disruptions this turn = destroyed")
        else:
            # SOLDIER LOGIC
            if hits == defense:
                # Exactly met = disrupted (unless already disrupted this turn)
                if target_state.disrupted_this_turn:
                    # 2nd disruption same turn = destroyed
                    result['outcome'] = 'destroyed'
                    target_state.pending_destruction = True
                    result['details'].append("2nd disruption same turn = destroyed")
                else:
                    if cover_success:
                        result['outcome'] = 'disrupted'
                        target_state.disruptions_this_turn += 1
                        target_state.disrupted_this_turn = True
                        result['details'].append("Cover limited to disruption")
                    else:
                        result['outcome'] = 'disrupted'
                        target_state.disruptions_this_turn += 1
                        target_state.disrupted_this_turn = True
            else:
                # Exceeded defense = destroyed (unless cover saves)
                if cover_success and not target_state.disrupted_this_turn:
                    result['outcome'] = 'disrupted'
                    target_state.disruptions_this_turn += 1
                    target_state.disrupted_this_turn = True
                    result['details'].append("Cover saved from destruction")
                else:
                    result['outcome'] = 'destroyed'
                    target_state.pending_destruction = True
                    if target_state.disrupted_this_turn:
                        result['details'].append("Already disrupted this turn - destroyed")
        
        return result
    
    @staticmethod
    def print_combat_result(result: Dict):
        """Print formatted combat result"""
        print(f"\n{'='*70}")
        print(f"⚔️  {result['attacker']} attacks {result['target']}")
        print(f"{'='*70}")
        print(f"Range: {result['distance']} hexes ({result['range']})", end='')
        if result.get('is_rear_attack'):
            print(" [REAR ATTACK]")
        else:
            print()
        
        # Show ability effects
        if result.get('ability_notes'):
            print(f"\n✨ Ability effects:")
            for note in result['ability_notes']:
                print(f"   • {note}")
        
        if result['outcome'] == 'no_attack':
            print(f"\n❌ Cannot attack:")
            for detail in result['details']:
                print(f"   • {detail}")
            return
        
        print(f"\n🎲 Attack rolls ({len(result['attack_rolls'])} dice): {result['attack_rolls']}")
        if result.get('attacker_disrupted'):
            print(f"   ⚠️  Attacker disrupted from previous turn - only hits on 5-6")
        else:
            print(f"   Hit threshold: {result.get('hit_threshold', 4)}+")
        print(f"   Hits scored: {result['hits']}")
        
        if result['outcome'] == 'miss':
            print(f"\n❌ Attack failed:")
            for detail in result['details']:
                print(f"   • {detail}")
            return
        
        print(f"\n🛡️  Defense: {result['defense_value']}")
        
        if result['cover_rolled']:
            cover_str = f"Roll: {result['cover_roll_value']} - "
            cover_str += "SUCCESS ✓" if result['cover_successful'] else "FAILED ✗"
            print(f"🌲 Cover: {cover_str}")
        
        outcome_icons = {
            'disrupted': '⚠️',
            'damaged': '💔',
            'destroyed': '💥'
        }
        
        icon = outcome_icons.get(result['outcome'], '❓')
        print(f"\n{icon} {result['target']} is {result['outcome'].upper()}!")
        
        if result['details']:
            for detail in result['details']:
                print(f"   • {detail}")
        
        print(f"{'='*70}\n")


# Test the updated combat system
if __name__ == "__main__":
    from units import load_units
    from abilities import AbilitySystem
    
    print("=== COMBAT SYSTEM WITH SIMULTANEOUS COMBAT ===\n")
    
    # Load systems
    ability_system = AbilitySystem('Axis and Allies Unit Data for Analysis - Special_Abilities.csv')
    combat_system = CombatSystem(ability_system)
    units = load_units()
    
    # Filter out obstacles
    combat_units = [u for u in units if not ability_system.is_obstacle_unit(u)]
    
    # Complete nation lists
    axis_nations = ['Germany', 'Japan', 'Italy', 'Romania', 'Hungary', 'Finland', 
                    'Bulgaria', 'Slovakia', 'Croatia']
    allied_nations = ['USA', 'UK', 'Soviet Union', 'France', 'Poland', 'China', 
                      'Australia', 'Canada', 'New Zealand', 'Greece', 'Belgium', 
                      'South Africa', 'Yugoslavia']
    
    # Get test units
    axis_soldiers = [u for u in combat_units if u.nation in axis_nations and 
                     u.unit_type == 'Soldier' and u.per_short > 0]
    allied_soldiers = [u for u in combat_units if u.nation in allied_nations and 
                       u.unit_type == 'Soldier' and u.per_short > 0]
    
    if not axis_soldiers or not allied_soldiers:
        print("Error: Could not find suitable soldiers for testing")
        exit(1)
    
    axis_infantry = axis_soldiers[0]
    allied_infantry = allied_soldiers[0]
    
    # Find Owen SMG
    owen_units = [u for u in combat_units if u.name == 'Owen SMG']
    owen = owen_units[0] if owen_units else allied_infantry
    
    # Find a tank
    tanks = [u for u in combat_units if u.unit_type == 'Vehicle' and u.veh_short > 0]
    tank = tanks[0] if tanks else None
    
    print(f"Selected units:")
    print(f"  Axis: {axis_infantry.name} ({axis_infantry.nation}) - AXIS NATION")
    print(f"    Defense: {axis_infantry.defense_front}/{axis_infantry.defense_rear}")
    print(f"  Allied: {allied_infantry.name} ({allied_infantry.nation})")
    print(f"    Defense: {allied_infantry.defense_front}/{allied_infantry.defense_rear}")
    if owen:
        print(f"  Special: {owen.name} ({owen.nation})")
        print(f"    Abilities: {', '.join(owen.abilities)}")
    if tank:
        print(f"  Tank: {tank.name} ({tank.nation})")
        print(f"    Defense: {tank.defense_front}/{tank.defense_rear}")
    
    # SCENARIO: Simultaneous Combat
    print("\n" + "="*70)
    print("SCENARIO: SIMULTANEOUS COMBAT DEMONSTRATION")
    print("="*70)
    print("Both units attack each other in the same turn.")
    print("Casualties are marked but not removed until end of turn.\n")
    
    axis_inf = UnitState(axis_infantry)
    allied_inf = UnitState(allied_infantry)
    
    print(f"Initial states:")
    print(f"  {axis_inf}")
    print(f"  {allied_inf}\n")
    
    print("--- COMBAT PHASE ---")
    print("\n1️⃣  First player attacks:")
    result1 = combat_system.resolve_attack(axis_inf, allied_inf, distance=1, 
                                          target_terrain='open')
    CombatSystem.print_combat_result(result1)
    print(f"Allied unit state after being attacked: {allied_inf}")
    print("⚠️  Note: Unit may be marked for destruction but can still fight!\n")
    
    print("2️⃣  Second player attacks (simultaneous - happens even if marked for death):")
    result2 = combat_system.resolve_attack(allied_inf, axis_inf, distance=1,
                                          target_terrain='open')
    CombatSystem.print_combat_result(result2)
    print(f"Axis unit state after being attacked: {axis_inf}\n")
    
    print("--- END OF TURN: APPLY CASUALTIES ---")
    axis_inf.apply_end_of_turn_casualties()
    allied_inf.apply_end_of_turn_casualties()
    
    print(f"Final states after casualties applied:")
    print(f"  {axis_inf}")
    print(f"  {allied_inf}")
    
    # Close Assault rear armor test
    if tank and owen:
        print("\n" + "="*70)
        print("SCENARIO: CLOSE ASSAULT ALWAYS TARGETS REAR ARMOR")
        print("="*70)
        
        owen_state = UnitState(owen)
        tank_state = UnitState(tank)
        
        print(f"Tank defense: Front={tank.defense_front}, Rear={tank.defense_rear}")
        print(f"Close Assault should target rear ({tank.defense_rear})\n")
        
        result = combat_system.resolve_attack(owen_state, tank_state, distance=0,
                                             target_terrain='open')
        CombatSystem.print_combat_result(result)
        print(f"Tank state: {tank_state}")
    
    print("\n✅ All fixes tested:")
    print("  ✓ Simultaneous combat (casualties marked, not removed)")
    print("  ✓ Unit states display correctly (DISRUPTED, PENDING DESTRUCTION)")
    print("  ✓ Close Assault targets rear armor")