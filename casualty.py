"""
Casualty Phase System for Axis & Allies Miniatures

Implements simultaneous combat through face-down/face-up hit counters.

Key Rules:
- Hits during Assault Phase are placed as FACE-DOWN counters
- Face-down counters don't affect units until flipped face-up
- Units with face-down Destroyed counters can still attack
- Casualty Phase flips counters and removes destroyed units

Casualty Phase Steps:
1. Remove current face-up Disrupted counters (not Damaged)
2. Flip all face-down counters face-up
3. Remove units with Destroyed counters
4. Apply Damaged status to vehicles with Damaged counters
5. Apply Disrupted status to units with Disrupted counters

Exception: Defensive Fire is IMMEDIATE (already handled separately)
"""

from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

from game_state import GameState, UnitState


class HitCounterType(Enum):
    """Types of hit counters"""
    DISRUPTED = "disrupted"
    DAMAGED = "damaged"
    DESTROYED = "destroyed"


@dataclass
class HitCounter:
    """Represents a single hit counter on a unit"""
    counter_type: HitCounterType
    face_up: bool = False
    
    def flip_face_up(self):
        """Flip the counter face-up"""
        self.face_up = True


@dataclass
class PendingHits:
    """Tracks pending (face-down) hits for a unit during a phase"""
    unit_id: str
    counters: List[HitCounter] = field(default_factory=list)
    
    def add_hit(self, counter_type: HitCounterType):
        """Add a face-down hit counter"""
        self.counters.append(HitCounter(counter_type, face_up=False))
    
    def get_face_down_count(self) -> int:
        """Count face-down counters"""
        return sum(1 for c in self.counters if not c.face_up)
    
    def has_destroyed(self) -> bool:
        """Check if unit has a destroyed counter"""
        return any(c.counter_type == HitCounterType.DESTROYED for c in self.counters)


