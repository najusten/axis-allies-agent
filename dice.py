"""
Dice and Combat Resolution for Axis & Allies Miniatures

This module implements the authentic dice mechanics from the rulebook:
- Attack dice: roll N dice (N = attack value), each die hits on 4, 5, 6
- Disrupted/Damaged units: -1 penalty means hits on 5, 6 only
- Damage thresholds based on successes vs defense
- Cover saves: Infantry 4+, Vehicles 5+
- Status effects: Disrupted, Damaged (vehicles only), Destroyed
"""

import random
from typing import Tuple, List, Optional, Dict
from dataclasses import dataclass
from enum import Enum


class UnitStatus(Enum):
    """Status of a unit"""
    HEALTHY = "healthy"
    DISRUPTED = "disrupted"
    DAMAGED = "damaged"  # Vehicles only
    DISRUPTED_AND_DAMAGED = "disrupted_and_damaged"  # Vehicles only
    DESTROYED = "destroyed"


class UnitCategory(Enum):
    """Category of unit for damage resolution"""
    SOLDIER = "soldier"  # Includes infantry and artillery
    VEHICLE = "vehicle"
    AIRCRAFT = "aircraft"  # Treated like soldiers for damage


@dataclass
class AttackResult:
    """Result of an attack roll"""
    dice_rolled: int
    successes: int
    hit_threshold: int  # 4 normally, 5 if disrupted/damaged
    rolls: List[int]  # Individual die results
    
    def __str__(self):
        return f"Rolled {self.dice_rolled}d6 (need {self.hit_threshold}+): {self.successes} successes {self.rolls}"


@dataclass
class DamageResult:
    """Result of damage resolution"""
    hits_scored: int  # 0, 1, 2, or 3
    new_status: UnitStatus
    status_change: str  # Description of what happened
    counters_placed: List[str]  # Face-down counters placed
    
    def __str__(self):
        return f"{self.hits_scored} hit(s) -> {self.status_change}"


@dataclass 
class CoverResult:
    """Result of a cover save roll"""
    roll: int
    threshold: int  # 4 for soldiers, 5 for vehicles
    modifier: int  # -1 if attacker in same hex
    success: bool
    
    def __str__(self):
        mod_str = f" (modified by {self.modifier})" if self.modifier != 0 else ""
        return f"Cover roll: {self.roll}{mod_str} vs {self.threshold}+ -> {'SUCCESS' if self.success else 'FAILED'}"


