"""
Game State Manager for Axis & Allies Miniatures

Manages the complete game state including:
- Board and terrain
- All units and their positions
- Turn/phase tracking
- Action history
- Victory conditions
"""

from typing import Dict, List, Tuple, Optional, Set, TYPE_CHECKING
from dataclasses import dataclass, field
from copy import deepcopy
import uuid

from board import Board, Hex
from units import Unit
from action import Action, MoveAction, AttackAction

if TYPE_CHECKING:
    from facing import HexDirection


@dataclass
class UnitState:
    """Tracks the state of a single unit"""
    unit: Unit
    position: Tuple[int, int]  # (q, r) hex coordinates
    owner: str  # 'player1' or 'player2'
    current_health: int
    has_moved: bool = False
    has_attacked: bool = False  # Kept for backwards compatibility
    attacks_this_turn: int = 0  # For Double Shot tracking
    targets_attacked_this_turn: Set[str] = field(default_factory=set)  # For Coordinated Fire C.A.
    abilities_used: Set[str] = field(default_factory=set)
    is_disrupted: bool = False  # Status effects
    is_damaged: bool = False
    facing: Optional[int] = None  # HexDirection value (0-5) for vehicles, None for soldiers
    strike_and_fade_available: bool = False  # Enabled after attack if unit has Strike and Fade
    heavy_armor_used: bool = False  # Heavy Armor: ignore first Damaged counter each game
    covering_fire_target: bool = False  # True if hit by Covering Fire this turn (can't defensive fire)
    all_guns_blazing_available: bool = False  # Enabled after attack if unit has All Guns Blazing
    strafe_available: bool = False  # Enabled after attack if unit has Strafe (attack adjacent Soldier)
    strafe_target_hex: Optional[Tuple[int, int]] = None  # Hex of original target for Strafe
    bombs_used: bool = False  # Bombs: once per game special attack
    speed_boost_used: bool = False  # Speed Boost: once per game, attack vs Aircraft resolves immediately
    overrun_used_this_phase: bool = False  # Overrun: once per phase, disrupt Soldier when entering hex
    extra_mg_used: bool = False  # Extra Machine Guns: once per turn, extra attack vs Soldier
    multiturreted_front_used: bool = False  # Multiturreted: tracks if front-arc attack was used
    multiturreted_rear_used: bool = False  # Multiturreted: tracks if rear-arc attack was used
    rapid_fire_used: bool = False  # Rapid Fire: once per turn, extra attack with jam risk
    rapid_fire_jammed: bool = False  # Rapid Fire: True if disruption was caused by Rapid Fire (sticky)
    overheat_jammed: bool = False  # Overheat: True if disruption was caused by Overheat (sticky)
    unreliable_disrupted: bool = False  # Unreliable: True if disruption is sticky (never clears)
    carried_unit_id: Optional[str] = None  # Transport: ID of soldier being carried
    carried_by_id: Optional[str] = None  # Soldier: ID of transport carrying this unit
    smoke_screen_used: bool = False  # Smoke Screen: once per game ability
    is_deployed: bool = True  # Paratrooper: False if not yet deployed on map
    is_aircraft_on_map: bool = False  # Aircraft: True when placed during Flight phase
    shock_troop_used: bool = False  # Shock Troop: True after first attack this game
    armor_piercing_used: bool = False  # Armor-Piercing Rounds: once per game
    he_round_used: bool = False  # HE Round: once per game
    headshot_used: bool = False  # Headshot: once per game
    lead_the_way_used: bool = False  # Lead the Way: once per turn reroll
    firepower_used: bool = False  # Firepower: once per turn, extra attack vs unit in same hex
    remote_control_used: bool = False  # Remote Control: once per game special attack
    rocket_salvo_used: bool = False  # Rocket Salvo: once per game area attack
    rockets_8_used: bool = False  # Rockets 8: once per game, 8 dice vs target within 4 hexes
    top_mounted_rockets_used: bool = False  # Top-Mounted Rockets: once per game area attack
    additional_hull_cannon_used: bool = False  # Additional Hull-Mounted Cannon: once per turn
    extra_hull_cannon_used: bool = False  # Extra Hull-Mounted Cannon: once per turn
    quick_reactions_available: bool = False  # Command Quick Reactions: can change facing after attack

    def __post_init__(self):
        # Ensure unit has an ID
        if not hasattr(self.unit, 'id') or not self.unit.id:
            self.unit.id = str(uuid.uuid4())
        
        # Initialize health if not set
        if self.current_health is None:
            self.current_health = getattr(self.unit, 'defense_front', 3)
        
        # Initialize facing for vehicles
        if self.unit.unit_type == 'Vehicle' and self.facing is None:
            self.facing = 0  # Default facing: East
    
    @property
    def is_alive(self) -> bool:
        """Check if unit is still alive"""
        return self.current_health > 0
    
    def reset_for_turn(self):
        """Reset per-turn flags"""
        self.has_moved = False
        self.has_attacked = False
        self.attacks_this_turn = 0
        self.strike_and_fade_available = False
        self.all_guns_blazing_available = False
        self.strafe_available = False
        self.strafe_target_hex = None
        self.abilities_used.clear()
        self.covering_fire_target = False  # Reset Covering Fire effect each turn
        self.overrun_used_this_phase = False  # Reset Overrun each turn
        self.extra_mg_used = False  # Reset Extra Machine Guns each turn
        self.multiturreted_front_used = False  # Reset Multiturreted each turn
        self.multiturreted_rear_used = False
        self.rapid_fire_used = False  # Reset Rapid Fire each turn
        self.lead_the_way_used = False  # Reset Lead the Way each turn
        self.firepower_used = False  # Reset Firepower each turn
        self.targets_attacked_this_turn.clear()  # Reset Coordinated Fire C.A. tracking
        self.quick_reactions_available = False  # Reset Command Quick Reactions
        self.additional_hull_cannon_used = False  # Reset Additional Hull-Mounted Cannon
        self.extra_hull_cannon_used = False  # Reset Extra Hull-Mounted Cannon
        # Note: rapid_fire_jammed is NOT reset here (sticky disruption)
        # Note: bombs_used is NOT reset (once per game)
        self.is_aircraft_on_map = False  # Aircraft removed at end of turn


