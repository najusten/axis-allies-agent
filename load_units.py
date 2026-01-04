import csv

# Read the unit stats
print("=== UNIT STATS ===")
with open('Axis and Allies Unit Data for Analysis - Unit_Stats.csv', 'r') as file:
    reader = csv.DictReader(file)
    stats_data = list(reader)
    
    # Print the column headers
    print("Columns:", stats_data[0].keys())
    print()
    
    # Print first 3 units as examples
    for i, unit in enumerate(stats_data[:3]):
        print(f"Unit {i+1}:")
        for key, value in unit.items():
            print(f"  {key}: {value}")
        print()

print("\n" + "="*50 + "\n")

# Read the special abilities
print("=== SPECIAL ABILITIES ===")
with open('Axis and Allies Unit Data for Analysis - Special_Abilities.csv', 'r') as file:
    reader = csv.DictReader(file)
    abilities_data = list(reader)
    
    # Print the column headers
    print("Columns:", abilities_data[0].keys())
    print()
    
    # Print first 3 abilities as examples
    for i, ability in enumerate(abilities_data[:3]):
        print(f"Ability {i+1}:")
        for key, value in ability.items():
            print(f"  {key}: {value}")
        print()