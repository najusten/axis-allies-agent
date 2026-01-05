import csv
from typing import Dict, List, Set, Optional
import re

class AbilitySystem:
    """
    Central system for parsing and applying special abilities.
    Categorizes abilities by their game phase effects.
    """
    
    # Keywords to identify ability categories
    ABILITY_KEYWORDS = {
        'movement': ['speed', 'move', 'mobility', 'road march', 'movement', 'foot soldier'],
        'attack': ['attack', 'attacks', 'fire', 'shot', 'weapon', 'gun', 'cannon', 'suppressive'],
        'defense': ['defense', 'armor', 'superior armor', 'damage', 'hull down', 'dug in'],
        'cover': ['cover', 'concealment', 'camouflage', 'hidden'],
        'los': ['indirect', 'line of sight', 'spotter', 'high angle', 'ignore cover'],
        'range': ['range', 'limited range', 'extended range', 'long range'],
        'initiative': ['initiative', 'command', 'captain', 'commander', 'leader'],
        'disruption': ['disrupted', 'disruption', 'morale', 'suppress'],
        'special_movement': ['assault', 'strike and fade', 'blitz', 'fast'],
        'terrain': ['hill', 'forest', 'building', 'obstacle', 'fortification'],
        'negative': ['jam', 'unreliable', 'slow', 'vulnerable', 'inaccurate'],
        'hit_modifier': ['hit on', 'successful hit', 'roll to hit'],
        'close_combat': ['close assault', 'close combat', 'melee'],
    }
    
    # Unit types that are obstacles/fortifications, not combat units
    OBSTACLE_TYPES = ['Obstacle', 'Soldier Support']
    
    def __init__(self, abilities_csv_path: str):
        """Load and categorize all abilities"""
        self.abilities = {}  # ability_name -> description
        self.categories = {}  # ability_name -> set of categories
        self.unmatched_abilities = set()  # Track abilities without descriptions
        self._load_abilities(abilities_csv_path)
        self._categorize_abilities()
    
    def _load_abilities(self, csv_path: str):
        """Load abilities from CSV"""
        with open(csv_path, 'r') as file:
            reader = csv.DictReader(file)
            for row in reader:
                name = row['Ability Name'].strip()
                description = row['Description'].strip()
                self.abilities[name] = description
        print(f"Loaded {len(self.abilities)} special abilities")
    
    def _categorize_abilities(self):
        """Categorize each ability based on keywords in description"""
        for ability_name, description in self.abilities.items():
            description_lower = description.lower()
            categories = set()
            
            # Check each category's keywords
            for category, keywords in self.ABILITY_KEYWORDS.items():
                for keyword in keywords:
                    if keyword in description_lower:
                        categories.add(category)
                        break
            
            # Default category if nothing matched
            if not categories:
                categories.add('other')
            
            self.categories[ability_name] = categories
    
    def get_ability_description(self, ability_name: str) -> Optional[str]:
        """
        Get the full description of an ability.
        Handles special formats like "COMMANDER ABILITIES:2" and "Close Assault 5"
        """
        # Check for exact match first
        if ability_name in self.abilities:
            return self.abilities[ability_name]
        
        # Try case-insensitive match (handles MineField vs Minefield)
        for key in self.abilities.keys():
            if key.lower() == ability_name.lower():
                return self.abilities[key]
        
        # Handle "COMMANDER ABILITIES:X" format
        commander_match = re.match(r'COMMANDER ABILITIES?:(\d+)', ability_name, re.IGNORECASE)
        if commander_match:
            bonus = commander_match.group(1)
            return f"This unit provides +{bonus} to initiative rolls."
        
        # Handle numbered abilities like "Close Assault 5", "Aggression 1"
        # The CSV might have entries like "Close Assault (4–16)" so we need to match the base name
        number_match = re.match(r'(.+?)\s+(\d+)$', ability_name)
        if number_match:
            base_ability = number_match.group(1)
            number = number_match.group(2)
            
            # Try exact match
            if base_ability in self.abilities:
                base_desc = self.abilities[base_ability]
                # Replace (X) or patterns like (4–16) with the actual number
                modified_desc = re.sub(r'\([X\d–]+\)', number, base_desc)
                return f"{modified_desc} [Value: {number}]"
            
            # Try case-insensitive match
            for key in self.abilities.keys():
                if key.lower() == base_ability.lower():
                    base_desc = self.abilities[key]
                    # Replace (X) or patterns like (4–16) with the actual number
                    modified_desc = re.sub(r'\([X\d–]+\)', number, base_desc)
                    return f"{modified_desc} [Value: {number}]"
            
            # Try matching against ability names that have ranges in them
            # e.g., "Close Assault 5" should match "Close Assault (4–16)"
            for key in self.abilities.keys():
                # Strip any (X) or (number–number) patterns from the key
                key_base = re.sub(r'\s*\([X\d–]+\)', '', key)
                if key_base.lower() == base_ability.lower():
                    base_desc = self.abilities[key]
                    # Replace (X) or patterns like (4–16) with the actual number
                    modified_desc = re.sub(r'\([X\d–]+\)', number, base_desc)
                    return f"{modified_desc} [Value: {number}]"
        
        # Track unmatched for reporting
        self.unmatched_abilities.add(ability_name)
        return None
    
    def get_ability_categories(self, ability_name: str) -> Set[str]:
        """Get categories for an ability"""
        # Check exact match
        if ability_name in self.categories:
            return self.categories[ability_name]
        
        # For numbered abilities, try base name
        number_match = re.match(r'(.+?)\s+(\d+)$', ability_name)
        if number_match:
            base_ability = number_match.group(1)
            if base_ability in self.categories:
                return self.categories[base_ability]
            
            # Try matching against keys with ranges
            for key in self.categories.keys():
                key_base = re.sub(r'\s*\([X\d–]+\)', '', key)
                if key_base.lower() == base_ability.lower():
                    return self.categories[key]
        
        # COMMANDER ABILITIES
        if 'COMMANDER ABILITIES' in ability_name:
            return {'initiative'}
        
        return set()
    
    def is_obstacle_unit(self, unit) -> bool:
        """Check if a unit is an obstacle/fortification (not a combat unit)"""
        return unit.unit_type in self.OBSTACLE_TYPES
    
    def unit_has_ability(self, unit, ability_name: str) -> bool:
        """Check if a unit has a specific ability"""
        return ability_name in unit.abilities
    
    def unit_has_any_ability_in_category(self, unit, category: str) -> List[str]:
        """Get all abilities a unit has in a specific category"""
        matching = []
        for ability in unit.abilities:
            if category in self.get_ability_categories(ability):
                matching.append(ability)
        return matching
    
    def parse_close_assault(self, ability_name: str) -> Optional[int]:
        """Parse Close Assault X ability, return number of dice"""
        match = re.match(r'Close Assault\s+(\d+)', ability_name, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None
    
    def get_movement_modifiers(self, unit) -> Dict:
        """
        Analyze unit's movement-related abilities.
        Returns dict with movement modifications.
        """
        modifiers = {
            'speed_bonus': 0,
            'speed_penalty': 0,
            'can_move_and_shoot': True,
            'can_assault_move': False,
            'road_bonus': False,
            'terrain_penalties': {},
            'notes': []
        }
        
        # Skip obstacles
        if self.is_obstacle_unit(unit):
            modifiers['can_move_and_shoot'] = False
            modifiers['notes'].append("Obstacle/Fortification - cannot move")
            return modifiers
        
        movement_abilities = self.unit_has_any_ability_in_category(unit, 'movement')
        special_movement = self.unit_has_any_ability_in_category(unit, 'special_movement')
        
        for ability in movement_abilities + special_movement:
            description = self.get_ability_description(ability)
            if not description:
                continue
            description_lower = description.lower()
            
            # Check for specific movement modifiers
            if 'assault' in description_lower or 'strike and fade' in ability.lower():
                modifiers['can_assault_move'] = True
                modifiers['notes'].append(f"{ability}: Can move in assault phase")
            
            if 'foot soldier' in description_lower:
                modifiers['notes'].append(f"{ability}: Infantry movement rules")
            
            if 'road march' in description_lower:
                modifiers['road_bonus'] = True
                modifiers['notes'].append(f"{ability}: Bonus on roads")
            
            # Parse Aggression X (can move X before attacking)
            aggression_match = re.match(r'Aggression\s+(\d+)', ability, re.IGNORECASE)
            if aggression_match:
                speed = aggression_match.group(1)
                modifiers['notes'].append(f"{ability}: Can move {speed} before attacking")
        
        return modifiers
    
    def get_attack_modifiers(self, unit, target, distance: int, 
                            target_terrain: str = 'open') -> Dict:
        """
        Analyze unit's attack-related abilities.
        Returns dict with attack modifications.
        """
        modifiers = {
            'bonus_dice': 0,
            'hit_threshold': 4,  # Default: hit on 4+
            'ignore_cover': False,
            'can_attack': True,
            'range_limit': None,
            'indirect_fire': False,
            'requires_los': True,
            'close_assault_dice': None,
            'notes': []
        }
        
        # Obstacles can't attack
        if self.is_obstacle_unit(unit):
            modifiers['can_attack'] = False
            modifiers['notes'].append("Obstacles cannot attack")
            return modifiers
        
        attack_abilities = self.unit_has_any_ability_in_category(unit, 'attack')
        range_abilities = self.unit_has_any_ability_in_category(unit, 'range')
        los_abilities = self.unit_has_any_ability_in_category(unit, 'los')
        hit_modifiers = self.unit_has_any_ability_in_category(unit, 'hit_modifier')
        close_combat = self.unit_has_any_ability_in_category(unit, 'close_combat')
        
        # Check for Close Assault (same hex only, vehicles only)
        for ability in unit.abilities:
            close_assault_dice = self.parse_close_assault(ability)
            if close_assault_dice and distance == 0:
                # Only applies to vehicles
                target_is_vehicle = target.unit_type in ['Vehicle', 'Aircraft']
                if target_is_vehicle:
                    modifiers['close_assault_dice'] = close_assault_dice
                    modifiers['ignore_cover'] = True  # Close Assault ignores cover
                    modifiers['notes'].append(f"{ability}: {close_assault_dice} dice vs vehicles, ignores cover")
        
        # Check range limitations
        for ability in range_abilities:
            description = self.get_ability_description(ability)
            if not description:
                continue
            
            # Limited Range X
            range_match = re.search(r'limited range (\d+)', description.lower())
            if range_match:
                range_limit = int(range_match.group(1))
                modifiers['range_limit'] = range_limit
                if distance > range_limit:
                    modifiers['can_attack'] = False
                    modifiers['notes'].append(f"Limited Range {range_limit}: Max range exceeded (distance: {distance})")
        
        # Check LOS modifiers
        for ability in los_abilities:
            description = self.get_ability_description(ability)
            if not description:
                continue
            description_lower = description.lower()
            
            if 'indirect' in description_lower or 'high angle' in description_lower:
                modifiers['indirect_fire'] = True
                modifiers['requires_los'] = False
                modifiers['notes'].append(f"{ability}: Can fire without LOS")
            
            if 'ignore cover' in description_lower:
                modifiers['ignore_cover'] = True
                modifiers['notes'].append(f"{ability}: Ignores cover")
        
        # Check hit threshold modifiers
        for ability in hit_modifiers:
            description = self.get_ability_description(ability)
            if not description:
                continue
            description_lower = description.lower()
            
            # "Successful hit on a 3, 4, 5, or 6"
            if 'hit on a 3' in description_lower or 'successful hit on a 3' in description_lower:
                modifiers['hit_threshold'] = 3
                modifiers['notes'].append(f"{ability}: Hits on 3+")
            elif 'hit on a 5' in description_lower:
                modifiers['hit_threshold'] = 5
                modifiers['notes'].append(f"{ability}: Hits on 5+ (worse)")
        
        # Check for attack penalties
        negative_abilities = self.unit_has_any_ability_in_category(unit, 'negative')
        for ability in negative_abilities:
            description = self.get_ability_description(ability)
            if not description:
                continue
            description_lower = description.lower()
            
            if 'inaccurate' in description_lower:
                # Inaccurate typically means -1 on attack rolls
                inaccurate_match = re.search(r'inaccurate\s+(\d+)', description_lower)
                if inaccurate_match:
                    penalty = inaccurate_match.group(1)
                    modifiers['notes'].append(f"{ability}: -{penalty} penalty on attack rolls")
        
        return modifiers
    
    def get_defense_modifiers(self, unit, terrain: str, 
                             is_rear_attack: bool = False) -> Dict:
        """
        Analyze unit's defense-related abilities.
        Returns dict with defense modifications.
        """
        modifiers = {
            'defense_bonus': 0,
            'armor_modifier': 0,  # For "Superior Armor" (need to exceed by 2)
            'cover_bonus': 0,
            'immune_to_cover': False,
            'special_saves': [],
            'notes': []
        }
        
        defense_abilities = self.unit_has_any_ability_in_category(unit, 'defense')
        cover_abilities = self.unit_has_any_ability_in_category(unit, 'cover')
        
        for ability in defense_abilities:
            description = self.get_ability_description(ability)
            if not description:
                continue
            description_lower = description.lower()
            
            if 'superior armor' in description_lower:
                # Need to exceed defense by 2 instead of 1
                modifiers['armor_modifier'] = 1
                modifiers['notes'].append(f"{ability}: Need to exceed defense by 2 for damage")
            
            if 'hull down' in description_lower:
                modifiers['defense_bonus'] = 1
                modifiers['notes'].append(f"{ability}: +1 defense")
            
            if 'dug in' in description_lower:
                # Usually gives cover benefits
                modifiers['cover_bonus'] = 1
                modifiers['notes'].append(f"{ability}: Cover benefits")
        
        for ability in cover_abilities:
            description = self.get_ability_description(ability)
            if not description:
                continue
            
            if 'Camouflage' in ability or 'concealment' in description.lower():
                modifiers['notes'].append(f"{ability}: Enhanced cover")
        
        return modifiers
    
    def get_initiative_modifiers(self, unit) -> int:
        """Get initiative bonuses from abilities (e.g., COMMANDER ABILITIES:2 = +2)"""
        initiative_bonus = 0
        
        for ability in unit.abilities:
            # Check for COMMANDER ABILITIES:X format
            commander_match = re.match(r'COMMANDER ABILITIES?:(\d+)', ability, re.IGNORECASE)
            if commander_match:
                bonus = int(commander_match.group(1))
                initiative_bonus += bonus
                continue
            
            # Check description for initiative bonuses
            description = self.get_ability_description(ability)
            if description:
                description_lower = description.lower()
                bonus_match = re.search(r'\+(\d+)\s+initiative', description_lower)
                if bonus_match:
                    bonus = int(bonus_match.group(1))
                    initiative_bonus += bonus
        
        return initiative_bonus
    
    def check_los_blocked(self, unit, target_hex_terrain: str, 
                         blocking_hexes: List) -> bool:
        """
        Check if LOS is blocked considering unit abilities.
        Returns True if blocked, False if unit can see through.
        """
        los_abilities = self.unit_has_any_ability_in_category(unit, 'los')
        
        # Check if unit has abilities to see through obstacles
        for ability in los_abilities:
            description = self.get_ability_description(ability)
            if not description:
                continue
            description_lower = description.lower()
            
            if 'indirect' in description_lower:
                return False  # Indirect fire ignores LOS
            
            if 'spotter' in description_lower:
                # Some spotter abilities let you see through one obstacle
                return len(blocking_hexes) > 1
        
        # Default: blocked if any blocking hexes
        return len(blocking_hexes) > 0
    
    def print_unit_abilities_summary(self, unit):
        """Print a summary of all abilities for a unit"""
        print(f"\n{'='*70}")
        print(f"Abilities for: {unit.name} ({unit.unit_type})")
        print(f"{'='*70}")
        
        if not unit.abilities:
            print("No special abilities")
            return
        
        for ability in unit.abilities:
            categories = self.get_ability_categories(ability)
            description = self.get_ability_description(ability)
            
            print(f"\n📋 {ability}")
            if categories:
                print(f"   Categories: {', '.join(sorted(categories))}")
            if description:
                print(f"   Effect: {description}")
            else:
                print(f"   Effect: ⚠️  Ability not found in database")
        
        print(f"{'='*70}\n")
    
    def print_unmatched_abilities(self):
        """Print all abilities that couldn't be matched to descriptions"""
        if self.unmatched_abilities:
            print(f"\n⚠️  Unmatched abilities ({len(self.unmatched_abilities)}):")
            for ability in sorted(self.unmatched_abilities):
                print(f"  - {ability}")


# Test the ability system
if __name__ == "__main__":
    from units import load_units
    
    print("=== ABILITY SYSTEM TEST ===\n")
    
    # Load ability system
    ability_system = AbilitySystem('Axis and Allies Unit Data for Analysis - Special_Abilities.csv')
    
    # Show categorization stats
    category_counts = {}
    for categories in ability_system.categories.values():
        for cat in categories:
            category_counts[cat] = category_counts.get(cat, 0) + 1
    
    print("\nAbility categories found:")
    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count} abilities")
    
    # Load units and test on a few
    units = load_units()
    
    # Test specific units
    test_units = []
    
    # Find officer with COMMANDER ABILITIES
    officer = [u for u in units if 'COMMANDER ABILITIES' in ' '.join(u.abilities)]
    if officer:
        test_units.append(officer[0])
    
    # Find unit with Close Assault
    close_assault_unit = [u for u in units if any('Close Assault' in a for a in u.abilities)]
    if close_assault_unit:
        test_units.append(close_assault_unit[0])
    
    # Find an obstacle
    obstacle = [u for u in units if u.unit_type == 'Obstacle']
    if obstacle:
        test_units.append(obstacle[0])
    
    # Regular combat unit
    regular = [u for u in units if u.name == 'Owen SMG']
    if regular:
        test_units.append(regular[0])
    
    for unit in test_units[:4]:
        ability_system.print_unit_abilities_summary(unit)
        
        # Test modifiers
        if not ability_system.is_obstacle_unit(unit):
            print(f"Movement modifiers:")
            movement_mods = ability_system.get_movement_modifiers(unit)
            for key, value in movement_mods.items():
                if value and key != 'notes':
                    print(f"  {key}: {value}")
            if movement_mods['notes']:
                for note in movement_mods['notes']:
                    print(f"  • {note}")
            
            print(f"\nAttack modifiers (vs vehicle at distance 0):")
            # Create a mock vehicle for testing
            from units import Unit
            mock_vehicle = Unit("Test Tank", "USA", "Vehicle", "1944", "20", "4/3", "4", "12", "10", "8", "6", "4", "0", "")
            attack_mods = ability_system.get_attack_modifiers(unit, mock_vehicle, 0)
            for key, value in attack_mods.items():
                if value and key != 'notes' and key != 'can_attack':
                    print(f"  {key}: {value}")
            if attack_mods['notes']:
                for note in attack_mods['notes']:
                    print(f"  • {note}")
            
            # Initiative bonus
            init_bonus = ability_system.get_initiative_modifiers(unit)
            if init_bonus > 0:
                print(f"\nInitiative bonus: +{init_bonus}")
        
        print("\n" + "="*70)
    
    # Print unmatched abilities
    ability_system.print_unmatched_abilities()