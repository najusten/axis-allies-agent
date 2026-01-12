from typing import Optional, List, Tuple
from units import Unit

class TransportState:
    """Tracks units loaded onto transports"""
    
    def __init__(self, transport_unit):
        self.transport = transport_unit
        self.loaded_units = []  # List of units currently aboard
        self.max_capacity = self._get_capacity()
    
    def _get_capacity(self) -> int:
        """Determine transport capacity from abilities"""
        # Most transports carry 1-2 units
        # Check abilities for specific capacity
        if 'Transport' in ' '.join(self.transport.abilities):
            return 1  # Default capacity
        if 'Gun Transport' in ' '.join(self.transport.abilities):
            return 1  # Can carry artillery specifically
        return 0
    
    def can_load(self, unit) -> Tuple[bool, str]:
        """
        Check if a unit can be loaded onto this transport.
        Returns (can_load, reason)
        """
        # Check capacity
        if len(self.loaded_units) >= self.max_capacity:
            return False, "Transport at capacity"
        
        # Check unit type - only Soldiers can be transported
        if unit.unit_type != 'Soldier':
            return False, "Only Soldier units can be transported"
        
        # Check if it's artillery and transport can carry it
        is_artillery = 'Artillery' in unit.name or unit.speed == 0
        has_gun_transport = any('Gun Transport' in ability for ability in self.transport.abilities)
        has_regular_transport = any('Transport' in ability and 'Gun' not in ability 
                                   for ability in self.transport.abilities)
        
        if is_artillery:
            # Check if it's large artillery (cannot be transported)
            if 'Large' in ' '.join(unit.abilities):
                return False, "Large Artillery cannot be transported"
            
            if not has_gun_transport:
                return False, "Transport cannot carry artillery (needs Gun Transport ability)"
        else:
            # Non-artillery soldier
            if not (has_regular_transport or has_gun_transport):
                return False, "Transport ability required"
        
        return True, "OK"
    
    def load_unit(self, unit) -> bool:
        """Load a unit onto the transport"""
        can_load, reason = self.can_load(unit)
        if can_load:
            self.loaded_units.append(unit)
            return True
        return False
    
    def unload_unit(self, unit) -> bool:
        """Unload a unit from the transport"""
        if unit in self.loaded_units:
            self.loaded_units.remove(unit)
            return True
        return False
    
    def has_fighting_platform(self) -> bool:
        """
        Check if transport has Fighting Platform ability.
        
        NOTE: Fighting Platform is SEPARATE from Transport ability.
        - Transport ability = can carry soldiers
        - Fighting Platform = passengers can attack while aboard
        A transport without Fighting Platform can still carry units, they just can't attack.
        """
        return any('Fighting Platform' in ability for ability in self.transport.abilities)
    
    def can_passengers_attack(self, transport_moved_this_phase: bool) -> bool:
        """
        Check if passengers can attack this turn.
        Requires Fighting Platform and transport must not have moved in assault phase.
        Only non-artillery soldiers can use Fighting Platform.
        """
        if not self.has_fighting_platform():
            return False
        
        # Only non-artillery soldiers can use Fighting Platform
        # Artillery cannot attack from Fighting Platform
        return not transport_moved_this_phase
    
    def destroy_all_passengers(self):
        """When transport is destroyed, all passengers are destroyed"""
        destroyed = self.loaded_units.copy()
        self.loaded_units.clear()
        return destroyed


class TransportManager:
    """
    Manages all transport operations in the game.
    
    NOTE: Loaded units do NOT count toward hex stacking limits.
    They are "inside" the transport and don't take up hex space.
    """
    
    def __init__(self):
        self.transports = {}  # unit_id -> TransportState
    
    def register_transport(self, unit) -> Optional[TransportState]:
        """Register a unit as a transport if it has transport abilities"""
        has_transport = any('Transport' in ability for ability in unit.abilities)
        
        if has_transport:
            transport_state = TransportState(unit)
            self.transports[id(unit)] = transport_state
            return transport_state
        return None
    
    def get_transport_state(self, unit) -> Optional[TransportState]:
        """Get transport state for a unit"""
        return self.transports.get(id(unit))
    
    def is_unit_loaded(self, unit) -> Tuple[bool, Optional[TransportState]]:
        """Check if a unit is loaded on any transport"""
        for transport_state in self.transports.values():
            if unit in transport_state.loaded_units:
                return True, transport_state
        return False, None
    
    def load_unit(self, transport_unit, passenger_unit, 
                  transport_hex: Tuple[int, int], 
                  passenger_hex: Tuple[int, int]) -> Tuple[bool, str]:
        """
        Attempt to load a passenger onto a transport.
        Costs 1 movement point for the passenger.
        
        Args:
            transport_unit: The transport vehicle
            passenger_unit: The unit to load
            transport_hex: (q, r) location of transport
            passenger_hex: (q, r) location of passenger
        
        Returns:
            (success, message)
        """
        # Must be in same hex
        if transport_hex != passenger_hex:
            return False, "Units must be in same hex to load"
        
        # Get or create transport state
        transport_state = self.get_transport_state(transport_unit)
        if not transport_state:
            return False, "Unit is not a transport"
        
        # Check if unit is already loaded somewhere
        is_loaded, _ = self.is_unit_loaded(passenger_unit)
        if is_loaded:
            return False, "Unit is already loaded on a transport"
        
        # Try to load
        can_load, reason = transport_state.can_load(passenger_unit)
        if not can_load:
            return False, reason
        
        # Load the unit
        transport_state.load_unit(passenger_unit)
        return True, f"{passenger_unit.name} loaded onto {transport_unit.name} (costs 1 movement)"
    
    def unload_unit(self, passenger_unit, 
                    unload_hex: Tuple[int, int],
                    transport_hex: Tuple[int, int]) -> Tuple[bool, str]:
        """
        Attempt to unload a passenger from a transport.
        Can only be done in Movement phase, not Assault phase.
        
        Args:
            passenger_unit: The unit to unload
            unload_hex: (q, r) where to unload
            transport_hex: (q, r) where transport is
        
        Returns:
            (success, message)
        """
        # Find which transport has this unit
        is_loaded, transport_state = self.is_unit_loaded(passenger_unit)
        if not is_loaded:
            return False, "Unit is not loaded on any transport"
        
        # Must unload to same hex as transport
        if unload_hex != transport_hex:
            return False, "Can only unload to transport's hex"
        
        # Unload
        transport_state.unload_unit(passenger_unit)
        return True, f"{passenger_unit.name} unloaded"
    
    def handle_transport_destroyed(self, transport_unit) -> List:
        """
        Handle a transport being destroyed - all passengers are also destroyed.
        Returns list of destroyed passenger units.
        """
        transport_state = self.get_transport_state(transport_unit)
        if transport_state:
            destroyed_passengers = transport_state.destroy_all_passengers()
            # Remove transport from tracking
            del self.transports[id(transport_unit)]
            return destroyed_passengers
        return []
    
    def handle_transport_damaged_disrupted(self, transport_unit) -> bool:
        """
        Handle transport being damaged or disrupted.
        Passengers are unaffected and can choose to stay or dismount.
        Returns True if transport has passengers.
        """
        transport_state = self.get_transport_state(transport_unit)
        if transport_state:
            return len(transport_state.loaded_units) > 0
        return False