class CasualtySystem:
    """
    Manages the simultaneous combat system through hit counters.
    
    During Assault Phase:
    - Attacks place face-down hit counters
    - Units are NOT removed until Casualty Phase
    - Units with face-down counters can still attack
    
    During Casualty Phase:
    - Face-up Disrupted counters from previous turn are removed
    - Face-down counters are flipped face-up
    - Destroyed units are removed
    - Status effects are applied
    """
    
    def __init__(self):
        # Track pending hits for each unit (by unit_id)
        self._pending_hits: Dict[str, PendingHits] = {}
        
        # Track face-up counters from previous turn (for clearing disrupted)
        self._face_up_disrupted: Set[str] = set()
        self._face_up_damaged: Set[str] = set()
    
    def reset_phase(self):
        """Reset pending hits for a new assault phase"""
        self._pending_hits.clear()
    
    def _get_or_create_pending(self, unit_id: str) -> PendingHits:
        """Get or create pending hits tracker for a unit"""
        if unit_id not in self._pending_hits:
            self._pending_hits[unit_id] = PendingHits(unit_id)
        return self._pending_hits[unit_id]
    
    def calculate_hit_counters(
        self,
        hits: int,
        unit_type: str,
        existing_face_down: int = 0
    ) -> List[HitCounterType]:
        """
        Calculate which hit counters to place based on number of hits.
        
        Args:
            hits: Number of hits scored (1, 2, or 3)
            unit_type: 'Soldier' or 'Vehicle'
            existing_face_down: Number of face-down counters already on this unit
        
        Returns:
            List of counter types to place
        """
        counters = []
        
        # Total counters that will exist after adding new ones
        for i in range(hits):
            counter_num = existing_face_down + i + 1  # 1-indexed
            
            if counter_num == 1:
                # First counter is always Disrupted
                counters.append(HitCounterType.DISRUPTED)
            elif counter_num == 2:
                if unit_type == 'Vehicle':
                    # Second counter for Vehicle is Damaged
                    counters.append(HitCounterType.DAMAGED)
                else:
                    # Second counter for Soldier is Destroyed
                    counters.append(HitCounterType.DESTROYED)
            elif counter_num >= 3:
                # Third+ counter is always Destroyed
                counters.append(HitCounterType.DESTROYED)
        
        return counters
    
    def record_hits(
        self,
        unit_id: str,
        unit_type: str,
        hits: int
    ) -> List[HitCounterType]:
        """
        Record hits against a unit (places face-down counters).
        
        Args:
            unit_id: ID of the unit being hit
            unit_type: 'Soldier' or 'Vehicle'
            hits: Number of hits scored (1, 2, or 3)
        
        Returns:
            List of counter types placed
        """
        pending = self._get_or_create_pending(unit_id)
        existing = pending.get_face_down_count()
        
        counter_types = self.calculate_hit_counters(hits, unit_type, existing)
        
        for ct in counter_types:
            pending.add_hit(ct)
        
        return counter_types
    
    def unit_has_pending_destroyed(self, unit_id: str) -> bool:
        """Check if a unit has a pending (face-down) Destroyed counter"""
        if unit_id not in self._pending_hits:
            return False
        return self._pending_hits[unit_id].has_destroyed()
    
    def get_pending_hits_summary(self, unit_id: str) -> Dict:
        """Get summary of pending hits for a unit"""
        if unit_id not in self._pending_hits:
            return {'disrupted': 0, 'damaged': 0, 'destroyed': 0}
        
        pending = self._pending_hits[unit_id]
        summary = {'disrupted': 0, 'damaged': 0, 'destroyed': 0}
        
        for counter in pending.counters:
            if not counter.face_up:
                if counter.counter_type == HitCounterType.DISRUPTED:
                    summary['disrupted'] += 1
                elif counter.counter_type == HitCounterType.DAMAGED:
                    summary['damaged'] += 1
                elif counter.counter_type == HitCounterType.DESTROYED:
                    summary['destroyed'] += 1
        
        return summary
    
    def resolve_casualty_phase(self, game_state: GameState) -> Dict:
        """
        Resolve the Casualty Phase.
        
        Steps:
        1. Remove face-up Disrupted counters from previous turn
        2. Flip face-down counters face-up
        3. Remove destroyed units
        4. Apply damaged/disrupted status
        
        Returns:
            Dictionary with results:
            - units_destroyed: List of unit IDs removed
            - units_damaged: List of unit IDs now damaged
            - units_disrupted: List of unit IDs now disrupted
            - disruption_cleared: List of unit IDs with disruption cleared
        """
        results = {
            'units_destroyed': [],
            'units_damaged': [],
            'units_disrupted': [],
            'disruption_cleared': []
        }
        
        # Step 1: Clear face-up disrupted from previous turn
        for unit_id in list(self._face_up_disrupted):
            unit_state = game_state.get_unit_state(unit_id)
            if unit_state and unit_state.is_disrupted:
                unit_state.is_disrupted = False
                results['disruption_cleared'].append(unit_id)
        self._face_up_disrupted.clear()
        
        # Note: face-up Damaged is NOT cleared (persists until destroyed)
        
        # Step 2 & 3 & 4: Process pending hits
        for unit_id, pending in self._pending_hits.items():
            unit_state = game_state.get_unit_state(unit_id)
            if not unit_state:
                continue
            
            # Check for destroyed
            if pending.has_destroyed():
                game_state.remove_unit(unit_id)
                results['units_destroyed'].append(unit_id)
                continue
            
            # Apply other statuses
            for counter in pending.counters:
                counter.flip_face_up()
                
                if counter.counter_type == HitCounterType.DISRUPTED:
                    unit_state.is_disrupted = True
                    self._face_up_disrupted.add(unit_id)
                    if unit_id not in results['units_disrupted']:
                        results['units_disrupted'].append(unit_id)
                
                elif counter.counter_type == HitCounterType.DAMAGED:
                    unit_state.is_damaged = True
                    self._face_up_damaged.add(unit_id)
                    if unit_id not in results['units_damaged']:
                        results['units_damaged'].append(unit_id)
        
        # Clear pending hits for next phase
        self._pending_hits.clear()
        
        return results
    
    def get_effective_defense(
        self,
        unit_state: UnitState,
        base_defense: int
    ) -> int:
        """
        Get effective defense considering FACE-UP status only.
        
        Face-down counters don't reduce defense until flipped.
        """
        defense = base_defense
        
        # Only face-up counters affect defense
        if unit_state.is_disrupted:
            defense = max(1, defense - 1)
        if unit_state.is_damaged:
            defense = max(1, defense - 1)
        
        return defense
    
    def can_unit_act(self, unit_state: UnitState) -> bool:
        """
        Check if a unit can still act (attack/move).
        
        Units with face-down counters can still act.
        Only face-up disruption prevents movement.
        """
        # Face-up disrupted units can't move but CAN attack
        # Face-down counters don't prevent anything
        return unit_state.is_alive


