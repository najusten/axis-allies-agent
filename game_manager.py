import random
from typing import List, Dict, Tuple, Optional
from board import Board
from units import Unit
from combat import CombatSystem, UnitState
from movement import MovementSystem
from abilities import AbilitySystem

class GameState:
    """Tracks the complete state of the game"""
    
    def __init__(self, board: Board, player1_units: List[UnitState], 
                 player2_units: List[UnitState], objective_hex: Tuple[int, int]):
        self.board = board
        self.player1_units = player1_units
        self.player2_units = player2_units
        self.objective_hex = objective_hex
        self.turn_number = 0
        self.first_player = None  # Determined by initiative each turn
        self.second_player = None
        
        # Track unit actions this turn
        self.units_that_moved = set()
        self.units_that_attacked = set()
    
    def reset_turn_actions(self):
        """Clear action tracking at start of turn"""
        self.units_that_moved.clear()
        self.units_that_attacked.clear()
    
    def get_active_units(self, player: int) -> List[UnitState]:
        """Get all non-destroyed units for a player"""
        units = self.player1_units if player == 1 else self.player2_units
        return [u for u in units if not u.destroyed]
    
    def check_objective_control(self) -> Optional[int]:
        """
        Check who controls the objective.
        Returns player number (1 or 2) if controlled, None if contested or uncontrolled.
        A player controls if they have units on OR adjacent to objective and opponent doesn't.
        """
        obj_q, obj_r = self.objective_hex
        
        # Get all hexes on or adjacent to objective
        control_hexes = [(obj_q, obj_r)]
        neighbors = self.board.get_neighbors(obj_q, obj_r)
        control_hexes.extend([(n.q, n.r) for n in neighbors])
        
        player1_present = False
        player2_present = False
        
        # Check which players have units in control zone
        for q, r in control_hexes:
            hex_tile = self.board.get_hex(q, r)
            if hex_tile and hex_tile.unit:
                # Find which player owns this unit
                if any(u.unit == hex_tile.unit for u in self.get_active_units(1)):
                    player1_present = True
                elif any(u.unit == hex_tile.unit for u in self.get_active_units(2)):
                    player2_present = True
        
        # Return controller or None if contested
        if player1_present and not player2_present:
            return 1
        elif player2_present and not player1_present:
            return 2
        else:
            return None  # Contested or uncontrolled
    
    def count_army_points(self, player: int) -> int:
        """Count total point value of surviving units"""
        units = self.get_active_units(player)
        return sum(u.unit.cost for u in units)