class GamePhase:
    """Enumeration of game phases"""
    DEPLOYMENT = "deployment"
    MOVEMENT = "movement"
    FLIGHT = "flight"  # Aircraft placement
    ASSAULT = "assault"
    AIRSTRIKE = "airstrike"  # Aircraft attacks
    CONSOLIDATION = "consolidation"
    END = "end"

    @staticmethod
    def get_phase_order() -> List[str]:
        """Get phases in order"""
        return [
            GamePhase.DEPLOYMENT,
            GamePhase.MOVEMENT,
            GamePhase.FLIGHT,
            GamePhase.ASSAULT,
            GamePhase.AIRSTRIKE,
            GamePhase.CONSOLIDATION,
            GamePhase.END
        ]
    
    @staticmethod
    def next_phase(current_phase: str) -> str:
        """Get the next phase in sequence"""
        phases = GamePhase.get_phase_order()
        try:
            current_idx = phases.index(current_phase)
            if current_idx < len(phases) - 1:
                return phases[current_idx + 1]
            return GamePhase.END
        except ValueError:
            return GamePhase.MOVEMENT


class GameState:
    """
    Complete game state manager.
    Tracks everything needed to represent the current state of the game.
    """
    
    def __init__(self, board: Board, player1_units: List[UnitState] = None,
                 player2_units: List[UnitState] = None,
                 objective_position: Tuple[int, int] = None):
        self.board = board
        self.turn_number = 1
        self.current_phase = GamePhase.DEPLOYMENT
        self.active_player = "player1"

        # Unit tracking
        self.units: Dict[str, UnitState] = {}

        # Add player units
        if player1_units:
            for unit_state in player1_units:
                self.add_unit(unit_state)

        if player2_units:
            for unit_state in player2_units:
                self.add_unit(unit_state)

        # Action history
        self.action_history: List[Action] = []

        # Victory conditions
        self.winner: Optional[str] = None
        self.game_over = False

        # Smoke screens on hexes (blocks LOS, removed at end of turn)
        self.smoke_screens: Set[Tuple[int, int]] = set()

        # Track destroyed units for Fury/Tides of War abilities
        self.units_destroyed_this_turn: Dict[str, List[str]] = {"player1": [], "player2": []}  # owner -> list of destroyed unit IDs
        self.units_destroyed_last_turn: Dict[str, List[str]] = {"player1": [], "player2": []}

        # Track destroyed unit wrecks for Improvisation ability
        # Maps (q, r) -> list of {unit_type, attack_values, name}
        self.destroyed_unit_wrecks: Dict[Tuple[int, int], List[dict]] = {}

        # Objective position (defaults to center of board if not specified)
        if objective_position is not None:
            self.objective_position = objective_position
        else:
            self.objective_position = (board.width // 2, board.height // 2)
    
    def add_unit(self, unit_state: UnitState):
        """Add a unit to the game state"""
        unit_id = unit_state.unit.id
        self.units[unit_id] = unit_state
        
        # Place unit on board
        q, r = unit_state.position
        hex_tile = self.board.get_hex(q, r)
        if hex_tile:
            hex_tile.unit = unit_state.unit
    
    def remove_unit(self, unit_id: str):
        """Remove a unit from the game (death)"""
        if unit_id in self.units:
            unit_state = self.units[unit_id]
            unit = unit_state.unit

            # Track destroyed unit for Fury/Tides of War abilities
            owner = unit_state.owner
            if owner in self.units_destroyed_this_turn:
                self.units_destroyed_this_turn[owner].append(unit_id)

            # Track wreck for Improvisation ability (only Soldiers and Vehicles)
            q, r = unit_state.position
            if unit.unit_type in ('Soldier', 'Vehicle'):
                wreck_data = {
                    'unit_type': unit.unit_type,
                    'name': unit.name,
                    'attack_close': getattr(unit, 'attack_close', 0),
                    'attack_medium': getattr(unit, 'attack_medium', 0),
                    'attack_long': getattr(unit, 'attack_long', 0),
                    'attack_versus_soldier': getattr(unit, 'attack_versus_soldier', None),
                    'attack_versus_vehicle': getattr(unit, 'attack_versus_vehicle', None),
                }
                if (q, r) not in self.destroyed_unit_wrecks:
                    self.destroyed_unit_wrecks[(q, r)] = []
                self.destroyed_unit_wrecks[(q, r)].append(wreck_data)

            # Remove from board
            hex_tile = self.board.get_hex(q, r)
            if hex_tile:
                hex_tile.unit = None

            # Remove from units dict
            del self.units[unit_id]
    
    def get_unit(self, unit_id: str) -> Optional[Unit]:
        """Get a unit by ID"""
        if unit_id in self.units:
            return self.units[unit_id].unit
        return None
    
    def get_unit_state(self, unit_id: str) -> Optional[UnitState]:
        """Get complete unit state by ID"""
        return self.units.get(unit_id)
    
    def get_unit_position(self, unit_id: str) -> Optional[Tuple[int, int]]:
        """Get unit's current position"""
        if unit_id in self.units:
            return self.units[unit_id].position
        return None
    
    def get_unit_owner(self, unit_id: str) -> Optional[str]:
        """Get which player owns this unit"""
        if unit_id in self.units:
            return self.units[unit_id].owner
        return None
    
    def get_units_by_owner(self, owner: str) -> List[UnitState]:
        """Get all units owned by a player"""
        return [us for us in self.units.values() if us.owner == owner]
    
    def get_enemy_units(self, owner: str) -> List[UnitState]:
        """Get all enemy units"""
        enemy = "player2" if owner == "player1" else "player1"
        return self.get_units_by_owner(enemy)

    def get_destroyed_wrecks_at_position(self, q: int, r: int) -> List[dict]:
        """Get destroyed unit wrecks at a position (for Improvisation ability)"""
        return self.destroyed_unit_wrecks.get((q, r), [])

    def get_units_at_position(self, q: int, r: int) -> List[UnitState]:
        """Get all units at a specific hex position"""
        return [us for us in self.units.values() if us.position == (q, r) and us.is_alive]

    def has_smoke(self, q: int, r: int) -> bool:
        """Check if a hex has smoke screen"""
        return (q, r) in self.smoke_screens

    def add_smoke(self, q: int, r: int):
        """Add a smoke screen to a hex"""
        self.smoke_screens.add((q, r))

    def has_unit_moved(self, unit_id: str) -> bool:
        """Check if unit has moved this turn"""
        if unit_id in self.units:
            return self.units[unit_id].has_moved
        return False
    
    def has_unit_attacked(self, unit_id: str) -> bool:
        """Check if unit has attacked this turn"""
        if unit_id in self.units:
            return self.units[unit_id].has_attacked
        return False
    
    def has_ability_been_used(self, unit_id: str, ability_name: str) -> bool:
        """Check if unit has used a specific ability this turn"""
        if unit_id in self.units:
            return ability_name in self.units[unit_id].abilities_used
        return False
    
    def move_unit(self, unit_id: str, to_q: int, to_r: int):
        """Move a unit to a new position"""
        if unit_id not in self.units:
            return False
        
        unit_state = self.units[unit_id]
        old_q, old_r = unit_state.position
        
        # Remove from old position
        old_hex = self.board.get_hex(old_q, old_r)
        if old_hex:
            old_hex.unit = None
        
        # Add to new position
        new_hex = self.board.get_hex(to_q, to_r)
        if new_hex:
            new_hex.unit = unit_state.unit
            unit_state.position = (to_q, to_r)
            unit_state.has_moved = True
            return True
        
        return False
    
    def damage_unit(self, unit_id: str, damage: int):
        """Apply damage to a unit"""
        if unit_id in self.units:
            unit_state = self.units[unit_id]
            unit_state.current_health -= damage
            
            if unit_state.current_health <= 0:
                self.remove_unit(unit_id)
                return True  # Unit destroyed
        
        return False  # Unit still alive
    
    def mark_unit_attacked(self, unit_id: str, target_id: str = None):
        """Mark that a unit has attacked this turn, optionally tracking the target"""
        if unit_id in self.units:
            unit_state = self.units[unit_id]
            unit_state.attacks_this_turn += 1
            # Track target for Coordinated Fire C.A.
            if target_id:
                unit_state.targets_attacked_this_turn.add(target_id)
            # Keep has_attacked for backwards compatibility
            # For Double Shot units, has_attacked becomes True after 2 attacks
            max_attacks = self.get_max_attacks(unit_id)
            if unit_state.attacks_this_turn >= max_attacks:
                unit_state.has_attacked = True

    def get_max_attacks(self, unit_id: str) -> int:
        """Get maximum attacks allowed for a unit (1 normally, 2 with Double Shot/Multiturreted)"""
        if unit_id not in self.units:
            return 1
        unit = self.units[unit_id].unit
        abilities = getattr(unit, 'abilities', []) or []
        # Check for Double Shot or Multiturreted ability
        for ability in abilities:
            if 'double shot' in ability.lower():
                return 2
            if 'multiturreted' in ability.lower():
                return 2
        return 1

    def can_unit_attack(self, unit_id: str) -> bool:
        """Check if unit can still attack this turn"""
        if unit_id not in self.units:
            return False
        unit_state = self.units[unit_id]
        max_attacks = self.get_max_attacks(unit_id)
        return unit_state.attacks_this_turn < max_attacks
    
    def mark_ability_used(self, unit_id: str, ability_name: str):
        """Mark that a unit has used an ability this turn"""
        if unit_id in self.units:
            self.units[unit_id].abilities_used.add(ability_name)
    
    def next_phase(self):
        """Advance to the next phase"""
        next_phase = GamePhase.next_phase(self.current_phase)
        
        if next_phase == GamePhase.END:
            # End of turn, switch players and reset
            self.end_turn()
        else:
            self.current_phase = next_phase
    
    def end_turn(self):
        """End the current turn and start next player's turn"""
        # Switch active player
        self.active_player = "player2" if self.active_player == "player1" else "player1"

        # Reset all units for new turn
        for unit_state in self.units.values():
            unit_state.reset_for_turn()

        # Clear smoke screens at end of turn
        self.smoke_screens.clear()

        # If back to player1, increment turn counter
        if self.active_player == "player1":
            self.turn_number += 1
            # Shift destroyed tracking for new turn (Fury/Tides of War)
            self.units_destroyed_last_turn = self.units_destroyed_this_turn.copy()
            self.units_destroyed_this_turn = {"player1": [], "player2": []}

        # Reset to first phase
        self.current_phase = GamePhase.MOVEMENT
    
    def apply_action(self, action: Action):
        """Apply an action to the game state and record it"""
        self.action_history.append(action)
        
        # Action-specific application logic would go here
        # This will be implemented by the ActionExecutor class
    
    def check_victory_conditions(self) -> Optional[str]:
        """
        Check if the game is over and who won.
        Returns winner ('player1' or 'player2') or None if game continues.
        """
        # Count alive units for each player
        p1_units = [u for u in self.get_units_by_owner("player1") if u.is_alive]
        p2_units = [u for u in self.get_units_by_owner("player2") if u.is_alive]
        
        # Total elimination victory
        if len(p1_units) == 0 and len(p2_units) > 0:
            self.winner = "player2"
            self.game_over = True
            return "player2"
        
        if len(p2_units) == 0 and len(p1_units) > 0:
            self.winner = "player1"
            self.game_over = True
            return "player1"
        
        # Objective control is checked separately by GameRunner at end of turn
        # (not during phases)

        return None

    def get_units_adjacent_to_objective(self, player: str = None) -> List['UnitState']:
        """
        Get all units adjacent to the objective (in same hex or 1 hex away).

        Args:
            player: If specified, only return units belonging to this player.
                   If None, return all units.

        Returns:
            List of UnitState objects adjacent to objective.
        """
        obj_q, obj_r = self.objective_position
        adjacent_units = []

        for unit_state in self.units.values():
            if not unit_state.is_alive:
                continue

            if player is not None and unit_state.owner != player:
                continue

            # Check if unit is adjacent (distance 0 or 1)
            unit_q, unit_r = unit_state.position
            distance = self.board.hex_distance(obj_q, obj_r, unit_q, unit_r)

            if distance <= 1:
                adjacent_units.append(unit_state)

        return adjacent_units

    def check_objective_control(self) -> Optional[str]:
        """
        Check who controls the objective.

        Control = being the ONLY player with units adjacent to the objective.
        Adjacent = in the same hex OR one hex away.

        Returns:
            'player1' if player1 controls, 'player2' if player2 controls,
            None if contested (both have units) or uncontrolled (neither has units).
        """
        p1_adjacent = self.get_units_adjacent_to_objective("player1")
        p2_adjacent = self.get_units_adjacent_to_objective("player2")

        p1_has_units = len(p1_adjacent) > 0
        p2_has_units = len(p2_adjacent) > 0

        if p1_has_units and not p2_has_units:
            return "player1"
        elif p2_has_units and not p1_has_units:
            return "player2"
        else:
            # Either contested (both have units) or uncontrolled (neither)
            return None

    def get_total_points(self, player: str) -> float:
        """
        Get total point cost of surviving units for a player.
        Used for tiebreaker at turn 10.
        """
        total = 0.0
        for unit_state in self.get_units_by_owner(player):
            if unit_state.is_alive:
                cost = getattr(unit_state.unit, 'cost', 0) or 0
                total += cost
        return total

    def is_game_over(self) -> bool:
        """Check if the game has ended"""
        return self.game_over or self.check_victory_conditions() is not None
    
    def get_winner(self) -> Optional[str]:
        """Get the winner if game is over"""
        if not self.game_over:
            self.check_victory_conditions()
        return self.winner
    
    def clone(self) -> 'GameState':
        """
        Create a deep copy of the game state.
        Useful for look-ahead search and AI planning.
        """
        # Create new board
        new_board = Board(self.board.width, self.board.height)
        
        # Copy terrain
        for q in range(self.board.width):
            for r in range(self.board.height):
                old_hex = self.board.get_hex(q, r)
                if old_hex:
                    new_board.set_terrain(q, r, old_hex.terrain)
        
        # Deep copy units
        new_p1_units = []
        new_p2_units = []
        
        for unit_state in self.units.values():
            new_unit = deepcopy(unit_state.unit)
            new_unit_state = UnitState(
                unit=new_unit,
                position=unit_state.position,
                owner=unit_state.owner,
                current_health=unit_state.current_health,
                has_moved=unit_state.has_moved,
                has_attacked=unit_state.has_attacked,
                attacks_this_turn=unit_state.attacks_this_turn,
                abilities_used=unit_state.abilities_used.copy(),
                is_disrupted=unit_state.is_disrupted,
                is_damaged=unit_state.is_damaged,
                facing=unit_state.facing,
                strike_and_fade_available=unit_state.strike_and_fade_available,
                heavy_armor_used=unit_state.heavy_armor_used,
                covering_fire_target=unit_state.covering_fire_target,
                all_guns_blazing_available=unit_state.all_guns_blazing_available,
                strafe_available=unit_state.strafe_available,
                strafe_target_hex=unit_state.strafe_target_hex,
                bombs_used=unit_state.bombs_used,
                speed_boost_used=unit_state.speed_boost_used,
                overrun_used_this_phase=unit_state.overrun_used_this_phase,
                extra_mg_used=unit_state.extra_mg_used,
                multiturreted_front_used=unit_state.multiturreted_front_used,
                multiturreted_rear_used=unit_state.multiturreted_rear_used,
                rapid_fire_used=unit_state.rapid_fire_used,
                rapid_fire_jammed=unit_state.rapid_fire_jammed,
                overheat_jammed=unit_state.overheat_jammed,
                unreliable_disrupted=unit_state.unreliable_disrupted,
                carried_unit_id=unit_state.carried_unit_id,
                carried_by_id=unit_state.carried_by_id,
                smoke_screen_used=unit_state.smoke_screen_used,
                is_deployed=unit_state.is_deployed,
                is_aircraft_on_map=unit_state.is_aircraft_on_map
            )
            
            if unit_state.owner == "player1":
                new_p1_units.append(new_unit_state)
            else:
                new_p2_units.append(new_unit_state)
        
        # Create new game state
        new_state = GameState(new_board, new_p1_units, new_p2_units,
                             objective_position=self.objective_position)
        new_state.turn_number = self.turn_number
        new_state.current_phase = self.current_phase
        new_state.active_player = self.active_player
        new_state.action_history = self.action_history.copy()
        new_state.winner = self.winner
        new_state.game_over = self.game_over
        new_state.smoke_screens = self.smoke_screens.copy()

        return new_state
    
    def get_state_summary(self) -> str:
        """Get a human-readable summary of the game state"""
        p1_units = self.get_units_by_owner("player1")
        p2_units = self.get_units_by_owner("player2")

        summary = f"Turn {self.turn_number} - {self.current_phase.upper()} Phase\n"
        summary += f"Active Player: {self.active_player}\n"

        # Objective status
        obj_q, obj_r = self.objective_position
        controller = self.check_objective_control()
        control_str = controller if controller else "contested/none"
        summary += f"Objective at ({obj_q},{obj_r}): {control_str}\n"

        summary += f"\nPlayer 1: {len(p1_units)} units\n"
        for us in p1_units[:5]:  # Show first 5
            summary += f"  - {us.unit.name} at ({us.position[0]},{us.position[1]}) HP:{us.current_health}\n"

        summary += f"\nPlayer 2: {len(p2_units)} units\n"
        for us in p2_units[:5]:  # Show first 5
            summary += f"  - {us.unit.name} at ({us.position[0]},{us.position[1]}) HP:{us.current_health}\n"

        if self.game_over:
            summary += f"\nGAME OVER - Winner: {self.winner}\n"

        return summary
    
    def __str__(self):
        return self.get_state_summary()


# Helper functions for creating game states

def create_test_game_state(board_size: int = 15) -> GameState:
    """Create a test game state with some units"""
    from units import load_units
    
    board = Board(width=board_size, height=board_size)
    
    # Add some terrain
    for q in range(6, 9):
        for r in range(6, 9):
            board.set_terrain(q, r, 'forest')
    
    # Load units
    all_units = load_units()
    
    # Get some test units
    infantry_units = [u for u in all_units if u.unit_type == 'Soldier']
    vehicle_units = [u for u in all_units if u.unit_type == 'Vehicle']
    
    # Create player 1 units
    p1_units = []
    if len(infantry_units) > 0:
        inf1 = deepcopy(infantry_units[0])
        inf1.id = "p1_inf1"
        # Use defense_front as the defense value
        defense = getattr(inf1, 'defense_front', 3)
        p1_units.append(UnitState(inf1, (3, 3), "player1", defense))
    
    if len(vehicle_units) > 0:
        tank1 = deepcopy(vehicle_units[0])
        tank1.id = "p1_tank1"
        defense = getattr(tank1, 'defense_front', 3)
        p1_units.append(UnitState(tank1, (5, 5), "player1", defense))
    
    # Create player 2 units
    p2_units = []
    if len(infantry_units) > 1:
        inf2 = deepcopy(infantry_units[1])
        inf2.id = "p2_inf1"
        defense = getattr(inf2, 'defense_front', 3)
        p2_units.append(UnitState(inf2, (10, 10), "player2", defense))
    
    if len(vehicle_units) > 1:
        tank2 = deepcopy(vehicle_units[1])
        tank2.id = "p2_tank1"
        defense = getattr(tank2, 'defense_front', 3)
        p2_units.append(UnitState(tank2, (8, 8), "player2", defense))
    
    # Create game state
    game_state = GameState(board, p1_units, p2_units)
    game_state.current_phase = GamePhase.MOVEMENT
    
    return game_state