def demo_casualty_system():
    """Demonstrate the casualty system"""
    print("=" * 70)
    print("CASUALTY SYSTEM DEMONSTRATION")
    print("=" * 70)
    
    casualty_system = CasualtySystem()
    
    # Test hit counter calculation
    print("\n--- Hit Counter Calculation ---")
    
    test_cases = [
        (1, 'Soldier', 0, "Soldier takes 1 hit"),
        (2, 'Soldier', 0, "Soldier takes 2 hits"),
        (1, 'Vehicle', 0, "Vehicle takes 1 hit"),
        (2, 'Vehicle', 0, "Vehicle takes 2 hits"),
        (3, 'Vehicle', 0, "Vehicle takes 3 hits"),
        (1, 'Vehicle', 1, "Vehicle with 1 existing hit takes 1 more"),
        (1, 'Vehicle', 2, "Vehicle with 2 existing hits takes 1 more"),
    ]
    
    for hits, unit_type, existing, desc in test_cases:
        counters = casualty_system.calculate_hit_counters(hits, unit_type, existing)
        counter_names = [c.value for c in counters]
        print(f"  {desc}: {counter_names}")
    
    # Test recording hits
    print("\n--- Recording Hits ---")
    
    casualty_system.reset_phase()
    
    # Simulate combat
    counters1 = casualty_system.record_hits("soldier_1", "Soldier", 1)
    print(f"  Soldier_1 hit for 1: {[c.value for c in counters1]}")
    
    counters2 = casualty_system.record_hits("soldier_1", "Soldier", 1)
    print(f"  Soldier_1 hit again for 1: {[c.value for c in counters2]}")
    print(f"  Soldier_1 pending destroyed? {casualty_system.unit_has_pending_destroyed('soldier_1')}")
    
    counters3 = casualty_system.record_hits("tank_1", "Vehicle", 2)
    print(f"  Tank_1 hit for 2: {[c.value for c in counters3]}")
    print(f"  Tank_1 pending summary: {casualty_system.get_pending_hits_summary('tank_1')}")
    
    # Test simultaneous combat scenario
    print("\n--- Simultaneous Combat Scenario ---")
    print("Both units shoot each other in the same assault phase:")
    
    casualty_system.reset_phase()
    
    # Unit A shoots Unit B for kill
    casualty_system.record_hits("unit_b", "Soldier", 2)
    print("  Unit A shoots Unit B: 2 hits (would be destroyed)")
    print(f"  Unit B has pending destroyed? {casualty_system.unit_has_pending_destroyed('unit_b')}")
    
    # Unit B shoots Unit A (before casualty phase, B can still act!)
    casualty_system.record_hits("unit_a", "Soldier", 1)
    print("  Unit B shoots Unit A: 1 hit (disrupted)")
    print("  -> Unit B can fire back even though it will be destroyed!")
    
    print("\n  After Casualty Phase:")
    print("  -> Unit B is removed (destroyed)")
    print("  -> Unit A is disrupted (from B's shot)")
    
    print("\n" + "=" * 70)
    print("CASUALTY SYSTEM DEMONSTRATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    demo_casualty_system()