class DiceSystem:
    """
    Handles all dice rolling and combat resolution for A&A Miniatures.
    
    Key mechanics:
    - Attack dice hit on 4, 5, 6 (50% each die)
    - Disrupted/Damaged penalty: hit on 5, 6 only (33% each die)
    - Penalties don't stack (disrupted + damaged = still just -1)
    - Soldiers: disrupted -> destroyed (no damaged state)
    - Vehicles: healthy -> disrupted -> damaged -> destroyed
    - Cover saves can cap damage at disruption only
    """
    
    # Standard hit thresholds
    NORMAL_HIT_THRESHOLD = 4  # Hit on 4, 5, 6
    PENALIZED_HIT_THRESHOLD = 5  # Hit on 5, 6 (when disrupted or damaged)
    
    # Cover save thresholds
    SOLDIER_COVER_THRESHOLD = 4  # Save on 4, 5, 6
    VEHICLE_COVER_THRESHOLD = 5  # Save on 5, 6
    
    def __init__(self, random_seed: Optional[int] = None):
        """
        Initialize the dice system.
        
        Args:
            random_seed: Optional seed for reproducible results (useful for testing)
        """
        if random_seed is not None:
            random.seed(random_seed)
    
    def roll_d6(self) -> int:
        """Roll a single d6"""
        return random.randint(1, 6)

    def roll_single_die(self) -> int:
        """Alias for roll_d6"""
        return self.roll_d6()

    def roll_dice(self, num_dice: int) -> List[int]:
        """Roll multiple d6s and return the results"""
        return [self.roll_d6() for _ in range(num_dice)]
    
    def get_hit_threshold(self, is_disrupted: bool = False, 
                          is_damaged: bool = False,
                          ability_modifier: int = 0) -> int:
        """
        Get the minimum die roll needed to score a hit.
        
        Args:
            is_disrupted: True if attacking unit is disrupted
            is_damaged: True if attacking unit is damaged
            ability_modifier: Modifier from special abilities (positive = harder to hit)
        
        Returns:
            Minimum die roll for a success (4 normally, 5 if disrupted/damaged)
        
        Note: Disrupted and damaged penalties don't stack per the rules.
        """
        base_threshold = self.NORMAL_HIT_THRESHOLD
        
        # Apply disrupted/damaged penalty (they don't stack)
        if is_disrupted or is_damaged:
            base_threshold = self.PENALIZED_HIT_THRESHOLD
        
        # Apply ability modifiers
        threshold = base_threshold + ability_modifier
        
        # Clamp to valid range (minimum 2, maximum 6)
        return max(2, min(6, threshold))
    
    def roll_attack(self, num_dice: int, 
                    is_disrupted: bool = False,
                    is_damaged: bool = False,
                    ability_modifier: int = 0) -> AttackResult:
        """
        Roll attack dice and count successes.
        
        Args:
            num_dice: Number of dice to roll (= attack value at range)
            is_disrupted: True if attacking unit is disrupted
            is_damaged: True if attacking unit is damaged
            ability_modifier: Modifier from special abilities
        
        Returns:
            AttackResult with roll details and success count
        """
        if num_dice <= 0:
            return AttackResult(
                dice_rolled=0,
                successes=0,
                hit_threshold=self.NORMAL_HIT_THRESHOLD,
                rolls=[]
            )
        
        threshold = self.get_hit_threshold(is_disrupted, is_damaged, ability_modifier)
        rolls = self.roll_dice(num_dice)
        successes = sum(1 for r in rolls if r >= threshold)
        
        return AttackResult(
            dice_rolled=num_dice,
            successes=successes,
            hit_threshold=threshold,
            rolls=rolls
        )
    
    def roll_cover_save(self, unit_category: UnitCategory,
                        attacker_same_hex: bool = False,
                        ability_modifier: int = 0) -> CoverResult:
        """
        Roll a cover save for a unit in defensive terrain.
        
        Args:
            unit_category: SOLDIER or VEHICLE (determines threshold)
            attacker_same_hex: True if attacker is in same hex (-1 penalty)
            ability_modifier: Modifier from special abilities
        
        Returns:
            CoverResult indicating success or failure
        """
        # Determine base threshold
        if unit_category == UnitCategory.SOLDIER or unit_category == UnitCategory.AIRCRAFT:
            base_threshold = self.SOLDIER_COVER_THRESHOLD
        else:
            base_threshold = self.VEHICLE_COVER_THRESHOLD
        
        # Apply modifiers
        modifier = 0
        if attacker_same_hex:
            modifier -= 1  # -1 penalty for same hex
        modifier += ability_modifier
        
        # The threshold stays the same, but we apply modifier to the roll
        # (or equivalently, to the threshold - same effect)
        effective_threshold = base_threshold - modifier
        
        roll = self.roll_d6()
        success = roll >= effective_threshold
        
        return CoverResult(
            roll=roll,
            threshold=base_threshold,
            modifier=modifier,
            success=success
        )
    
    def calculate_hits(self, successes: int, defense: int, superior_armor: int = 0) -> int:
        """
        Calculate number of hits based on successes vs defense.

        Args:
            successes: Number of successful attack dice
            defense: Target's defense value
            superior_armor: Superior Armor X value (need to exceed by X for 2 hits)

        Returns:
            Number of hits (0, 1, 2, or 3)
            - 0 hits: successes < defense
            - 1 hit: successes == defense (disrupted)
            - 2 hits: successes >= defense + X where X is superior_armor or 1 (damaged/destroyed)
            - 3 hits: successes >= defense * 2 (destroyed)
        """
        # Minimum threshold to score 2 hits (normally 1, but Superior Armor increases it)
        two_hit_threshold = superior_armor if superior_armor > 0 else 1

        if successes < defense:
            return 0
        elif successes >= defense * 2:
            return 3
        elif successes >= defense + two_hit_threshold:
            return 2
        else:  # successes >= defense but < defense + two_hit_threshold
            return 1
    
    def resolve_soldier_damage(self, hits: int, 
                               current_status: UnitStatus,
                               cover_success: bool = False) -> DamageResult:
        """
        Resolve damage for a Soldier unit (includes infantry and artillery).
        
        Soldiers have only: healthy -> disrupted -> destroyed
        No "damaged" state for soldiers.
        
        Args:
            hits: Number of hits scored (0-3)
            current_status: Current status of the unit
            cover_success: True if cover save succeeded (caps at disruption)
        
        Returns:
            DamageResult with new status and description
        """
        if hits == 0:
            return DamageResult(
                hits_scored=0,
                new_status=current_status,
                status_change="No effect (missed)",
                counters_placed=[]
            )
        
        # Cover success caps damage at disruption
        if cover_success:
            if current_status == UnitStatus.HEALTHY:
                return DamageResult(
                    hits_scored=1,
                    new_status=UnitStatus.DISRUPTED,
                    status_change="Disrupted (cover saved from worse)",
                    counters_placed=["disrupted"]
                )
            else:
                # Already disrupted, cover prevents additional damage
                return DamageResult(
                    hits_scored=0,
                    new_status=current_status,
                    status_change="No additional effect (cover save)",
                    counters_placed=[]
                )
        
        # No cover - apply full damage
        if current_status == UnitStatus.HEALTHY:
            if hits >= 2:
                # 2+ hits destroys a soldier
                return DamageResult(
                    hits_scored=hits,
                    new_status=UnitStatus.DESTROYED,
                    status_change="DESTROYED",
                    counters_placed=["disrupted", "destroyed"]
                )
            else:
                # 1 hit disrupts
                return DamageResult(
                    hits_scored=1,
                    new_status=UnitStatus.DISRUPTED,
                    status_change="Disrupted",
                    counters_placed=["disrupted"]
                )
        
        elif current_status == UnitStatus.DISRUPTED:
            # Any hit on disrupted soldier destroys it
            return DamageResult(
                hits_scored=hits,
                new_status=UnitStatus.DESTROYED,
                status_change="DESTROYED (was disrupted)",
                counters_placed=["destroyed"]
            )
        
        else:
            # Already destroyed - shouldn't happen but handle gracefully
            return DamageResult(
                hits_scored=0,
                new_status=UnitStatus.DESTROYED,
                status_change="Already destroyed",
                counters_placed=[]
            )
    
    def resolve_vehicle_damage(self, hits: int,
                               current_status: UnitStatus,
                               cover_success: bool = False) -> DamageResult:
        """
        Resolve damage for a Vehicle unit.
        
        Vehicles have: healthy -> disrupted -> damaged -> destroyed
        Can also be disrupted+damaged simultaneously.
        
        Args:
            hits: Number of hits scored (0-3)
            current_status: Current status of the unit
            cover_success: True if cover save succeeded (caps at +1 status level)
        
        Returns:
            DamageResult with new status and description
        """
        if hits == 0:
            return DamageResult(
                hits_scored=0,
                new_status=current_status,
                status_change="No effect (missed)",
                counters_placed=[]
            )
        
        # Cover success caps damage at +1 status level
        if cover_success:
            if current_status == UnitStatus.HEALTHY:
                return DamageResult(
                    hits_scored=1,
                    new_status=UnitStatus.DISRUPTED,
                    status_change="Disrupted (cover saved from worse)",
                    counters_placed=["disrupted"]
                )
            elif current_status == UnitStatus.DISRUPTED:
                return DamageResult(
                    hits_scored=1,
                    new_status=UnitStatus.DAMAGED,
                    status_change="Damaged (cover saved from destruction)",
                    counters_placed=["damaged"]
                )
            elif current_status == UnitStatus.DAMAGED:
                # Cover can't save a damaged vehicle from destruction
                return DamageResult(
                    hits_scored=hits,
                    new_status=UnitStatus.DESTROYED,
                    status_change="DESTROYED (damaged vehicle can't be saved)",
                    counters_placed=["destroyed"]
                )
            elif current_status == UnitStatus.DISRUPTED_AND_DAMAGED:
                # Same as damaged - can't save
                return DamageResult(
                    hits_scored=hits,
                    new_status=UnitStatus.DESTROYED,
                    status_change="DESTROYED (damaged vehicle can't be saved)",
                    counters_placed=["destroyed"]
                )
            else:
                return DamageResult(
                    hits_scored=0,
                    new_status=current_status,
                    status_change="Already destroyed",
                    counters_placed=[]
                )
        
        # No cover - apply full damage based on hits
        if current_status == UnitStatus.HEALTHY:
            if hits >= 3:
                return DamageResult(
                    hits_scored=3,
                    new_status=UnitStatus.DESTROYED,
                    status_change="DESTROYED",
                    counters_placed=["disrupted", "damaged", "destroyed"]
                )
            elif hits == 2:
                return DamageResult(
                    hits_scored=2,
                    new_status=UnitStatus.DISRUPTED_AND_DAMAGED,
                    status_change="Disrupted and Damaged",
                    counters_placed=["disrupted", "damaged"]
                )
            else:  # hits == 1
                return DamageResult(
                    hits_scored=1,
                    new_status=UnitStatus.DISRUPTED,
                    status_change="Disrupted",
                    counters_placed=["disrupted"]
                )
        
        elif current_status == UnitStatus.DISRUPTED:
            if hits >= 2:
                return DamageResult(
                    hits_scored=hits,
                    new_status=UnitStatus.DESTROYED,
                    status_change="DESTROYED (was disrupted)",
                    counters_placed=["damaged", "destroyed"]
                )
            else:  # hits == 1
                return DamageResult(
                    hits_scored=1,
                    new_status=UnitStatus.DISRUPTED_AND_DAMAGED,
                    status_change="Damaged (was disrupted)",
                    counters_placed=["damaged"]
                )
        
        elif current_status == UnitStatus.DAMAGED:
            if hits >= 2:
                return DamageResult(
                    hits_scored=hits,
                    new_status=UnitStatus.DESTROYED,
                    status_change="DESTROYED (was damaged)",
                    counters_placed=["disrupted", "destroyed"]
                )
            else:  # hits == 1
                return DamageResult(
                    hits_scored=1,
                    new_status=UnitStatus.DISRUPTED_AND_DAMAGED,
                    status_change="Disrupted (was damaged)",
                    counters_placed=["disrupted"]
                )
        
        elif current_status == UnitStatus.DISRUPTED_AND_DAMAGED:
            # Any hit destroys
            return DamageResult(
                hits_scored=hits,
                new_status=UnitStatus.DESTROYED,
                status_change="DESTROYED (was disrupted and damaged)",
                counters_placed=["destroyed"]
            )
        
        else:
            return DamageResult(
                hits_scored=0,
                new_status=UnitStatus.DESTROYED,
                status_change="Already destroyed",
                counters_placed=[]
            )
    
    def resolve_damage(self, hits: int,
                       unit_category: UnitCategory,
                       current_status: UnitStatus,
                       cover_success: bool = False) -> DamageResult:
        """
        Resolve damage based on unit category.
        
        Args:
            hits: Number of hits scored
            unit_category: SOLDIER, VEHICLE, or AIRCRAFT
            current_status: Current status of the unit
            cover_success: True if cover save succeeded
        
        Returns:
            DamageResult with new status
        """
        if unit_category == UnitCategory.VEHICLE:
            return self.resolve_vehicle_damage(hits, current_status, cover_success)
        else:
            # Soldiers and Aircraft use soldier damage rules
            return self.resolve_soldier_damage(hits, current_status, cover_success)
    
    def resolve_full_attack(self, 
                            attack_dice: int,
                            target_defense: int,
                            target_category: UnitCategory,
                            target_status: UnitStatus,
                            attacker_disrupted: bool = False,
                            attacker_damaged: bool = False,
                            target_has_cover: bool = False,
                            attacker_same_hex: bool = False,
                            attack_ability_mod: int = 0,
                            cover_ability_mod: int = 0) -> Dict:
        """
        Resolve a complete attack from start to finish.
        
        Args:
            attack_dice: Number of attack dice to roll
            target_defense: Target's defense value
            target_category: Target's unit category
            target_status: Target's current status
            attacker_disrupted: True if attacker is disrupted
            attacker_damaged: True if attacker is damaged
            target_has_cover: True if target is in cover terrain
            attacker_same_hex: True if attacker in same hex as target
            attack_ability_mod: Attack modifier from abilities
            cover_ability_mod: Cover save modifier from abilities
        
        Returns:
            Dictionary with complete attack resolution details
        """
        # Step 1: Roll cover save if applicable
        cover_result = None
        cover_success = False
        
        if target_has_cover:
            cover_result = self.roll_cover_save(
                target_category,
                attacker_same_hex,
                cover_ability_mod
            )
            cover_success = cover_result.success
        
        # Step 2: Roll attack dice
        attack_result = self.roll_attack(
            attack_dice,
            attacker_disrupted,
            attacker_damaged,
            attack_ability_mod
        )
        
        # Step 3: Calculate hits
        # Note: If target is disrupted or damaged, defense is reduced by 1
        effective_defense = target_defense
        if target_status in [UnitStatus.DISRUPTED, UnitStatus.DAMAGED, 
                            UnitStatus.DISRUPTED_AND_DAMAGED]:
            effective_defense = max(1, target_defense - 1)
        
        hits = self.calculate_hits(attack_result.successes, effective_defense)
        
        # Step 4: Resolve damage
        damage_result = self.resolve_damage(
            hits,
            target_category,
            target_status,
            cover_success
        )
        
        return {
            'attack_result': attack_result,
            'cover_result': cover_result,
            'effective_defense': effective_defense,
            'hits': hits,
            'damage_result': damage_result,
            'target_destroyed': damage_result.new_status == UnitStatus.DESTROYED
        }

    def roll_movement(self, ability_modifier: int = 0) -> Tuple[int, bool]:
        """
        Roll a movement roll (for entering difficult terrain).

        Movement rolls succeed on 4+ by default.

        Args:
            ability_modifier: Modifier from abilities like Robust (+1) or Mountaineering (+1)

        Returns:
            Tuple of (roll_result, success)
        """
        roll = self.roll_d6()
        threshold = 4 - ability_modifier  # 4+ normally, 3+ with +1 modifier
        success = roll >= threshold
        return roll, success


