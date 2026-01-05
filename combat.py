import random
from typing import Tuple, Optional
from board import Board
from movement import MovementSystem

class CombatSystem:
    """Handles combat resolution between units"""
    
    @staticmethod
    def roll_dice(num_dice: int) -> list:
        """Roll multiple d6 dice"""
        return [random.randint(1, 6) for _ in range(num_dice)]
    
    @staticmethod
    def get_attack_dice(attacker, target, distance: int) -> int:
        """
        Get number of attack dice based on target type and range.
        Returns 0 if unit can't attack at this range/target type.
        """
        range_category = MovementSystem.get_range_category(distance)
        
        # Determine if target is vehicle or personnel
        is_vehicle = target.unit_type in ['Vehicle', 'Aircraft']
        
        if is_vehicle:
            # Attacking a vehicle
            if range_category == 'short':
                return attacker.veh_short
            elif range_category == 'medium':
                return attacker.veh_medium
            else:  # long
                return attacker.veh_long
        else:
            # Attacking personnel (soldier)
            if range_category == 'short':
                return attacker.per_short
            elif range_category == 'medium':
                return attacker.per_medium
            else:  # long
                return attacker.per_long
    
    @staticmethod
    def resolve_attack(attacker, target, distance: int, 
                      terrain_cover: str = 'open',
                      is_rear_attack: bool = False) -> dict:
        """
        Resolve a single attack.
        Returns a dict with attack results.
        """
        result = {
            'attacker': attacker.name,
            'target': target.name,
            'distance': distance,
            'range': MovementSystem.get_range_category(distance),
            'hit': False,
            'defense_successful': False,
            'disrupted': False,
            'destroyed': False,
            'attack_rolls': [],
            'defense_rolls': [],
            'hits_scored': 0
        }
        
        # Get attack dice
        attack_dice = CombatSystem.get_attack_dice(attacker, target, distance)
        
        if attack_dice == 0:
            result['error'] = f"{attacker.name} cannot attack {target.name} at this range/type"
            return result
        
        # Roll attack dice (hit on 4+)
        attack_rolls = CombatSystem.roll_dice(attack_dice)
        result['attack_rolls'] = attack_rolls
        hits = sum(1 for roll in attack_rolls if roll >= 4)
        result['hits_scored'] = hits
        
        if hits == 0:
            result['error'] = "All attack rolls missed!"
            return result
        
        result['hit'] = True
        
        # Determine defense value
        defense_value = target.defense_rear if is_rear_attack else target.defense_front
        
        # Apply terrain cover bonus (+1 to defense in forest/building)
        cover_bonus = 0
        if terrain_cover in ['forest', 'building', 'hill']:
            cover_bonus = 1
        
        effective_defense = (defense_value or 0) + cover_bonus
        
        # Roll defense dice (one per hit, need to roll >= defense value)
        defense_rolls = CombatSystem.roll_dice(hits)
        result['defense_rolls'] = defense_rolls
        result['defense_value'] = effective_defense
        result['cover_bonus'] = cover_bonus
        
        # Check if any defenses failed
        failed_defenses = sum(1 for roll in defense_rolls if roll < effective_defense)
        
        if failed_defenses > 0:
            # Unit is destroyed if it fails defense
            result['destroyed'] = True
        else:
            # All defenses succeeded - unit is disrupted
            result['defense_successful'] = True
            result['disrupted'] = True
        
        return result
    
    @staticmethod
    def print_combat_result(result: dict):
        """Print a formatted combat result"""
        print(f"\n{'='*60}")
        print(f"⚔️  {result['attacker']} attacks {result['target']}")
        print(f"{'='*60}")
        print(f"Range: {result['distance']} hexes ({result['range']})")
        
        if 'error' in result and not result['hit']:
            print(f"❌ {result['error']}")
            return
        
        print(f"\n🎲 Attack rolls: {result['attack_rolls']}")
        print(f"   Hits scored: {result['hits_scored']}")
        
        if result['hits_scored'] == 0:
            print(f"❌ {result['error']}")
            return
        
        print(f"\n🛡️  Defense value: {result['defense_value']}", end='')
        if result.get('cover_bonus', 0) > 0:
            print(f" (includes +{result['cover_bonus']} cover bonus)")
        else:
            print()
        
        print(f"🎲 Defense rolls: {result['defense_rolls']}")
        
        if result['destroyed']:
            print(f"\n💥 {result['target']} is DESTROYED!")
        elif result['disrupted']:
            print(f"\n⚠️  {result['target']} is DISRUPTED (all defenses succeeded)")
        
        print(f"{'='*60}\n")


