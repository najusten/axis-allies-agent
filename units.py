import csv

class Unit:
    """Represents a single Axis & Allies miniature unit"""
    
    def __init__(self, name, nation, unit_type, year, cost, defense, speed,
                 veh_short, veh_medium, veh_long,
                 per_short, per_medium, per_long,
                 abilities):
        self.name = name
        self.nation = nation
        self.unit_type = unit_type
        self.year = int(year) if year and year != '-' else None
        self.cost = float(cost) if cost and cost != '-' else 0
        self.cost = float(cost) if cost and cost != '-' else 0
        
        # Handle front/rear defense values (e.g., "4/3" or "4 4")
        if defense and defense != '-':
            # Try splitting by '/' first, then by space
            if '/' in defense:
                defense_parts = defense.split('/')
            else:
                defense_parts = defense.split()
            
            self.defense_front = int(defense_parts[0])
            self.defense_rear = int(defense_parts[1]) if len(defense_parts) > 1 else int(defense_parts[0])
        else:
            self.defense_front = None
            self.defense_rear = None
        
        # Handle speed (some units have 'A' for aircraft)
        if speed and speed != '-':
            self.speed = speed if speed == 'A' else int(speed)
        else:
            self.speed = 0      

        # Attack values against vehicles
        self.veh_short = int(veh_short) if veh_short and veh_short != '-' else 0
        self.veh_medium = int(veh_medium) if veh_medium and veh_medium != '-' else 0
        self.veh_long = int(veh_long) if veh_long and veh_long != '-' else 0
        
        # Attack values against personnel
        self.per_short = int(per_short) if per_short and per_short != '-' else 0
        self.per_medium = int(per_medium) if per_medium and per_medium != '-' else 0
        self.per_long = int(per_long) if per_long and per_long != '-' else 0
        
        # Parse abilities (comma-separated string)
        self.abilities = [a.strip() for a in abilities.split(',')] if abilities else []
    
    def __str__(self):
        return f"{self.name} ({self.nation}) - Type: {self.unit_type}, Cost: {self.cost}, Speed: {self.speed}"
    
    def can_attack_vehicle(self):
        """Check if this unit can attack vehicles"""
        return self.veh_short > 0 or self.veh_medium > 0 or self.veh_long > 0
    
    def can_attack_personnel(self):
        """Check if this unit can attack personnel"""
        return self.per_short > 0 or self.per_medium > 0 or self.per_long > 0


def load_abilities():
    """Load special abilities from CSV"""
    abilities = {}
    with open('Axis and Allies Unit Data for Analysis - Special_Abilities.csv', 'r') as file:
        reader = csv.DictReader(file)
        for row in reader:
            ability_name = row['Ability Name'].strip()
            description = row['Description'].strip()
            abilities[ability_name] = description
    return abilities


def load_units():
    """Load all units from CSV"""
    units = []
    with open('Axis and Allies Unit Data for Analysis - Unit_Stats.csv', 'r') as file:
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
    return units


# Test the code
if __name__ == "__main__":
    print("Loading abilities...")
    abilities = load_abilities()
    print(f"Loaded {len(abilities)} special abilities\n")
    
    print("Loading units...")
    units = load_units()
    print(f"Loaded {len(units)} units\n")
    
    # Show first 5 combat units (skip obstacles)
    print("=== Sample Combat Units ===")
    combat_units = [u for u in units if u.unit_type not in ['Obstacle']]
    for unit in combat_units[:5]:
        print(unit)
        if unit.abilities:
            print(f"  Abilities: {', '.join(unit.abilities)}")
        print()