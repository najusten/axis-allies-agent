"""
Action Generator for Axis & Allies Miniatures

Generates all legal actions available to a player in a given game state.
This is the core of the AI decision-making process.
"""

from typing import List, Tuple, Set
from board import Board
from game_state import GameState, UnitState, GamePhase
from action import (
    Action, MoveAction, AttackAction, MoveAndAttackAction, 
    UseAbilityAction, PassAction, EndPhaseAction,
    ActionValidator, create_move_action, create_attack_action
)
from movement import MovementSystem
from combat import CombatSystem
from abilities import AbilitySystem


class ActionGenerator:
    """
    Generates all legal actions for a player in the current game state.
    This is used for:
    - Human player action selection
    - AI decision making
    - Validation and verification
    """
    
    def __init__(self, movement_system: MovementSystem, 
                 combat_system: CombatSystem,
                 ability_system: AbilitySystem):
        self.movement_system = movement_system
        self.combat_system = combat_system
        self.ability_system = ability_system

    def _get_unit_max_range(self, unit, target_type: str) -> int:
        """
        Calculate maximum range a unit can attack based on its attack values.
        
        Args:
            unit: The attacking unit
            target_type: 'Soldier' or 'Vehicle'
        
        Returns:
            Maximum range in hexes (0-1 for short, 2-4 for medium, 5-8 for long)
        """
        # Determine which attack values to check based on target type
        if target_type == 'Soldier':
            short_val = getattr(unit, 'per_short', 0)
            medium_val = getattr(unit, 'per_medium', 0)
            long_val = getattr(unit, 'per_long', 0)
        else:  # Vehicle
            short_val = getattr(unit, 'veh_short', 0)
            medium_val = getattr(unit, 'veh_medium', 0)
            long_val = getattr(unit, 'veh_long', 0)
        
        # Determine max range based on non-zero attack values
        # Long range = 5-8 hexes
        if long_val > 0:
            return 8
        # Medium range = 2-4 hexes  
        elif medium_val > 0:
            return 4
        # Short range = 0-1 hexes
        elif short_val > 0:
            return 1
        # Can't attack this target type
        else:
            return 0
    
    def get_all_legal_actions(self, game_state: GameState, player: str) -> List[Action]:
        """
        Get ALL legal actions available to a player in the current game state.
        This is the master function for generating the complete action space.
        """
        actions = []
        
        # Get all units owned by this player
        player_units = game_state.get_units_by_owner(player)
        
        # Generate actions based on current phase
        if game_state.current_phase == GamePhase.MOVEMENT:
            actions.extend(self._get_movement_phase_actions(game_state, player_units))
        
        elif game_state.current_phase == GamePhase.ASSAULT:
            actions.extend(self._get_assault_phase_actions(game_state, player_units))
        
        # Always allow ending the phase
        actions.append(EndPhaseAction(game_state.current_phase))
        
        # Always allow passing
        actions.append(PassAction(player))
        
        return actions
    
    def _get_movement_phase_actions(self, game_state: GameState, 
                                    player_units: List[UnitState]) -> List[Action]:
        """Generate all legal actions during the movement phase"""
        actions = []
        
        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            
            unit = unit_state.unit
            q, r = unit_state.position
            
            # Skip if already moved
            if unit_state.has_moved:
                continue
            
            # Get all reachable hexes
            reachable = self.movement_system.get_reachable_hexes(
                game_state.board, q, r, unit
            )
            
            # Create a move action for each reachable hex
            for (dest_q, dest_r) in reachable:
                # Skip current position
                if (dest_q, dest_r) == (q, r):
                    continue
                
                # Create move action
                move_action = MoveAction(
                    unit_id=unit.id,
                    from_q=q,
                    from_r=r,
                    to_q=dest_q,
                    to_r=dest_r,
                    path=[(q, r), (dest_q, dest_r)],  # Simplified path
                    movement_cost=0  # Would calculate actual cost
                )
                
                actions.append(move_action)
        
        return actions
    
    def _get_assault_phase_actions(self, game_state: GameState,
                                   player_units: List[UnitState]) -> List[Action]:
        """Generate all legal actions during the assault phase"""
        actions = []
        
        # Get enemy units
        enemy_owner = "player2" if game_state.active_player == "player1" else "player1"
        enemy_units = game_state.get_units_by_owner(enemy_owner)
        
        for unit_state in player_units:
            if not unit_state.is_alive:
                continue
            
            unit = unit_state.unit
            q, r = unit_state.position
            
            # Generate attack actions
            if not unit_state.has_attacked:
                actions.extend(self._get_attack_actions(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))
            
            # Generate move-and-attack actions (for units with special abilities)
            if self.movement_system.can_unit_move_and_attack(unit):
                actions.extend(self._get_move_and_attack_actions(
                    game_state, unit, unit_state, (q, r), enemy_units
                ))
            
            # Generate ability actions
            actions.extend(self._get_ability_actions(
                game_state, unit, unit_state
            ))
        
        return actions
    
    def _get_attack_actions(self, game_state: GameState, unit, unit_state: UnitState,
                           position: Tuple[int, int], 
                           enemy_units: List[UnitState]) -> List[AttackAction]:
        """Generate all legal attack actions for a unit"""
        actions = []
        q, r = position
        
        for enemy_state in enemy_units:
            if not enemy_state.is_alive:
                continue
            
            enemy = enemy_state.unit
            enemy_q, enemy_r = enemy_state.position
            
            # Calculate distance
            distance = game_state.board.hex_distance(q, r, enemy_q, enemy_r)
            
            # Check if in range
            max_range = self._get_unit_max_range(unit, enemy.unit_type)
            if distance > max_range or max_range == 0:
                continue
            
            # Check line of sight
            has_los, blocking = self.movement_system.has_line_of_sight(
                game_state.board, unit, q, r, enemy_q, enemy_r
            )
            
            if not has_los:
                continue
            
            # Get range category
            range_cat = self.movement_system.get_range_category(distance)
            
            # Create attack action
            attack_action = AttackAction(
                unit_id=unit.id,
                attacker_q=q,
                attacker_r=r,
                target_id=enemy.id,
                target_q=enemy_q,
                target_r=enemy_r,
                range_category=range_cat,
                distance=distance,
                has_los=has_los
            )
            
            actions.append(attack_action)
        
        return actions
    
    def _get_move_and_attack_actions(self, game_state: GameState, unit, 
                                     unit_state: UnitState,
                                     current_pos: Tuple[int, int],
                                     enemy_units: List[UnitState]) -> List[MoveAndAttackAction]:
        """
        Generate all legal move-and-attack actions.
        For units with abilities like "All Guns Blazing" or "Aggression".
        """
        actions = []
        q, r = current_pos
        
        # Get assault movement range (usually smaller than full movement)
        assault_range = self.movement_system.get_assault_move_range(unit)
        
        if assault_range == 0:
            return actions
        
        # Get reachable positions for assault move
        reachable = self.movement_system.get_reachable_hexes(
            game_state.board, q, r, unit, max_speed=assault_range
        )
        
        # For each reachable position, check what we can attack from there
        for (move_q, move_r) in reachable:
            if (move_q, move_r) == (q, r):
                continue  # Skip staying in place
            
            # Create move action
            move_action = MoveAction(
                unit_id=unit.id,
                from_q=q,
                from_r=r,
                to_q=move_q,
                to_r=move_r
            )
            
            # Check what we can attack from this new position
            for enemy_state in enemy_units:
                if not enemy_state.is_alive:
                    continue
                
                enemy = enemy_state.unit
                enemy_q, enemy_r = enemy_state.position
                
                # Calculate distance from new position
                distance = game_state.board.hex_distance(move_q, move_r, enemy_q, enemy_r)
                
                max_range = self._get_unit_max_range(unit, enemy.unit_type)
                if distance > max_range or max_range == 0:
                    continue
                
                # Check LOS from new position
                has_los, _ = self.movement_system.has_line_of_sight(
                    game_state.board, unit, move_q, move_r, enemy_q, enemy_r
                )
                
                if not has_los:
                    continue
                
                range_cat = self.movement_system.get_range_category(distance)
                
                # Create attack action from new position
                attack_action = AttackAction(
                    unit_id=unit.id,
                    attacker_q=move_q,
                    attacker_r=move_r,
                    target_id=enemy.id,
                    target_q=enemy_q,
                    target_r=enemy_r,
                    range_category=range_cat,
                    distance=distance,
                    has_los=has_los
                )
                
                # Create combined move-and-attack action
                combined_action = MoveAndAttackAction(
                    unit_id=unit.id,
                    move_action=move_action,
                    attack_action=attack_action
                )
                
                actions.append(combined_action)
        
        return actions
    
    def _get_ability_actions(self, game_state: GameState, unit, 
                            unit_state: UnitState) -> List[UseAbilityAction]:
        """Generate all legal ability use actions for a unit"""
        actions = []
        
        if not unit.abilities:
            return actions
        
        for ability_name in unit.abilities:
            # Skip if already used this turn
            if ability_name in unit_state.abilities_used:
                continue
            
            # Create ability action
            # Note: This is simplified - real implementation would need
            # to determine valid targets and parameters for each ability
            ability_action = UseAbilityAction(
                unit_id=unit.id,
                ability_name=ability_name
            )
            
            actions.append(ability_action)
        
        return actions
    
    def get_legal_moves(self, game_state: GameState, unit_id: str) -> List[MoveAction]:
        """Get all legal moves for a specific unit"""
        unit_state = game_state.get_unit_state(unit_id)
        if not unit_state or not unit_state.is_alive or unit_state.has_moved:
            return []
        
        unit = unit_state.unit
        q, r = unit_state.position
        
        reachable = self.movement_system.get_reachable_hexes(
            game_state.board, q, r, unit
        )
        
        moves = []
        for (dest_q, dest_r) in reachable:
            if (dest_q, dest_r) == (q, r):
                continue
            
            move = MoveAction(
                unit_id=unit.id,
                from_q=q,
                from_r=r,
                to_q=dest_q,
                to_r=dest_r
            )
            moves.append(move)
        
        return moves
    
    def get_legal_attacks(self, game_state: GameState, unit_id: str) -> List[AttackAction]:
        """Get all legal attacks for a specific unit"""
        unit_state = game_state.get_unit_state(unit_id)
        if not unit_state or not unit_state.is_alive or unit_state.has_attacked:
            return []
        
        unit = unit_state.unit
        owner = unit_state.owner
        enemy_owner = "player2" if owner == "player1" else "player1"
        enemy_units = game_state.get_units_by_owner(enemy_owner)
        
        return self._get_attack_actions(
            game_state, unit, unit_state, unit_state.position, enemy_units
        )
    
    def count_legal_actions(self, game_state: GameState, player: str) -> int:
        """Count total number of legal actions (useful for complexity analysis)"""
        return len(self.get_all_legal_actions(game_state, player))
    
    def get_action_summary(self, game_state: GameState, player: str) -> str:
        """Get a human-readable summary of available actions"""
        actions = self.get_all_legal_actions(game_state, player)
        
        move_count = sum(1 for a in actions if isinstance(a, MoveAction))
        attack_count = sum(1 for a in actions if isinstance(a, AttackAction))
        move_attack_count = sum(1 for a in actions if isinstance(a, MoveAndAttackAction))
        ability_count = sum(1 for a in actions if isinstance(a, UseAbilityAction))
        
        summary = f"Available Actions for {player}:\n"
        summary += f"  Movement: {move_count} options\n"
        summary += f"  Attacks: {attack_count} options\n"
        summary += f"  Move+Attack: {move_attack_count} options\n"
        summary += f"  Abilities: {ability_count} options\n"
        summary += f"  Total: {len(actions)} actions\n"
        
        return summary


# Test/Demo function
def demo_action_generation():
    """Demonstrate action generation"""
    from game_state import create_test_game_state
    from abilities import AbilitySystem
    
    # Create test game
    game_state = create_test_game_state()
    
    # Create systems
    ability_system = AbilitySystem('Axis and Allies Unit Data for Analysis - Special_Abilities.csv')
    movement_system = MovementSystem(ability_system)
    combat_system = CombatSystem(ability_system)
    
    # Create action generator
    action_gen = ActionGenerator(movement_system, combat_system, ability_system)
    
    print("=== ACTION GENERATION DEMO ===\n")
    print(game_state)
    print()
    
    # Get all legal actions for player 1
    print(action_gen.get_action_summary(game_state, "player1"))
    print()
    
    # Show first few actions in detail
    actions = action_gen.get_all_legal_actions(game_state, "player1")
    print("Sample actions:")
    for action in actions[:10]:
        print(f"  {action}")
    
    print(f"\n... and {len(actions) - 10} more actions")


if __name__ == "__main__":
    demo_action_generation()