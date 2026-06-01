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

        # Check all abilities on the unit for movement effects
        for ability in (unit.abilities or []):
            # High Gear X - adds +X to movement speed ONLY when moving entirely on roads
            high_gear_match = re.match(r'High Gear\s+(\d+)', ability, re.IGNORECASE)
            if high_gear_match:
                bonus = int(high_gear_match.group(1))
                modifiers['high_gear_bonus'] = bonus
                modifiers['notes'].append(f"{ability}: +{bonus} movement speed (road only)")

            # Robust - +1 on movement ROLLS (terrain crossing), speed 1 when disrupted
            if 'robust' in ability.lower():
                modifiers['movement_roll_bonus'] = modifiers.get('movement_roll_bonus', 0) + 1
                modifiers['robust'] = True  # Flag for disrupted speed handling
                modifiers['notes'].append(f"{ability}: +1 on movement rolls, speed 1 when disrupted")

            # Strike and Fade - can move in assault phase
            if 'strike and fade' in ability.lower():
                modifiers['can_assault_move'] = True
                modifiers['notes'].append(f"{ability}: Can move in assault phase")

            # Aggression X - can move X before attacking
            aggression_match = re.match(r'Aggression\s+(\d+)', ability, re.IGNORECASE)
            if aggression_match:
                speed = aggression_match.group(1)
                modifiers['notes'].append(f"{ability}: Can move {speed} before attacking")

            # Excellent Suspension - can enter hill hexes as clear hexes
            if ability.lower() == 'excellent suspension':
                modifiers['ignore_hill_terrain'] = True
                modifiers['notes'].append(f"{ability}: Enter hill hexes as clear terrain")

            # Thin Wheels - can't cross streams or enter marshes except along a road
            if ability.lower() == 'thin wheels':
                modifiers['thin_wheels'] = True
                modifiers['notes'].append(f"{ability}: Can't enter marsh/streams without road")

            # Brushcutters - can enter forest hexes without making a movement roll
            if ability.lower() == 'brushcutters':
                modifiers['ignore_forest_terrain'] = True
                modifiers['notes'].append(f"{ability}: Enter forest hexes without movement roll")

            # Mountaineering - +1 on movement rolls
            if ability.lower() == 'mountaineering':
                modifiers['movement_roll_bonus'] = modifiers.get('movement_roll_bonus', 0) + 1
                modifiers['notes'].append(f"{ability}: +1 on movement rolls")

            # Poor Suspension - can't enter hill hexes except along a road
            if ability.lower() == 'poor suspension':
                modifiers['poor_suspension'] = True
                modifiers['notes'].append(f"{ability}: Can't enter hill hexes without road")

            # Weak Suspension - must make movement roll to enter hill hexes
            if ability.lower() == 'weak suspension':
                modifiers['weak_suspension'] = True
                modifiers['notes'].append(f"{ability}: Must roll to enter hill hexes")

            # Trench Crossing - can cross streams without making a movement roll
            if ability.lower() == 'trench crossing':
                modifiers['ignore_stream_terrain'] = True
                modifiers['notes'].append(f"{ability}: Cross streams without movement roll")

            # Amphibious - cross streams without movement roll AND enter water as double-cost
            if ability.lower() == 'amphibious':
                modifiers['ignore_stream_terrain'] = True
                modifiers['amphibious'] = True
                modifiers['notes'].append(f"{ability}: Cross streams without roll, enter water as double-cost")

            # Water Craft - can only enter water hexes, enters them as clear
            if ability.lower() == 'water craft':
                modifiers['water_craft'] = True
                modifiers['notes'].append(f"{ability}: Can only enter water hexes (as clear terrain)")

            # Mechanized Tactics - can dismount at end of movement phase
            if ability.lower() == 'mechanized tactics':
                modifiers['mechanized_tactics'] = True
                modifiers['notes'].append(f"{ability}: Can dismount at end of movement phase")

            # Vanguard - can move at speed 4 before first turn (requires deployment phase)
            if ability.lower() == 'vanguard':
                modifiers['vanguard'] = True
                modifiers['vanguard_speed'] = 4
                modifiers['notes'].append(f"{ability}: Move at speed 4 before first turn")

            # AVRE - can enter hexes with Obstacles and destroys them
            if ability.lower() == 'avre':
                modifiers['avre'] = True
                modifiers['notes'].append(f"{ability}: Can enter obstacle hexes and destroy them")

            # Gliderborne - deploy anywhere not in opponent's starting area
            if ability.lower() == 'gliderborne':
                modifiers['gliderborne'] = True
                modifiers['notes'].append(f"{ability}: Deploy anywhere not in opponent's starting area")

            # Partisan - deploy on edge of battle map
            if ability.lower() == 'partisan':
                modifiers['partisan'] = True
                modifiers['notes'].append(f"{ability}: Deploy on edge of battle map")

            # Relocate X - speed X during assault phase
            relocate_match = re.match(r'relocate\s+(\d+)', ability, re.IGNORECASE)
            if relocate_match:
                relocate_speed = int(relocate_match.group(1))
                modifiers['relocate_speed'] = relocate_speed
                modifiers['notes'].append(f"{ability}: Speed {relocate_speed} during assault phase")

        # Check category-based abilities for additional effects
        movement_abilities = self.unit_has_any_ability_in_category(unit, 'movement')
        special_movement = self.unit_has_any_ability_in_category(unit, 'special_movement')

        for ability in movement_abilities + special_movement:
            description = self.get_ability_description(ability)
            if not description:
                continue
            description_lower = description.lower()

            # Check for specific movement modifiers
            if 'assault' in description_lower:
                modifiers['can_assault_move'] = True
                modifiers['notes'].append(f"{ability}: Can move in assault phase")

            if 'foot soldier' in description_lower:
                modifiers['notes'].append(f"{ability}: Infantry movement rules")

            if 'road march' in description_lower:
                modifiers['road_bonus'] = True
                modifiers['notes'].append(f"{ability}: Bonus on roads")

        return modifiers
    
    def get_attack_modifiers(self, unit, target, distance: int,
                            target_terrain: str = 'open',
                            is_rear_attack: bool = False,
                            target_state=None,
                            attacker_terrain: str = 'open') -> Dict:
        """
        Analyze unit's attack-related abilities.
        Returns dict with attack modifications.

        Args:
            unit: The attacking unit
            target: The target unit
            distance: Distance in hexes
            target_terrain: Terrain of target hex
            is_rear_attack: True if attacking from the rear
            target_state: Target's state (for Ruthless check - is_disrupted, is_damaged)
            attacker_terrain: Terrain of attacker hex (for Surprise Fire)
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

        # Aircraft targeting rules:
        # "Units attacking Aircraft use their anti-Soldier attacks and get –1 on each attack die."
        if target.unit_type == 'Aircraft':
            modifiers['use_anti_soldier_values'] = True
            modifiers['notes'].append("Aircraft: Use anti-Soldier attack values")

            # Check if attacker has Antiair or Ace (ignores the -1 penalty)
            attacker_abilities = getattr(unit, 'abilities', []) or []
            has_antiair_or_ace = any(
                'antiair' in a.lower() or a.lower() == 'ace'
                for a in attacker_abilities
            )
            if not has_antiair_or_ace:
                # -1 on each attack die = +1 to hit threshold (need 5+ instead of 4+)
                modifiers['hit_modifier'] = modifiers.get('hit_modifier', 0) + 1
                modifiers['notes'].append("Aircraft: -1 on each attack die (need 5+ to hit)")
            else:
                modifiers['notes'].append("Antiair/Ace: Ignores -1 penalty vs Aircraft")

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

        # Check for Hand to Hand (same hex only, soldiers only)
        for ability in unit.abilities:
            hth_match = re.match(r'Hand to Hand\s+(\d+)', ability, re.IGNORECASE)
            if hth_match and distance == 0:
                hth_dice = int(hth_match.group(1))
                # Only applies to soldiers
                if target.unit_type == 'Soldier':
                    modifiers['hand_to_hand_dice'] = hth_dice
                    modifiers['ignore_cover'] = True  # Hand to Hand ignores cover
                    modifiers['notes'].append(f"{ability}: {hth_dice} dice vs soldiers, ignores cover")

        # Check for Shrapnel (double successes vs Soldiers)
        for ability in unit.abilities:
            if 'shrapnel' in ability.lower():
                if target.unit_type == 'Soldier':
                    modifiers['shrapnel'] = True
                    modifiers['notes'].append(f"{ability}: Each success counts as 2 vs soldiers")

        # Check for Blast (attacks all units in target hex)
        for ability in unit.abilities:
            if ability.lower() == 'blast':
                modifiers['blast'] = True
                modifiers['notes'].append(f"{ability}: Attacks all units in target hex")

        # Check for Open Back on target
        # Open Back: Enemy units can use their anti-Soldier attack values when
        # attacking the rear defense of this unit.
        target_abilities = getattr(target, 'abilities', []) or []
        for ability in target_abilities:
            if 'open back' in ability.lower():
                if is_rear_attack:
                    modifiers['use_anti_soldier_values'] = True
                    modifiers['notes'].append(f"Open Back: Use anti-Soldier attack values vs rear")

        # Check for Bombardment on attacker
        # Bombardment: This unit's attacks ignore cover. Can't attack Aircraft.
        attacker_abilities = getattr(unit, 'abilities', []) or []
        for ability in attacker_abilities:
            if 'bombardment' in ability.lower():
                modifiers['ignore_cover'] = True
                modifiers['notes'].append(f"Bombardment: Attacks ignore cover")
                if target.unit_type == 'Aircraft':
                    modifiers['can_attack'] = False
                    modifiers['notes'].append(f"Bombardment: Can't attack Aircraft")
                break

        # Top-Mounted Rockets: Can't attack Aircraft, attacks ignore cover
        for ability in attacker_abilities:
            if 'top-mounted rockets' in ability.lower():
                modifiers['ignore_cover'] = True
                modifiers['notes'].append(f"Top-Mounted Rockets: Attacks ignore cover")
                if target.unit_type == 'Aircraft':
                    modifiers['can_attack'] = False
                    modifiers['notes'].append(f"Top-Mounted Rockets: Can't attack Aircraft")
                break

        # Jet: Can't attack Soldiers in forest, town, or marsh hexes
        for ability in attacker_abilities:
            if ability.lower() == 'jet':
                if target.unit_type == 'Soldier' and target_terrain in ['forest', 'town', 'marsh']:
                    modifiers['can_attack'] = False
                    modifiers['notes'].append(f"Jet: Can't attack Soldiers in {target_terrain}")
                # 6s count as double successes vs Aircraft (handled in dice resolution)
                if target.unit_type == 'Aircraft':
                    modifiers['jet_vs_aircraft'] = True
                    modifiers['notes'].append(f"Jet: 6s count as double successes vs Aircraft")
                break

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
        
        # Check for Inaccurate X directly on unit abilities
        for ability in (unit.abilities or []):
            inaccurate_match = re.match(r'Inaccurate\s+(\d+)', ability, re.IGNORECASE)
            if inaccurate_match:
                penalty = int(inaccurate_match.group(1))
                # Inaccurate X means -X on attack rolls (positive modifier = harder to hit)
                modifiers['hit_modifier'] = modifiers.get('hit_modifier', 0) + penalty
                new_threshold = modifiers['hit_threshold'] + penalty
                modifiers['notes'].append(f"{ability}: Hits on {new_threshold}+ (penalty)")

        # Check for Crack Shot - "+1 on each attack die" (easier to hit)
        for ability in (unit.abilities or []):
            if ability.lower() == 'crack shot':
                # +1 on each die = -1 to hit threshold (need 3+ instead of 4+)
                modifiers['hit_modifier'] = modifiers.get('hit_modifier', 0) - 1
                new_threshold = modifiers['hit_threshold'] + modifiers['hit_modifier']
                modifiers['notes'].append(f"Crack Shot: +1 on each attack die (hits on {new_threshold}+)")
                break

        # Tank Hunter: "+1 on each attack die against vehicles"
        for ability in (unit.abilities or []):
            if ability.lower() == 'tank hunter':
                if 'Vehicle' in target.unit_type:
                    modifiers['hit_modifier'] = modifiers.get('hit_modifier', 0) - 1
                    new_threshold = modifiers['hit_threshold'] + modifiers['hit_modifier']
                    modifiers['notes'].append(f"Tank Hunter: +1 on each attack die vs Vehicles (hits on {new_threshold}+)")
                break

        # Ruthless: "+1 on each attack die against disrupted and damaged units"
        for ability in (unit.abilities or []):
            if ability.lower() == 'ruthless':
                if target_state is not None:
                    # Handle both dict and object target_state
                    if isinstance(target_state, dict):
                        target_disrupted = target_state.get('is_disrupted', False)
                        target_damaged = target_state.get('is_damaged', False)
                    else:
                        target_disrupted = getattr(target_state, 'is_disrupted', False)
                        target_damaged = getattr(target_state, 'is_damaged', False)
                    if target_disrupted or target_damaged:
                        modifiers['hit_modifier'] = modifiers.get('hit_modifier', 0) - 1
                        new_threshold = modifiers['hit_threshold'] + modifiers['hit_modifier']
                        modifiers['notes'].append(f"Ruthless: +1 on each attack die vs disrupted/damaged (hits on {new_threshold}+)")
                break

        # Steady Firing: "rolls two extra attack dice when attacking a Soldier"
        for ability in (unit.abilities or []):
            if ability.lower() == 'steady firing':
                if target.unit_type == 'Soldier':
                    modifiers['bonus_dice'] = modifiers.get('bonus_dice', 0) + 2
                    modifiers['notes'].append(f"Steady Firing: +2 attack dice vs Soldiers")
                break

        # Flanking Attack: "rolls one extra attack die when attacking a Vehicle's rear"
        for ability in (unit.abilities or []):
            if ability.lower() == 'flanking attack':
                if is_rear_attack and 'Vehicle' in target.unit_type:
                    modifiers['bonus_dice'] = modifiers.get('bonus_dice', 0) + 1
                    modifiers['notes'].append(f"Flanking Attack: +1 attack die vs Vehicle rear")
                break

        # Surprise Fire: "rolls one extra attack die when attacking from a forest hex"
        for ability in (unit.abilities or []):
            if ability.lower() == 'surprise fire':
                if attacker_terrain == 'forest':
                    modifiers['bonus_dice'] = modifiers.get('bonus_dice', 0) + 1
                    modifiers['notes'].append(f"Surprise Fire: +1 attack die from forest")
                break

        # Dismounted Attack: "While there are no enemy Soldiers adjacent to this unit,
        # this unit's short range against Vehicles is 0-2 hexes."
        for ability in (unit.abilities or []):
            if ability.lower() == 'dismounted attack':
                modifiers['dismounted_attack'] = True
                modifiers['notes'].append("Dismounted Attack: Short range vs Vehicles extended to 0-2")
                break

        # Superior Camouflage: "While this unit has cover, enemy units can't attack at medium or long range"
        # This checks the target's abilities - if target has Superior Camouflage, attacker can't attack at medium/long
        # Exception: Experienced Recon can attack at medium range
        target_abilities = getattr(target, 'abilities', []) or []
        attacker_abilities = getattr(unit, 'abilities', []) or []
        has_experienced_recon = any(a.lower() == 'experienced recon' for a in attacker_abilities)
        for ability in target_abilities:
            if ability.lower() == 'superior camouflage':
                # Check if target has cover (forest, building, etc.)
                cover_terrains = ('forest', 'building', 'hill', 'town', 'ruins')
                has_cover = target_terrain in cover_terrains
                # Experienced Recon: can attack at medium range (2-4)
                if has_experienced_recon:
                    is_blocked_range = distance >= 5  # Only block long range
                else:
                    is_blocked_range = distance >= 2  # Block medium and long range
                if has_cover and is_blocked_range:
                    modifiers['can_attack'] = False
                    if has_experienced_recon:
                        modifiers['notes'].append(f"Superior Camouflage: Can't attack at long range while in cover")
                    else:
                        modifiers['notes'].append(f"Superior Camouflage: Can't attack at medium/long range while in cover")
                elif has_cover and has_experienced_recon and distance >= 2:
                    modifiers['notes'].append(f"Experienced Recon: Can attack Superior Camouflage at medium range")
                break

        # Agility: "short range against aircraft is 0-2 hexes and medium range is 3-5 hexes"
        # Normal: short 0-1, medium 2-4, long 5+
        # With Agility vs Aircraft: short 0-2, medium 3-5, long 6+
        for ability in (unit.abilities or []):
            if ability.lower() == 'agility':
                if target.unit_type == 'Aircraft':
                    modifiers['agility_range'] = True
                    modifiers['notes'].append(f"Agility: Extended range vs Aircraft (short 0-2, medium 3-5)")
                break

        # Seasoned Crew: "Units attacked by this unit get –1 on cover rolls"
        for ability in (unit.abilities or []):
            if ability.lower() == 'seasoned crew':
                modifiers['target_cover_penalty'] = modifiers.get('target_cover_penalty', 0) + 1
                modifiers['notes'].append(f"Seasoned Crew: Target gets -1 on cover rolls")
                break

        # Covering Fire: "Soldiers attacked by this unit can't make defensive fire attacks this turn"
        for ability in (unit.abilities or []):
            if ability.lower() == 'covering fire':
                if target.unit_type == 'Soldier':
                    modifiers['prevents_defensive_fire'] = True
                    modifiers['notes'].append(f"Covering Fire: Target Soldier can't make defensive fire")
                break

        # Limited Covering Fire: Same as Covering Fire but only at short range (2 hexes or less)
        for ability in (unit.abilities or []):
            if ability.lower() == 'limited covering fire':
                if target.unit_type == 'Soldier' and distance <= 2:
                    modifiers['prevents_defensive_fire'] = True
                    modifiers['notes'].append(f"Limited Covering Fire: Target Soldier can't make defensive fire")
                break

        # Flamethrower: "short-range attack ignores cover. If 3+ 6s, target destroyed immediately"
        for ability in (unit.abilities or []):
            if ability.lower() == 'flamethrower' or ability.lower() == 'hull-mounted flamethrower':
                # Only applies at short range (0-1 hexes)
                if distance <= 1:
                    # Ignores cover at short range
                    modifiers['ignore_cover'] = True
                    modifiers['flamethrower'] = True
                    modifiers['notes'].append(f"{ability}: Short-range attack ignores cover, 3+ 6s = instant destroy")
                break

        # Extended Range X: "This unit's long range against Vehicles is 5–X hexes."
        for ability in (unit.abilities or []):
            ext_range_match = re.match(r'extended range\s+(\d+)', ability, re.IGNORECASE)
            if ext_range_match:
                max_range = int(ext_range_match.group(1))
                modifiers['extended_range_vehicles'] = max_range
                modifiers['notes'].append(f"{ability}: Long range vs Vehicles is 5-{max_range} hexes")
                break

        # Enhanced Range X: "This unit's long range is 5–X hexes." (all targets)
        for ability in (unit.abilities or []):
            enh_range_match = re.match(r'enhanced range\s+(\d+)', ability, re.IGNORECASE)
            if enh_range_match:
                max_range = int(enh_range_match.group(1))
                modifiers['enhanced_range'] = max_range
                modifiers['notes'].append(f"{ability}: Long range is 5-{max_range} hexes")
                break

        # Fixed Howitzer: "This unit can attack only units in front of it."
        for ability in (unit.abilities or []):
            if ability.lower() == 'fixed howitzer':
                modifiers['fixed_howitzer'] = True
                modifiers['notes'].append(f"Fixed Howitzer: Can only attack units in front")
                break

        # Strafe: "This unit can attack another enemy soldier unit that is adjacent to the original target hex"
        for ability in (unit.abilities or []):
            if ability.lower() == 'strafe':
                modifiers['strafe'] = True
                modifiers['notes'].append(f"Strafe: Can attack adjacent Soldier after main attack")
                break

        # Bombs: "Once per game, roll 12 dice vs Soldier or 8 dice vs Vehicle in same hex"
        for ability in (unit.abilities or []):
            if ability.lower() == 'bombs':
                if distance == 0:  # Same hex
                    modifiers['bombs_available'] = True
                    if target.unit_type == 'Soldier':
                        modifiers['bombs_dice'] = 12
                    else:
                        modifiers['bombs_dice'] = 8
                    modifiers['notes'].append(f"Bombs: {modifiers.get('bombs_dice', 0)} dice available (once per game)")
                break

        # Commander Hunter: "rolls two extra attack dice when attacking a Commander"
        for ability in (unit.abilities or []):
            if ability.lower() == 'commander hunter':
                # Check if target is a Commander (has Commander Abilities)
                target_abilities = getattr(target, 'abilities', []) or []
                is_commander = any('commander abilities' in a.lower() for a in target_abilities)
                if is_commander:
                    modifiers['bonus_dice'] = modifiers.get('bonus_dice', 0) + 2
                    modifiers['notes'].append(f"Commander Hunter: +2 attack dice vs Commander")
                break

        # Gun Crew Hunter: "rolls two extra attack dice when attacking Artillery"
        for ability in (unit.abilities or []):
            if ability.lower() == 'gun crew hunter':
                # Check if target is Artillery
                target_subtype = getattr(target, 'subtype', '') or ''
                if 'artillery' in target_subtype.lower() or 'artillery' in target.unit_type.lower():
                    modifiers['bonus_dice'] = modifiers.get('bonus_dice', 0) + 2
                    modifiers['notes'].append(f"Gun Crew Hunter: +2 attack dice vs Artillery")
                break

        # SS Hunter: "rolls two extra attack dice when attacking an SS unit"
        for ability in (unit.abilities or []):
            if ability.lower() == 'ss hunter':
                # Check if target is an SS unit (has SS in name or abilities)
                target_name = getattr(target, 'name', '').lower()
                target_abilities_list = getattr(target, 'abilities', []) or []
                is_ss_unit = ('ss' in target_name or
                              any('ss ' in a.lower() or a.lower().startswith('ss')
                                  for a in target_abilities_list))
                if is_ss_unit:
                    modifiers['bonus_dice'] = modifiers.get('bonus_dice', 0) + 2
                    modifiers['notes'].append(f"SS Hunter: +2 attack dice vs SS unit")
                break

        # Well Led: "rolls one extra attack die when attacking during your assault phase"
        for ability in (unit.abilities or []):
            if ability.lower() == 'well led':
                modifiers['bonus_dice'] = modifiers.get('bonus_dice', 0) + 1
                modifiers['notes'].append(f"Well Led: +1 attack die")
                break

        # Camouflaged: "Until this unit attacks or moves, enemy units can't attack at medium or long range"
        # This checks if target has Camouflaged and hasn't moved/attacked
        # Exception: Experienced Recon can attack at medium range
        for ability in target_abilities:
            if ability.lower() == 'camouflaged':
                # Experienced Recon: can attack at medium range (2-4)
                if has_experienced_recon:
                    is_blocked_range = distance >= 5  # Only block long range
                else:
                    is_blocked_range = distance >= 2  # Block medium and long range
                if is_blocked_range:
                    # Note: The tracking of whether unit has moved/attacked would need
                    # to be passed via target_state. For now, check if it has the flag.
                    if target_state:
                        has_moved = getattr(target_state, 'has_moved', False)
                        has_attacked = getattr(target_state, 'has_attacked', False)
                        if not has_moved and not has_attacked:
                            modifiers['can_attack'] = False
                            if has_experienced_recon:
                                modifiers['notes'].append(f"Camouflaged: Can't attack at long range until target moves/attacks")
                            else:
                                modifiers['notes'].append(f"Camouflaged: Can't attack at medium/long range until target moves/attacks")
                elif has_experienced_recon and distance >= 2:
                    modifiers['notes'].append(f"Experienced Recon: Can attack Camouflaged at medium range")
                break

        # Concealed: "While this unit has cover enemy units can't attack at long range"
        for ability in target_abilities:
            if ability.lower() == 'concealed':
                cover_terrains = ('forest', 'building', 'hill', 'town', 'ruins')
                has_cover = target_terrain in cover_terrains
                is_long_range = distance >= 5
                if has_cover and is_long_range:
                    modifiers['can_attack'] = False
                    modifiers['notes'].append(f"Concealed: Can't attack target at long range while in cover")
                break

        # Intimidation: "Enemy Soldiers adjacent to this unit get –1 on each attack die."
        # This checks if attacker is a Soldier and target has Intimidation and is adjacent
        if distance <= 1 and unit.unit_type == 'Soldier':
            for ability in target_abilities:
                if ability.lower() == 'intimidation':
                    modifiers['hit_modifier'] = modifiers.get('hit_modifier', 0) + 1
                    new_threshold = modifiers['hit_threshold'] + modifiers['hit_modifier']
                    modifiers['notes'].append(f"Intimidation: -1 on each attack die (hits on {new_threshold}+)")
                    break

        return modifiers
    
    def get_defense_modifiers(self, unit, terrain: str = 'open',
                             is_rear_attack: bool = False,
                             attacker=None, distance: int = 0,
                             game_state=None, unit_state=None) -> Dict:
        """
        Analyze unit's defense-related abilities.
        Returns dict with defense modifications.

        Args:
            unit: The defending unit
            terrain: Terrain type of defender's hex
            is_rear_attack: True if being attacked from the rear
            attacker: The attacking unit (needed for Sideskirts, Gun Shield checks)
            distance: Attack distance in hexes (for Gun Shield)
            game_state: GameState for checking Pillbox and other position-based effects
            unit_state: UnitState of the defending unit
        """
        modifiers = {
            'defense_bonus': 0,
            'superior_armor': 0,  # Superior Armor X: need to exceed defense by X for 2 hits
            'cover_bonus': 0,
            'immune_to_cover': False,
            'special_saves': [],
            'notes': []
        }

        # Pillbox: Soldiers in same hex as friendly Pillbox get cover and +1 on cover rolls
        if game_state and unit_state and unit.unit_type == 'Soldier':
            position = unit_state.position
            units_in_hex = game_state.get_units_at_position(position[0], position[1])
            for hex_unit_state in units_in_hex:
                if hex_unit_state.unit.id == unit.id:
                    continue
                if hex_unit_state.owner != unit_state.owner:
                    continue  # Must be friendly
                hex_abilities = getattr(hex_unit_state.unit, 'abilities', []) or []
                has_pillbox = any('pillbox' in a.lower() for a in hex_abilities)
                if has_pillbox:
                    modifiers['cover_bonus'] += 1
                    modifiers['provides_cover'] = True
                    modifiers['notes'].append("Pillbox: +1 on cover rolls")
                    break  # Only one Pillbox bonus

        # Check all abilities directly for Superior Armor X or Steely Resolve X
        for ability in (unit.abilities or []):
            sa_match = re.match(r'Superior Armor\s+(\d+)', ability, re.IGNORECASE)
            if sa_match:
                value = int(sa_match.group(1))
                modifiers['superior_armor'] = value
                modifiers['notes'].append(f"{ability}: Need to exceed defense by {value} for 2 hits")

            # Steely Resolve X works the same as Superior Armor X
            sr_match = re.match(r'Steely Resolve\s+(\d+)', ability, re.IGNORECASE)
            if sr_match:
                value = int(sr_match.group(1))
                modifiers['superior_armor'] = value  # Use same field as Superior Armor
                modifiers['notes'].append(f"{ability}: Need to exceed defense by {value} for 2 hits")

        defense_abilities = self.unit_has_any_ability_in_category(unit, 'defense')
        cover_abilities = self.unit_has_any_ability_in_category(unit, 'cover')

        for ability in defense_abilities:
            description = self.get_ability_description(ability)
            if not description:
                continue
            description_lower = description.lower()
            
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

        # Check for cover roll modifiers directly on unit abilities
        for ability in (unit.abilities or []):
            ability_lower = ability.lower()

            # Hard to Spot: +1 on cover rolls
            if ability_lower == 'hard to spot':
                modifiers['cover_bonus'] = modifiers.get('cover_bonus', 0) + 1
                modifiers['notes'].append(f"Hard to Spot: +1 on cover rolls")

            # Large Silhouette: -1 on cover rolls
            if ability_lower == 'large silhouette':
                modifiers['cover_bonus'] = modifiers.get('cover_bonus', 0) - 1
                modifiers['notes'].append(f"Large Silhouette: -1 on cover rolls")

            # Tall Silhouette: fails cover rolls (no cover)
            if ability_lower == 'tall silhouette':
                modifiers['fails_cover_rolls'] = True
                modifiers['notes'].append(f"Tall Silhouette: Fails all cover rolls")

            # Sideskirts: +1/+1 defense against units that have the close assault ability
            if ability_lower == 'sideskirts' and attacker is not None:
                attacker_abilities = getattr(attacker, 'abilities', []) or []
                has_close_assault = any(
                    'close assault' in a.lower()
                    for a in attacker_abilities
                )
                if has_close_assault:
                    modifiers['defense_bonus'] = modifiers.get('defense_bonus', 0) + 1
                    modifiers['notes'].append(f"Sideskirts: +1/+1 defense vs Close Assault")

            # Gun Shield: +1/+1 defense against Soldiers at long range (5+ hexes)
            if ability_lower == 'gun shield' and attacker is not None:
                is_attacker_soldier = getattr(attacker, 'unit_type', '') == 'Soldier'
                is_long_range = distance >= 5
                if is_attacker_soldier and is_long_range:
                    modifiers['defense_bonus'] = modifiers.get('defense_bonus', 0) + 1
                    modifiers['notes'].append(f"Gun Shield: +1/+1 defense vs Soldiers at long range")

            # Elusive: +1/+1 defense while in forest or marsh hex
            if ability_lower == 'elusive':
                if terrain in ('forest', 'marsh'):
                    modifiers['defense_bonus'] = modifiers.get('defense_bonus', 0) + 1
                    modifiers['notes'].append(f"Elusive: +1/+1 defense in {terrain}")

            # Low Silhouette: gets cover rolls in clear hexes and succeeds on a roll of 6
            if ability_lower == 'low silhouette':
                modifiers['low_silhouette'] = True
                modifiers['notes'].append(f"Low Silhouette: Cover rolls in clear hexes (succeed on 6)")

            # Heavy Armor: "Ignore the first Damaged counter this unit receives each game"
            if ability_lower == 'heavy armor':
                modifiers['heavy_armor'] = True
                modifiers['notes'].append(f"Heavy Armor: Ignore first Damaged counter each game")

            # Superior Frontal Armor X: Like Superior Armor but only for front attacks
            sfa_match = re.match(r'superior frontal armor\s+(\d+)', ability, re.IGNORECASE)
            if sfa_match:
                if not is_rear_attack:
                    value = int(sfa_match.group(1))
                    # Only apply if greater than existing superior_armor
                    if value > modifiers.get('superior_armor', 0):
                        modifiers['superior_armor'] = value
                        modifiers['notes'].append(f"Superior Frontal Armor {value}: Need to exceed front defense by {value} for 2 hits")

            # Fanatic: "This unit ignores face-up Disrupted counters"
            if ability_lower == 'fanatic':
                modifiers['fanatic'] = True
                modifiers['notes'].append(f"Fanatic: Ignores face-up Disrupted counters")

            # Mobility: "+1/+1 defense against defensive-fire attacks"
            if ability_lower == 'mobility':
                modifiers['mobility'] = True
                modifiers['notes'].append(f"Mobility: +1/+1 defense vs defensive-fire attacks")

            # Entrenched: "Until this unit moves, it gets +1/+1 defense"
            if ability_lower == 'entrenched':
                modifiers['entrenched'] = True
                modifiers['notes'].append(f"Entrenched: +1/+1 defense until unit moves")

            # Urban Combat: "+1 on cover rolls while in a town hex"
            if ability_lower == 'urban combat':
                if terrain == 'town':
                    modifiers['cover_bonus'] = modifiers.get('cover_bonus', 0) + 1
                    modifiers['notes'].append(f"Urban Combat: +1 on cover rolls in town")

            # Gung Ho: "+1 on each attack die when making defensive-fire attacks"
            if ability_lower == 'gung ho':
                modifiers['gung_ho'] = True
                modifiers['notes'].append(f"Gung Ho: +1 on each attack die for defensive fire")

            # Slow: "Enemy Aircraft get +1 on each attack die when attacking this unit"
            if ability_lower == 'slow':
                if attacker and attacker.unit_type == 'Aircraft':
                    modifiers['slow_vs_aircraft'] = True
                    modifiers['notes'].append(f"Slow: Enemy Aircraft get +1 on attack dice")

            # Quick Swivel: "+1 attack die when making defensive-fire attacks"
            if ability_lower == 'quick swivel':
                modifiers['quick_swivel'] = True
                modifiers['notes'].append(f"Quick Swivel: +1 attack die for defensive fire")

            # Forest Camouflage: Auto-succeed cover vs long range in forest
            if ability_lower == 'forest camouflage':
                if terrain == 'forest' and distance >= 5:
                    modifiers['auto_cover_success'] = True
                    modifiers['notes'].append(f"Forest Camouflage: Auto-succeed cover vs long range")

            # Subtle: +1 on cover rolls against long-range attacks
            if ability_lower == 'subtle':
                if distance >= 5:
                    modifiers['cover_bonus'] = modifiers.get('cover_bonus', 0) + 1
                    modifiers['notes'].append(f"Subtle: +1 on cover rolls vs long range")

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