def get_unit_category(unit) -> UnitCategory:
    """
    Determine unit category from a Unit object.
    
    Args:
        unit: Unit object with unit_type attribute
    
    Returns:
        UnitCategory enum value
    """
    unit_type = getattr(unit, 'unit_type', 'Soldier')
    
    if unit_type == 'Vehicle':
        return UnitCategory.VEHICLE
    elif unit_type == 'Aircraft':
        return UnitCategory.AIRCRAFT
    else:
        # Soldier, Artillery treated as soldiers
        return UnitCategory.SOLDIER


def get_unit_status(unit_state) -> UnitStatus:
    """
    Get UnitStatus from a UnitState object.
    
    Args:
        unit_state: UnitState object with is_disrupted, is_damaged attributes
    
    Returns:
        UnitStatus enum value
    """
    is_disrupted = getattr(unit_state, 'is_disrupted', False)
    is_damaged = getattr(unit_state, 'is_damaged', False)
    
    if is_disrupted and is_damaged:
        return UnitStatus.DISRUPTED_AND_DAMAGED
    elif is_damaged:
        return UnitStatus.DAMAGED
    elif is_disrupted:
        return UnitStatus.DISRUPTED
    else:
        return UnitStatus.HEALTHY


# Demo/Test
if __name__ == "__main__":
    print("=" * 70)
    print("AXIS & ALLIES MINIATURES - DICE SYSTEM TEST")
    print("=" * 70)
    
    dice = DiceSystem(random_seed=42)  # Reproducible for testing
    
    # Test 1: Basic attack roll
    print("\n--- TEST 1: Basic Attack Roll ---")
    result = dice.roll_attack(num_dice=8, is_disrupted=False)
    print(f"8 dice, normal: {result}")
    
    result = dice.roll_attack(num_dice=8, is_disrupted=True)
    print(f"8 dice, disrupted (-1 penalty): {result}")
    
    # Test 2: Hit calculation
    print("\n--- TEST 2: Hit Calculation ---")
    for successes in range(0, 10):
        hits = dice.calculate_hits(successes, defense=4)
        print(f"  {successes} successes vs defense 4 = {hits} hit(s)")
    
    # Test 3: Soldier damage resolution
    print("\n--- TEST 3: Soldier Damage ---")
    for hits in range(0, 4):
        result = dice.resolve_soldier_damage(hits, UnitStatus.HEALTHY, cover_success=False)
        print(f"  {hits} hits on healthy soldier: {result}")
    
    # Test 4: Vehicle damage resolution
    print("\n--- TEST 4: Vehicle Damage ---")
    for hits in range(0, 4):
        result = dice.resolve_vehicle_damage(hits, UnitStatus.HEALTHY, cover_success=False)
        print(f"  {hits} hits on healthy vehicle: {result}")
    
    # Test 5: Cover saves
    print("\n--- TEST 5: Cover Saves ---")
    for _ in range(5):
        soldier_cover = dice.roll_cover_save(UnitCategory.SOLDIER)
        vehicle_cover = dice.roll_cover_save(UnitCategory.VEHICLE)
        print(f"  Soldier: {soldier_cover}")
        print(f"  Vehicle: {vehicle_cover}")
    
    # Test 6: Full attack resolution
    print("\n--- TEST 6: Full Attack Resolution ---")
    dice = DiceSystem(random_seed=None)  # Random for this test
    
    for i in range(3):
        print(f"\nAttack {i+1}:")
        result = dice.resolve_full_attack(
            attack_dice=10,
            target_defense=4,
            target_category=UnitCategory.VEHICLE,
            target_status=UnitStatus.HEALTHY,
            target_has_cover=True
        )
        print(f"  Attack: {result['attack_result']}")
        print(f"  Cover: {result['cover_result']}")
        print(f"  Effective defense: {result['effective_defense']}")
        print(f"  Hits: {result['hits']}")
        print(f"  Damage: {result['damage_result']}")
        print(f"  Destroyed: {result['target_destroyed']}")
    
    print("\n" + "=" * 70)
    print("DICE SYSTEM TEST COMPLETE")
    print("=" * 70)