class GameManager:
    """Manages the complete game flow and turn structure"""
    
    def __init__(self, ability_system: AbilitySystem, 
                 victory_condition: str = 'objective'):
        """
        Initialize game manager.
        
        victory_condition options:
        - 'objective': Control objective at turn 7+ (game continues until uncontested)
        - 'annihilation': Destroy all enemy units
        """
        self.ability_system = ability_system
        self.combat_system = CombatSystem(ability_system)
        self.movement_system = MovementSystem(ability_system)
        self.victory_condition = victory_condition
        self.game_state = None
    
    def setup_game(self, board: Board, 
                   player1_units: List[Tuple[Unit, int, int]],
                   player2_units: List[Tuple[Unit, int, int]],
                   objective_hex: Tuple[int, int]) -> GameState:
        """
        Set up a new game.
        
        Args:
            board: The game board
            player1_units: List of (unit, q, r) tuples for player 1
            player2_units: List of (unit, q, r) tuples for player 2
            objective_hex: (q, r) coordinates of objective
        """
        # Create unit states and place on board
        p1_states = []
        for unit, q, r in player1_units:
            state = UnitState(unit)
            board.place_unit(unit, q, r)
            p1_states.append(state)
        
        p2_states = []
        for unit, q, r in player2_units:
            state = UnitState(unit)
            board.place_unit(unit, q, r)
            p2_states.append(state)
        
        self.game_state = GameState(board, p1_states, p2_states, objective_hex)
        
        print(f"🎮 Game Setup Complete!")
        print(f"   Victory Condition: {self.victory_condition}")
        print(f"   Objective: {objective_hex}")
        if self.victory_condition == 'objective':
            print(f"   (Game continues until objective is uncontested at turn 7+)")
        print(f"   Player 1: {len(p1_states)} units ({sum(u.unit.cost for u in p1_states):.0f} points)")
        print(f"   Player 2: {len(p2_states)} units ({sum(u.unit.cost for u in p2_states):.0f} points)")
        
        return self.game_state
    
    def roll_initiative(self, player1_bonus: int = 0, player2_bonus: int = 0) -> Tuple[int, int, int, bool]:
        """
        Roll initiative for both players.
        Returns (player1_total, player2_total, winner, winner_chose_first)
        
        Winner gets to CHOOSE whether to go first or second.
        """
        # Roll single d6 for each player
        p1_roll = random.randint(1, 6) + player1_bonus
        p2_roll = random.randint(1, 6) + player2_bonus
        
        # Determine winner
        if p1_roll > p2_roll:
            winner = 1
            # AI decision: should this player go first?
            chose_first = self._should_go_first(1)
        elif p2_roll > p1_roll:
            winner = 2
            chose_first = self._should_go_first(2)
        else:
            # Tie - reroll
            return self.roll_initiative(player1_bonus, player2_bonus)
        
        return p1_roll, p2_roll, winner, chose_first
    
    def _should_go_first(self, player: int) -> bool:
        """
        AI heuristic: decide if player should go first.
        
        Considers:
        - Strike and Fade units (benefit from attacking first then moving)
        - Assault movement abilities (benefit from positioning first)
        - Default: slightly prefer first (60% chance)
        """
        units = self.game_state.get_active_units(player)
        
        # Count factors favoring going first
        favor_first = 0
        
        # Check for Strike and Fade or assault movement
        for unit_state in units:
            movement_mods = self.ability_system.get_movement_modifiers(unit_state.unit)
            
            # Check for Strike and Fade or assault abilities
            for note in movement_mods.get('notes', []):
                if 'strike and fade' in note.lower():
                    favor_first += 2  # Strong preference
                elif 'assault' in note.lower() and 'move' in note.lower():
                    favor_first += 1  # Moderate preference
        
        # Weighted random decision
        # More "favor_first" points = higher chance of going first
        threshold = 0.6 + (favor_first * 0.1)  # 60% base, +10% per factor
        return random.random() < min(threshold, 0.95)  # Cap at 95%
    
    def get_initiative_bonuses(self) -> Tuple[int, int]:
        """
        Get initiative bonuses from commander abilities.
        Bonuses DO NOT stack - use highest bonus only.
        If commander is destroyed, bonus is lost.
        If both sides have same bonus, they cancel out.
        """
        p1_bonus = 0
        p2_bonus = 0
        
        # Get highest bonus for each player (bonuses don't stack)
        for unit_state in self.game_state.get_active_units(1):
            if not unit_state.destroyed and not unit_state.pending_destruction:
                bonus = self.ability_system.get_initiative_modifiers(unit_state.unit)
                p1_bonus = max(p1_bonus, bonus)
        
        for unit_state in self.game_state.get_active_units(2):
            if not unit_state.destroyed and not unit_state.pending_destruction:
                bonus = self.ability_system.get_initiative_modifiers(unit_state.unit)
                p2_bonus = max(p2_bonus, bonus)
        
        # If both have same bonus, they cancel out
        if p1_bonus == p2_bonus:
            return 0, 0
        
        return p1_bonus, p2_bonus
    
    def movement_phase(self, player: int):
        """Execute movement phase for a player"""
        print(f"\n{'='*70}")
        print(f"🏃 Player {player} Movement Phase")
        print(f"{'='*70}")
        
        active_units = self.game_state.get_active_units(player)
        
        # In a real game, this would be interactive
        # For now, just report that units can move
        print(f"Player {player} has {len(active_units)} units that can move")
        print("(Movement would be selected here in interactive mode)")
        
        # Mark all units as having moved (simplified)
        for unit in active_units:
            self.game_state.units_that_moved.add(id(unit))
    
    def assault_phase(self, player: int):
        """
        Execute assault phase for a player.
        Each unit can either attack OR move (if didn't move in movement phase).
        """
        print(f"\n{'='*70}")
        print(f"⚔️  Player {player} Assault Phase")
        print(f"{'='*70}")
        
        active_units = self.game_state.get_active_units(player)
        
        print(f"Player {player} has {len(active_units)} units")
        print("Each unit can:")
        print("  - Attack (if in range of enemies)")
        print("  - Move (if didn't move in movement phase)")
        print("(Actions would be selected here in interactive mode)")
        
        # Mark units as having attacked (simplified)
        for unit in active_units:
            self.game_state.units_that_attacked.add(id(unit))
    
    def apply_casualties_phase(self):
        """Apply all pending casualties at end of turn"""
        print(f"\n{'='*70}")
        print(f"💀 Applying Casualties")
        print(f"{'='*70}")
        
        casualties_applied = False
        
        for unit_state in self.game_state.player1_units + self.game_state.player2_units:
            if unit_state.pending_destruction and not unit_state.destroyed:
                unit_state.apply_end_of_turn_casualties()
                print(f"   💥 {unit_state.unit.name} destroyed")
                casualties_applied = True
            
            # Reset turn flags
            unit_state.reset_turn_flags()
        
        if not casualties_applied:
            print("   No casualties this turn")
    
    def check_victory(self) -> Optional[Tuple[int, str]]:
        """
        Check victory conditions.
        Returns (winner, reason) or None if game continues.
        """
        # Check annihilation
        p1_active = len(self.game_state.get_active_units(1))
        p2_active = len(self.game_state.get_active_units(2))
        
        if p1_active == 0 and p2_active == 0:
            return (None, "Draw - mutual annihilation")
        elif p1_active == 0:
            return (2, "Player 2 wins - Player 1 annihilated")
        elif p2_active == 0:
            return (1, "Player 1 wins - Player 2 annihilated")
        
        # Check objective control (turn 7+ only)
        # Game continues indefinitely until ONE player controls (uncontested)
        if self.victory_condition == 'objective' and self.game_state.turn_number >= 7:
            controller = self.game_state.check_objective_control()
            if controller:
                return (controller, f"Player {controller} wins - controls objective (Turn {self.game_state.turn_number})")
            # If contested, game continues (no turn limit!)
        
        return None
    
    def play_turn(self):
        """Execute a complete turn"""
        self.game_state.turn_number += 1
        self.game_state.reset_turn_actions()
        
        print(f"\n{'#'*70}")
        print(f"# TURN {self.game_state.turn_number}")
        print(f"{'#'*70}")
        
        # 1. Initiative Phase
        print(f"\n🎲 Initiative Phase")
        p1_bonus, p2_bonus = self.get_initiative_bonuses()
        
        if p1_bonus > 0 or p2_bonus > 0:
            print(f"   Commander bonuses: Player 1 +{p1_bonus}, Player 2 +{p2_bonus}")
        
        p1_init, p2_init, winner, chose_first = self.roll_initiative(p1_bonus, p2_bonus)
        
        p1_base = p1_init - p1_bonus
        p2_base = p2_init - p2_bonus
        
        print(f"   Player 1: {p1_init} (rolled {p1_base}" + (f", +{p1_bonus} bonus)" if p1_bonus else ")"))
        print(f"   Player 2: {p2_init} (rolled {p2_base}" + (f", +{p2_bonus} bonus)" if p2_bonus else ")"))
        print(f"   Player {winner} wins initiative!")
        
        if chose_first:
            print(f"   Player {winner} chooses to go FIRST")
            first_player = winner
        else:
            print(f"   Player {winner} chooses to go SECOND")
            first_player = 2 if winner == 1 else 1
        
        self.game_state.first_player = first_player
        self.game_state.second_player = 2 if first_player == 1 else 1
        
        # 2. Movement Phases
        self.movement_phase(self.game_state.first_player)
        self.movement_phase(self.game_state.second_player)
        
        # 3. Assault Phases (attacks are simultaneous within each player's phase)
        self.assault_phase(self.game_state.first_player)
        self.assault_phase(self.game_state.second_player)
        
        # 4. Apply Casualties
        self.apply_casualties_phase()
        
        # 5. Check Victory
        result = self.check_victory()
        if result:
            winner, reason = result
            print(f"\n{'='*70}")
            print(f"🏆 GAME OVER!")
            print(f"{'='*70}")
            print(f"   {reason}")
            return result
        
        # Report objective status
        controller = self.game_state.check_objective_control()
        if controller:
            print(f"\n📍 Objective Status: Player {controller} controls")
        else:
            print(f"\n📍 Objective Status: Contested")
        
        # Report army status
        p1_units = len(self.game_state.get_active_units(1))
        p2_units = len(self.game_state.get_active_units(2))
        print(f"   Player 1: {p1_units} units remaining")
        print(f"   Player 2: {p2_units} units remaining")
        
        return None
    
    def play_game(self, max_turns: int = None):
        """Play a complete game to conclusion"""
        if not self.game_state:
            raise ValueError("Game not set up. Call setup_game() first.")
        
        # Play indefinitely if no max_turns specified (for objective mode)
        turn_count = 0
        while True:
            result = self.play_turn()
            if result:
                return result
            
            turn_count += 1
            if max_turns and turn_count >= max_turns:
                print(f"\n⏰ Max turns ({max_turns}) reached for testing!")
                return self.check_victory()