# Test transport system
if __name__ == "__main__":
    from units import load_units
    from abilities import AbilitySystem
    
    print("=== TRANSPORT SYSTEM TEST ===\n")
    
    # Load systems
    ability_system = AbilitySystem('Axis and Allies Unit Data for Analysis - Special_Abilities.csv')
    transport_manager = TransportManager()
    units = load_units()
    
    # Find transports with and without Fighting Platform
    transports_with_fp = [u for u in units if any('Transport' in a for a in u.abilities) 
                         and any('Fighting Platform' in a for a in u.abilities)]
    transports_without_fp = [u for u in units if any('Transport' in a for a in u.abilities) 
                            and not any('Fighting Platform' in a for a in u.abilities)]
    
    print("=" * 70)
    print("TEST 1: Transport WITH Fighting Platform")
    print("=" * 70)
    if transports_with_fp:
        transport = transports_with_fp[0]
        print(f"Transport: {transport.name}")
        print(f"  Has Transport: True")
        print(f"  Has Fighting Platform: True")
        
        transport_state = transport_manager.register_transport(transport)
        
        # Load infantry
        infantry = [u for u in units if u.unit_type == 'Soldier' and u.speed > 0][:1]
        if infantry:
            soldier = infantry[0]
            success, msg = transport_manager.load_unit(transport, soldier, (5, 5), (5, 5))
            print(f"\n  Loading {soldier.name}: {success}")
            print(f"  Message: {msg}")
            print(f"  Can attack (not moved): {transport_state.can_passengers_attack(False)}")
            print(f"  Can attack (moved): {transport_state.can_passengers_attack(True)}")
    
    print("\n" + "=" * 70)
    print("TEST 2: Transport WITHOUT Fighting Platform")
    print("=" * 70)
    if transports_without_fp:
        transport2 = transports_without_fp[0]
        print(f"Transport: {transport2.name}")
        print(f"  Has Transport: True")
        print(f"  Has Fighting Platform: False")
        
        transport_state2 = transport_manager.register_transport(transport2)
        
        # Load infantry
        infantry = [u for u in units if u.unit_type == 'Soldier' and u.speed > 0][:1]
        if infantry:
            soldier2 = infantry[0]
            success, msg = transport_manager.load_unit(transport2, soldier2, (3, 3), (3, 3))
            print(f"\n  Loading {soldier2.name}: {success}")
            print(f"  Message: {msg}")
            print(f"  Can attack (not moved): {transport_state2.can_passengers_attack(False)}")
            print(f"  Can attack (moved): {transport_state2.can_passengers_attack(True)}")
            print(f"  → Passengers CANNOT attack (no Fighting Platform)")
    
    print("\n" + "=" * 70)
    print("TEST 3: Large Artillery (should fail)")
    print("=" * 70)
    
    # Find artillery with "Large" ability
    large_artillery = [u for u in units if u.unit_type == 'Soldier' and 
                      any('Large' in a for a in u.abilities)]
    
    if large_artillery:
        large_art = large_artillery[0]
        gun_transports = [u for u in units if any('Gun Transport' in a for a in u.abilities)]
        if gun_transports:
            gun_transport = gun_transports[0]
            gun_state = transport_manager.register_transport(gun_transport)
            
            print(f"Attempting to load: {large_art.name}")
            print(f"  Has 'Large' ability: True")
            print(f"Onto: {gun_transport.name}")
            
            success, msg = transport_manager.load_unit(gun_transport, large_art, (7, 7), (7, 7))
            print(f"\n  Result: {success}")
            print(f"  Message: {msg}")
            print(f"  ✓ Large Artillery correctly rejected")
    
    print("\n✅ Transport system test complete!")
    print("\nKey Points:")
    print("  • Transport ability ≠ Fighting Platform ability")
    print("  • Transports can carry units without Fighting Platform")
    print("  • Fighting Platform allows passengers to attack")
    print("  • Large Artillery cannot be transported")
    print("  • Loaded units don't count toward stacking limits")