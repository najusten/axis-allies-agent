"""
Game State Manager for Axis & Allies Miniatures

Manages the complete game state including:
- Board and terrain
- All units and their positions
- Turn/phase tracking
- Action history
- Victory conditions
"""

from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass, field
from copy import deepcopy
import uuid

from board import Board, Hex
from units import Unit
from action import Action, MoveAction, AttackAction


@dataclass
class UnitState:
    """Tracks the state of a single unit"""
    unit: Unit
    position: Tuple[int, int]  # (q, r) hex coordinates
    owner: str  # 'player1' or 'player2'
    current_health: int
    has_moved: bool = False
    has_attacked: bool = False
    abilities_used: Set[str] = field(default_factory=set)
    is_disrupted: bool = False  # Status effects
    is_damaged: bool = False
    
    def __post_init__(self):
        # Ensure unit has an ID
        if not hasattr(self.unit, 'id') or not self.unit.id:
            self.unit.id = str(uuid.uuid4())
        
        # Initialize health if not set
        if self.current_health is None:
            self.current_health = getattr(self.unit, 'defense_front', 3)
    
    @property
    def is_alive(self) -> bool:
        """Check if unit is still alive"""
        return self.current_health > 0
    
    def reset_for_turn(self):
        """Reset per-turn flags"""
        self.has_moved = False
        self.has_attacked = False
        self.abilities_used.clear()


class GamePhase:
    """Enumeration of game phases"""
    DEPLOYMENT = "deployment"
    MOVEMENT = "movement"
    ASSAULT = "assault"
    CONSOLIDATION = "consolidation"
    END = "end"
    
    @staticmethod
    def get_phase_order() -> List[str]:
        """Get phases in order"""
        return [
            GamePhase.DEPLOYMENT,
            GamePhase.MOVEMENT,
            GamePhase.ASSAULT,
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
                 player2_units: List[UnitState] = None):
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
            
            # Remove from board
            q, r = unit_state.position
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
    
    def mark_unit_attacked(self, unit_id: str):
        """Mark that a unit has attacked this turn"""
        if unit_id in self.units:
            self.units[unit_id].has_attacked = True
    
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
        
        # If back to player1, increment turn counter
        if self.active_player == "player1":
            self.turn_number += 1
        
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
        
        # Could add other victory conditions here:
        # - Objective control
        # - Point victory
        # - Turn limit
        
        return None
    
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
                abilities_used=unit_state.abilities_used.copy(),
                is_disrupted=unit_state.is_disrupted,
                is_damaged=unit_state.is_damaged
            )
            
            if unit_state.owner == "player1":
                new_p1_units.append(new_unit_state)
            else:
                new_p2_units.append(new_unit_state)
        
        # Create new game state
        new_state = GameState(new_board, new_p1_units, new_p2_units)
        new_state.turn_number = self.turn_number
        new_state.current_phase = self.current_phase
        new_state.active_player = self.active_player
        new_state.action_history = self.action_history.copy()
        new_state.winner = self.winner
        new_state.game_over = self.game_over
        
        return new_state
    
    def get_state_summary(self) -> str:
        """Get a human-readable summary of the game state"""
        p1_units = self.get_units_by_owner("player1")
        p2_units = self.get_units_by_owner("player2")
        
        summary = f"Turn {self.turn_number} - {self.current_phase.upper()} Phase\n"
        summary += f"Active Player: {self.active_player}\n"
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