# Test the game manager
if __name__ == "__main__":
    from units import load_units
    from board import Board
    
    print("=== GAME MANAGER TEST ===\n")
    
    # Load systems
    ability_system = AbilitySystem('Axis and Allies Unit Data for Analysis - Special_Abilities.csv')
    game_manager = GameManager(ability_system, victory_condition='objective')
    
    # Load units
    all_units = load_units()
    combat_units = [u for u in all_units if not ability_system.is_obstacle_unit(u)]
    
    # Find units for both sides - COMPLETE LISTS
    axis_nations = ['Germany', 'Japan', 'Italy', 'Romania', 'Hungary', 'Finland', 
                    'Bulgaria', 'Slovakia', 'Croatia']
    allied_nations = ['USA', 'UK', 'Soviet Union', 'France', 'Poland', 'China', 
                    'Australia', 'Canada', 'New Zealand', 'Greece', 'Belgium', 
                    'South Africa', 'Yugoslavia']

    axis_units = [u for u in combat_units if u.nation in axis_nations and u.unit_type == 'Soldier'][:5]
    allied_units = [u for u in combat_units if u.nation in allied_nations and u.unit_type == 'Soldier'][:5]
    
    if not axis_units or not allied_units:
        print("❌ Could not find enough units for testing")
        exit(1)
    
    # Create board
    board = Board(width=15, height=15)
    
    # Set objective in center
    objective = (7, 7)
    board.set_terrain(*objective, 'building')
    
    # Deploy units
    player1_deployment = [(unit, 2 + i, 2) for i, unit in enumerate(axis_units)]
    player2_deployment = [(unit, 12 - i, 12) for i, unit in enumerate(allied_units)]
    
    # Setup game
    game_state = game_manager.setup_game(
        board,
        player1_deployment,
        player2_deployment,
        objective
    )
    
    print(f"\n{'='*70}")
    print("Starting simplified game simulation...")
    print("(Movement and combat actions are placeholders for now)")
    print(f"{'='*70}")
    
    # Play 3 turns as demonstration (limit for testing)
    for turn in range(3):
        result = game_manager.play_turn()
        if result:
            break
    
    print(f"\n✅ Game Manager test complete!")
    print(f"\nImplemented features:")
    print("  ✓ Game setup and deployment")
    print("  ✓ Turn structure (Initiative → Movement → Assault → Casualties)")
    print("  ✓ Initiative: 1d6 + highest commander bonus (bonuses don't stack, cancel if equal)")
    print("  ✓ Winner chooses first or second (AI considers Strike and Fade, assault abilities)")
    print("  ✓ Action tracking (moved/attacked)")
    print("  ✓ Victory: objective control at turn 7+ (no turn limit, continues until uncontested)")
    print("  ✓ Victory: annihilation")
    print(f"\nNext steps:")
    print("  • Add interactive movement selection")
    print("  • Add interactive attack selection")
    print("  • Integrate actual combat resolution")
    print("  • Add defensive fire")
    print("  • Add unit facing/orientation")