# Test combat system
if __name__ == "__main__":
    from units import load_units
    from board import Board
    
    print("=== AXIS & ALLIES MINIATURES COMBAT SIMULATOR ===\n")
    
    # Load units
    units = load_units()
    
    # Get some test units
    infantry = [u for u in units if u.unit_type == 'Soldier' and u.per_short > 0][0]
    tank = [u for u in units if u.unit_type == 'Vehicle' and u.veh_short > 0][0]
    
    print(f"Selected units:")
    print(f"  Infantry: {infantry.name}")
    print(f"    - Attack vs Personnel: {infantry.per_short}/{infantry.per_medium}/{infantry.per_long} (S/M/L)")
    print(f"    - Defense: {infantry.defense_front}/{infantry.defense_rear} (F/R)")
    print(f"\n  Tank: {tank.name}")
    print(f"    - Attack vs Vehicle: {tank.veh_short}/{tank.veh_medium}/{tank.veh_long} (S/M/L)")
    print(f"    - Attack vs Personnel: {tank.per_short}/{tank.per_medium}/{tank.per_long} (S/M/L)")
    print(f"    - Defense: {tank.defense_front}/{tank.defense_rear} (F/R)")
    
    # Scenario 1: Infantry vs Infantry at short range in open terrain
    print("\n" + "="*60)
    print("SCENARIO 1: Infantry vs Infantry (short range, open terrain)")
    print("="*60)
    result = CombatSystem.resolve_attack(infantry, infantry, distance=1, terrain_cover='open')
    CombatSystem.print_combat_result(result)
    
    # Scenario 2: Tank vs Infantry at medium range
    print("\n" + "="*60)
    print("SCENARIO 2: Tank vs Infantry (medium range, open terrain)")
    print("="*60)
    result = CombatSystem.resolve_attack(tank, infantry, distance=3, terrain_cover='open')
    CombatSystem.print_combat_result(result)
    
    # Scenario 3: Tank vs Infantry in forest (cover bonus)
    print("\n" + "="*60)
    print("SCENARIO 3: Tank vs Infantry (short range, forest cover)")
    print("="*60)
    result = CombatSystem.resolve_attack(tank, infantry, distance=1, terrain_cover='forest')
    CombatSystem.print_combat_result(result)
    
    # Scenario 4: Infantry vs Tank (rear attack)
    print("\n" + "="*60)
    print("SCENARIO 4: Infantry vs Tank (short range, rear attack)")
    print("="*60)
    result = CombatSystem.resolve_attack(infantry, tank, distance=1, 
                                        terrain_cover='open', is_rear_attack=True)
    CombatSystem.print_combat_result(result)
    
    # Run multiple combats to show variability
    print("\n" + "="*60)
    print("TESTING COMBAT VARIABILITY (5 identical attacks)")
    print("="*60)
    print(f"{tank.name} attacks {infantry.name} at medium range, open terrain\n")
    
    destroyed_count = 0
    disrupted_count = 0
    miss_count = 0
    
    for i in range(5):
        result = CombatSystem.resolve_attack(tank, infantry, distance=3, terrain_cover='open')
        print(f"Attack {i+1}: ", end='')
        if not result['hit']:
            print("MISS")
            miss_count += 1
        elif result['destroyed']:
            print("DESTROYED")
            destroyed_count += 1
        elif result['disrupted']:
            print("DISRUPTED")
            disrupted_count += 1
    
    print(f"\nResults: {destroyed_count} destroyed, {disrupted_count} disrupted, {miss_count} missed")