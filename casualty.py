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
    
    def __init__(self, dice=None):
        from dice import DiceSystem
        self.dice = dice if dice is not None else DiceSystem()
        # All per-game bookkeeping (pending hits, face-up counters) lives on
        # GameState so it is cloned with the state. This object is stateless.

    def reset_phase(self, game_state: GameState):
        """Discard pending hits (e.g. when abandoning a simulated phase)."""
        game_state.pending_hits.clear()

    def _get_or_create_pending(self, game_state: GameState, unit_id: str) -> PendingHits:
        """Get or create pending hits tracker for a unit"""
        if unit_id not in game_state.pending_hits:
            game_state.pending_hits[unit_id] = PendingHits(unit_id)
        return game_state.pending_hits[unit_id]
    
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
                if (unit_type or '').startswith('Vehicle'):     # "Vehicle Tank" etc.
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
        game_state: GameState,
        unit_id: str,
        unit_type: str,
        hits: int
    ) -> List[HitCounterType]:
        """
        Record hits against a unit (places face-down counters).
        
        Args:
            game_state: Game state that owns the pending-hit bookkeeping
            unit_id: ID of the unit being hit
            unit_type: 'Soldier' or 'Vehicle'
            hits: Number of hits scored (1, 2, or 3)
        
        Returns:
            List of counter types placed
        """
        pending = self._get_or_create_pending(game_state, unit_id)
        existing = pending.get_face_down_count()
        
        counter_types = self.calculate_hit_counters(hits, unit_type, existing)
        
        for ct in counter_types:
            pending.add_hit(ct)
        
        return counter_types
    
    def unit_has_pending_destroyed(self, game_state: GameState, unit_id: str) -> bool:
        """Check if a unit has a pending (face-down) Destroyed counter"""
        if unit_id not in game_state.pending_hits:
            return False
        return game_state.pending_hits[unit_id].has_destroyed()
    
    def get_pending_hits_summary(self, game_state: GameState, unit_id: str) -> Dict:
        """Get summary of pending hits for a unit (face-down counters by type)"""
        summary = {'disrupted': 0, 'damaged': 0, 'destroyed': 0, 'total': 0}
        if unit_id not in game_state.pending_hits:
            return summary
        
        pending = game_state.pending_hits[unit_id]
        
        for counter in pending.counters:
            if not counter.face_up:
                if counter.counter_type == HitCounterType.DISRUPTED:
                    summary['disrupted'] += 1
                elif counter.counter_type == HitCounterType.DAMAGED:
                    summary['damaged'] += 1
                elif counter.counter_type == HitCounterType.DESTROYED:
                    summary['destroyed'] += 1
        summary['total'] = summary['disrupted'] + summary['damaged'] + summary['destroyed']
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
        # Exception: Unreliable, Rapid Fire jammed, Overheat jammed disruption doesn't clear
        # Green: Must roll 4+ to remove Disrupted counter
        # Every face-up Disrupted counter recovers here, whether it was flipped at
        # the last casualty phase or placed face-up at once (defensive fire,
        # Ace/Antiair reactions, scenario setup).
        for unit_id, unit_state in list(game_state.units.items()):
            if unit_state.is_alive and unit_state.is_disrupted:
                # Check if disruption is sticky (from Unreliable, Rapid Fire, Overheat)
                if getattr(unit_state, 'unreliable_disrupted', False):
                    continue  # Never clears
                if getattr(unit_state, 'rapid_fire_jammed', False):
                    continue  # Doesn't clear from Rapid Fire
                if getattr(unit_state, 'overheat_jammed', False):
                    continue  # Doesn't clear from Overheat

                # Check for Green ability (must roll 4+ to remove Disrupted)
                unit_abilities = getattr(unit_state.unit, 'abilities', []) or []
                has_green = any(a.lower() == 'green' for a in unit_abilities)
                if has_green:
                    roll = self.dice.roll_d6()
                    if roll < 4:
                        continue  # Failed to remove Disrupted

                unit_state.is_disrupted = False
                results['disruption_cleared'].append(unit_id)
        game_state.face_up_disrupted.clear()
        
        # Note: face-up Damaged is NOT cleared (persists until destroyed)
        
        # Step 2 & 3 & 4: Process pending hits
        for unit_id, pending in list(game_state.pending_hits.items()):
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
                    game_state.face_up_disrupted.add(unit_id)
                    if unit_id not in results['units_disrupted']:
                        results['units_disrupted'].append(unit_id)
                
                elif counter.counter_type == HitCounterType.DAMAGED:
                    if unit_state.is_damaged:
                        # Rulebook: "If a damaged Vehicle would receive another
                        # Damaged counter, it gets a Destroyed counter instead."
                        game_state.remove_unit(unit_id)
                        results['units_destroyed'].append(unit_id)
                        break
                    unit_state.is_damaged = True
                    game_state.face_up_damaged.add(unit_id)
                    if unit_id not in results['units_damaged']:
                        results['units_damaged'].append(unit_id)
        
        # Clear pending hits for next phase
        game_state.pending_hits.clear()
        
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

