"""
High-Level Game Visualization API for Axis & Allies Miniatures

Provides a simple interface for generating and viewing game visualizations.

Usage:
    from game_visualizer import GameVisualizer
    from game_state import GameState

    visualizer = GameVisualizer()
    visualizer.open_in_browser(game_state)  # Opens game in browser
"""

import os
import webbrowser
import tempfile
import json
from typing import Set, Tuple, Optional, List, Dict
from pathlib import Path

from game_state import GameState, UnitState
from board import Board
from visualization import HTMLGenerator

# Try to import movement system for pre-computing valid moves
try:
    from movement import MovementSystem
    from abilities import AbilitySystem
    MOVEMENT_AVAILABLE = True
except ImportError:
    MOVEMENT_AVAILABLE = False


class GameVisualizer:
    """
    High-level API for generating game visualizations.

    Provides methods for:
    - Generating HTML visualization strings
    - Saving visualizations to files
    - Opening visualizations in browser
    - Integration with game runner for live play
    """

    def __init__(self, hex_size: float = 40):
        """
        Initialize the visualizer.

        Args:
            hex_size: Size of hexes in pixels (distance from center to vertex)
        """
        self.html_generator = HTMLGenerator()
        self.hex_size = hex_size
        self._temp_files: List[str] = []

    def generate_html(self, game_state: GameState,
                      output_path: Optional[str] = None,
                      valid_moves: Set[Tuple[int, int]] = None,
                      valid_attacks: Set[Tuple[int, int]] = None,
                      selected_unit_id: str = None) -> str:
        """
        Generate HTML visualization for the current game state.

        Args:
            game_state: The current game state to visualize
            output_path: Optional file path to save the HTML to
            valid_moves: Set of (q, r) positions that are valid move destinations
            valid_attacks: Set of (q, r) positions that are valid attack targets
            selected_unit_id: ID of the currently selected unit

        Returns:
            The generated HTML string
        """
        html = self.html_generator.generate_html(
            game_state,
            valid_moves=valid_moves,
            valid_attacks=valid_attacks,
            selected_unit_id=selected_unit_id
        )

        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html)

        return html

    def open_in_browser(self, game_state: GameState,
                        valid_moves: Set[Tuple[int, int]] = None,
                        valid_attacks: Set[Tuple[int, int]] = None,
                        selected_unit_id: str = None,
                        output_path: Optional[str] = None) -> str:
        """
        Generate visualization and open it in the default web browser.

        Args:
            game_state: The current game state to visualize
            valid_moves: Set of (q, r) positions that are valid move destinations
            valid_attacks: Set of (q, r) positions that are valid attack targets
            selected_unit_id: ID of the currently selected unit
            output_path: Optional file path to save the HTML to (uses temp file if not specified)

        Returns:
            The path to the HTML file
        """
        # Generate HTML
        html = self.generate_html(
            game_state,
            valid_moves=valid_moves,
            valid_attacks=valid_attacks,
            selected_unit_id=selected_unit_id
        )

        # Determine output path
        if output_path is None:
            # Create temp file
            fd, output_path = tempfile.mkstemp(suffix='.html', prefix='axis_allies_')
            os.close(fd)
            self._temp_files.append(output_path)

        # Write to file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        # Open in browser
        webbrowser.open(f'file://{os.path.abspath(output_path)}')

        return output_path

    def save_to_file(self, game_state: GameState, output_path: str,
                     valid_moves: Set[Tuple[int, int]] = None,
                     valid_attacks: Set[Tuple[int, int]] = None,
                     selected_unit_id: str = None) -> str:
        """
        Generate visualization and save to a file.

        Args:
            game_state: The current game state to visualize
            output_path: File path to save the HTML to
            valid_moves: Set of (q, r) positions that are valid move destinations
            valid_attacks: Set of (q, r) positions that are valid attack targets
            selected_unit_id: ID of the currently selected unit

        Returns:
            The path to the HTML file
        """
        self.generate_html(
            game_state,
            output_path=output_path,
            valid_moves=valid_moves,
            valid_attacks=valid_attacks,
            selected_unit_id=selected_unit_id
        )
        return output_path

    def cleanup_temp_files(self):
        """Remove any temporary files created by this visualizer."""
        for path in self._temp_files:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        self._temp_files.clear()

    def __del__(self):
        """Cleanup temp files on destruction."""
        self.cleanup_temp_files()


class LiveGameVisualizer:
    """
    Visualization for live/interactive play.

    Provides methods for updating the visualization during gameplay,
    with support for showing valid moves/attacks based on game rules.
    """

    def __init__(self, game_state: GameState, output_path: str = "game_view.html"):
        """
        Initialize live visualizer.

        Args:
            game_state: The game state to visualize
            output_path: Path to save the HTML file (will be updated in place)
        """
        self.game_state = game_state
        self.output_path = output_path
        self.visualizer = GameVisualizer()
        self.selected_unit_id: Optional[str] = None
        self._movement_system = None
        self._combat_system = None

    def set_movement_system(self, movement_system):
        """Set the movement system for calculating valid moves."""
        self._movement_system = movement_system

    def set_combat_system(self, combat_system):
        """Set the combat system for calculating valid attacks."""
        self._combat_system = combat_system

    def update(self, open_browser: bool = False):
        """
        Update the visualization file with current game state.

        Args:
            open_browser: If True, also open/refresh in browser
        """
        valid_moves = None
        valid_attacks = None

        # Calculate valid actions for selected unit
        if self.selected_unit_id:
            valid_moves = self._get_valid_moves(self.selected_unit_id)
            valid_attacks = self._get_valid_attacks(self.selected_unit_id)

        # Generate and save
        self.visualizer.generate_html(
            self.game_state,
            output_path=self.output_path,
            valid_moves=valid_moves,
            valid_attacks=valid_attacks,
            selected_unit_id=self.selected_unit_id
        )

        if open_browser:
            webbrowser.open(f'file://{os.path.abspath(self.output_path)}')

    def select_unit(self, unit_id: str):
        """Select a unit and update visualization with valid actions."""
        self.selected_unit_id = unit_id
        self.update()

    def deselect_unit(self):
        """Deselect the current unit."""
        self.selected_unit_id = None
        self.update()

    def _get_valid_moves(self, unit_id: str) -> Set[Tuple[int, int]]:
        """Get valid move destinations for a unit."""
        if not self._movement_system:
            return set()

        unit_state = self.game_state.get_unit_state(unit_id)
        if not unit_state or unit_state.has_moved:
            return set()

        # Use movement system to get reachable hexes
        q, r = unit_state.position
        return self._movement_system.get_reachable_hexes(
            self.game_state.board, q, r, unit_state.unit
        )

    def _get_valid_attacks(self, unit_id: str) -> Set[Tuple[int, int]]:
        """Get valid attack targets for a unit."""
        unit_state = self.game_state.get_unit_state(unit_id)
        if not unit_state or unit_state.has_attacked:
            return set()

        # Get all enemy units in range
        valid_targets = set()
        q, r = unit_state.position
        unit = unit_state.unit

        # Get max attack range
        max_range = 0
        if hasattr(unit, 'veh_long') and unit.veh_long > 0:
            max_range = 8  # Long range
        elif hasattr(unit, 'veh_medium') and unit.veh_medium > 0:
            max_range = 4  # Medium range
        elif hasattr(unit, 'veh_short') and unit.veh_short > 0:
            max_range = 1  # Short range

        if hasattr(unit, 'per_long') and unit.per_long > 0:
            max_range = max(max_range, 8)
        elif hasattr(unit, 'per_medium') and unit.per_medium > 0:
            max_range = max(max_range, 4)
        elif hasattr(unit, 'per_short') and unit.per_short > 0:
            max_range = max(max_range, 1)

        # Find enemies in range
        enemy_owner = 'player2' if unit_state.owner == 'player1' else 'player1'
        for enemy_state in self.game_state.get_units_by_owner(enemy_owner):
            if enemy_state.is_alive:
                eq, er = enemy_state.position
                distance = self.game_state.board.hex_distance(q, r, eq, er)
                if 0 < distance <= max_range:
                    valid_targets.add((eq, er))

        return valid_targets


class InteractiveGameVisualizer:
    """
    Creates fully interactive HTML with pre-computed valid moves and attacks.

    All valid actions are computed server-side and embedded in the HTML,
    allowing the JavaScript to show valid moves/attacks without any server
    communication.
    """

    def __init__(self):
        """Initialize with movement system if available."""
        self._movement_system = None
        self._ability_system = None

        if MOVEMENT_AVAILABLE:
            try:
                self._ability_system = AbilitySystem()
                self._movement_system = MovementSystem(self._ability_system)
            except Exception:
                pass  # Fall back to basic range calculation

    def generate_interactive_html(self, game_state: GameState,
                                   output_path: Optional[str] = None) -> str:
        """
        Generate fully interactive HTML with pre-computed actions.

        Args:
            game_state: The game state to visualize
            output_path: Optional path to save the HTML

        Returns:
            The generated HTML string
        """
        # Pre-compute valid moves and attacks for all units
        unit_actions = self._compute_all_unit_actions(game_state)

        # Generate base HTML
        html_generator = HTMLGenerator()
        base_html = html_generator.generate_html(game_state)

        # Inject the pre-computed action data into the JavaScript
        action_data_script = f'''
        <script>
        // Pre-computed valid moves and attacks for each unit
        const UNIT_ACTIONS = {json.dumps(unit_actions)};
        </script>
        '''

        # Insert before the closing </head> tag
        html = base_html.replace('</head>', f'{action_data_script}\n</head>')

        # Replace the JavaScript with enhanced interactive version
        html = self._inject_interactive_js(html)

        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html)

        return html

    def _compute_all_unit_actions(self, game_state: GameState) -> Dict:
        """Compute valid moves and attacks for all units."""
        unit_actions = {}

        for unit_id, unit_state in game_state.units.items():
            if not unit_state.is_alive:
                continue

            actions = {
                'valid_moves': [],
                'valid_attacks': [],
                'can_move': not unit_state.has_moved,
                'can_attack': not unit_state.has_attacked
            }

            # Compute valid moves
            if not unit_state.has_moved:
                moves = self._get_valid_moves(game_state, unit_state)
                actions['valid_moves'] = [[q, r] for q, r in moves]

            # Compute valid attacks
            if not unit_state.has_attacked:
                attacks = self._get_valid_attacks(game_state, unit_state)
                actions['valid_attacks'] = [[q, r] for q, r in attacks]

            unit_actions[unit_id] = actions

        return unit_actions

    def _get_valid_moves(self, game_state: GameState,
                         unit_state: UnitState) -> Set[Tuple[int, int]]:
        """Get valid move destinations for a unit."""
        if self._movement_system:
            q, r = unit_state.position
            try:
                return self._movement_system.get_reachable_hexes(
                    game_state.board, q, r, unit_state.unit
                )
            except Exception:
                pass

        # Fallback: simple speed-based movement (used when MovementSystem not available)
        q, r = unit_state.position
        unit = unit_state.unit
        speed = getattr(unit, 'speed', 2) or 2
        if isinstance(speed, str):
            speed = 2

        # Check for amphibious ability
        abilities = getattr(unit, 'abilities', []) or []
        is_amphibious = any('amphibious' in str(a).lower() for a in abilities)

        # Impassable terrain for most units
        impassable = {'water'} if not is_amphibious else set()

        # Difficult terrain costs 2 movement for vehicles
        difficult_terrain = {'forest', 'hill'}
        is_vehicle = unit.unit_type == 'Vehicle'

        valid_moves = set()
        for dq in range(-speed, speed + 1):
            for dr in range(-speed, speed + 1):
                nq, nr = q + dq, r + dr
                target_hex = game_state.board.get_hex(nq, nr)
                if target_hex:
                    # Check terrain passability
                    if target_hex.terrain in impassable:
                        continue

                    dist = game_state.board.hex_distance(q, r, nq, nr)

                    # Adjust for difficult terrain (simplified - doesn't account for path)
                    effective_dist = dist
                    if is_vehicle and target_hex.terrain in difficult_terrain:
                        effective_dist += 1  # Rough approximation of entry cost

                    if 0 < effective_dist <= speed:
                        # Check if hex is not occupied by another unit
                        occupied = any(
                            us.position == (nq, nr) and us.is_alive
                            for us in game_state.units.values()
                        )
                        if not occupied:
                            valid_moves.add((nq, nr))

        return valid_moves

    def _get_valid_attacks(self, game_state: GameState,
                           unit_state: UnitState) -> Set[Tuple[int, int]]:
        """Get valid attack targets for a unit."""
        valid_targets = set()
        q, r = unit_state.position
        unit = unit_state.unit

        # Get max attack range
        max_range = 0
        if getattr(unit, 'veh_long', 0) or getattr(unit, 'per_long', 0):
            max_range = 8
        elif getattr(unit, 'veh_medium', 0) or getattr(unit, 'per_medium', 0):
            max_range = 4
        elif getattr(unit, 'veh_short', 0) or getattr(unit, 'per_short', 0):
            max_range = 1

        if max_range == 0:
            return valid_targets

        # Find enemies in range
        enemy_owner = 'player2' if unit_state.owner == 'player1' else 'player1'
        for enemy_state in game_state.get_units_by_owner(enemy_owner):
            if enemy_state.is_alive:
                eq, er = enemy_state.position
                distance = game_state.board.hex_distance(q, r, eq, er)
                if 0 < distance <= max_range:
                    valid_targets.add((eq, er))

        return valid_targets

    def _inject_interactive_js(self, html: str) -> str:
        """Inject enhanced interactive JavaScript."""
        enhanced_js = '''
        // =====================================================
        // ENHANCED INTERACTIVE VISUALIZATION
        // =====================================================
        // Note: This is a READ-ONLY visualization.
        // Clicks show valid moves/attacks but don't execute them.
        // To actually play, use the game_runner.py with agents.
        // =====================================================

        // Re-initialize unit listeners with proper SVG handling
        function initEnhancedUnitListeners() {
            // Use event delegation on the SVG for better SVG element handling
            const svg = document.getElementById('game-board');
            if (!svg) return;

            svg.addEventListener('click', function(e) {
                // Find if we clicked on a unit or its children
                let target = e.target;
                let unitGroup = null;

                // Walk up the DOM to find a unit group
                while (target && target !== svg) {
                    if (target.classList && target.classList.contains('unit')) {
                        unitGroup = target;
                        break;
                    }
                    target = target.parentElement;
                }

                if (unitGroup) {
                    e.stopPropagation();
                    const unitId = unitGroup.dataset.unitId;
                    if (unitId) {
                        if (selectedUnitId === unitId) {
                            deselectUnit();
                        } else {
                            selectUnitWithActions(unitId);
                        }
                    }
                }
            });
        }

        function selectUnitWithActions(unitId) {
            // Clear previous highlights
            clearHighlights();

            selectedUnitId = unitId;

            // Update visual selection
            document.querySelectorAll('.unit').forEach(u => {
                u.classList.remove('selected');
            });

            const unitEl = document.querySelector(`.unit[data-unit-id="${unitId}"]`);
            if (unitEl) {
                unitEl.classList.add('selected');
            }

            // Update sidebar selection
            document.querySelectorAll('.unit-item').forEach(item => {
                item.classList.toggle('selected', item.dataset.unitId === unitId);
            });

            // Show valid moves and attacks from pre-computed data
            if (typeof UNIT_ACTIONS !== 'undefined' && UNIT_ACTIONS[unitId]) {
                const actions = UNIT_ACTIONS[unitId];
                showValidMoves(actions.valid_moves);
                showValidAttacks(actions.valid_attacks);
            }

            console.log(`Selected unit: ${unitId}`);
        }

        function clearHighlights() {
            // Remove existing highlight elements
            document.querySelectorAll('.dynamic-highlight').forEach(el => el.remove());
        }

        function showValidMoves(moves) {
            if (!moves || moves.length === 0) return;

            const svg = document.getElementById('game-board');
            const highlightGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            highlightGroup.classList.add('dynamic-highlight');

            moves.forEach(([q, r]) => {
                const hex = document.querySelector(`.hex[data-q="${q}"][data-r="${r}"]`);
                if (hex) {
                    const points = hex.getAttribute('points');
                    const highlight = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
                    highlight.setAttribute('points', points);
                    highlight.setAttribute('fill', 'rgba(34, 197, 94, 0.4)');
                    highlight.setAttribute('stroke', '#22c55e');
                    highlight.setAttribute('stroke-width', '3');
                    highlight.setAttribute('class', 'move-highlight dynamic-highlight');
                    highlight.setAttribute('data-q', q);
                    highlight.setAttribute('data-r', r);
                    highlight.style.cursor = 'pointer';
                    highlight.addEventListener('click', function(e) {
                        e.stopPropagation();
                        console.log(`Move to: (${q}, ${r}) - visualization only, move not executed`);
                        showActionMessage(`Move to (${q}, ${r})`, 'This is a visualization. Moves are not executed.');
                    });
                    highlightGroup.appendChild(highlight);
                }
            });

            // Insert before unit layer so units stay on top
            const unitLayer = svg.querySelector('.unit-layer');
            if (unitLayer) {
                svg.insertBefore(highlightGroup, unitLayer);
            } else {
                svg.appendChild(highlightGroup);
            }
        }

        function showValidAttacks(attacks) {
            if (!attacks || attacks.length === 0) return;

            const svg = document.getElementById('game-board');
            const highlightGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            highlightGroup.classList.add('dynamic-highlight');

            attacks.forEach(([q, r]) => {
                const hex = document.querySelector(`.hex[data-q="${q}"][data-r="${r}"]`);
                if (hex) {
                    const points = hex.getAttribute('points');
                    const highlight = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
                    highlight.setAttribute('points', points);
                    highlight.setAttribute('fill', 'rgba(239, 68, 68, 0.4)');
                    highlight.setAttribute('stroke', '#ef4444');
                    highlight.setAttribute('stroke-width', '3');
                    highlight.setAttribute('class', 'attack-highlight dynamic-highlight');
                    highlight.setAttribute('data-q', q);
                    highlight.setAttribute('data-r', r);
                    highlight.style.cursor = 'pointer';
                    highlight.addEventListener('click', function(e) {
                        e.stopPropagation();
                        console.log(`Attack at: (${q}, ${r}) - visualization only, attack not executed`);
                        showActionMessage(`Attack at (${q}, ${r})`, 'This is a visualization. Attacks are not executed.');
                    });
                    highlightGroup.appendChild(highlight);
                }
            });

            const unitLayer = svg.querySelector('.unit-layer');
            if (unitLayer) {
                svg.insertBefore(highlightGroup, unitLayer);
            } else {
                svg.appendChild(highlightGroup);
            }
        }

        function showActionMessage(title, message) {
            // Create a nicer notification instead of alert
            let notification = document.getElementById('action-notification');
            if (!notification) {
                notification = document.createElement('div');
                notification.id = 'action-notification';
                notification.style.cssText = `
                    position: fixed;
                    bottom: 20px;
                    left: 50%;
                    transform: translateX(-50%);
                    background: #16213e;
                    border: 2px solid #0f3460;
                    border-radius: 8px;
                    padding: 16px 24px;
                    color: #e8e8e8;
                    font-size: 14px;
                    z-index: 2000;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
                    opacity: 0;
                    transition: opacity 0.3s;
                `;
                document.body.appendChild(notification);
            }

            notification.innerHTML = `<strong>${title}</strong><br><span style="color:#888;font-size:12px">${message}</span>`;
            notification.style.opacity = '1';

            // Hide after 2 seconds
            setTimeout(() => {
                notification.style.opacity = '0';
            }, 2000);
        }

        function deselectUnit() {
            selectedUnitId = null;
            clearHighlights();

            document.querySelectorAll('.unit').forEach(u => {
                u.classList.remove('selected');
            });

            document.querySelectorAll('.unit-item').forEach(item => {
                item.classList.remove('selected');
            });
        }

        // Initialize enhanced listeners when DOM is ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initEnhancedUnitListeners);
        } else {
            initEnhancedUnitListeners();
        }

        // Override the original selectUnit function
        selectUnit = selectUnitWithActions;
        '''

        # Find the end of the existing JavaScript and append our enhanced code
        # Look for the closing script tag
        js_end_marker = '</script>\n</body>'
        if js_end_marker in html:
            html = html.replace(
                js_end_marker,
                f'\n{enhanced_js}\n</script>\n</body>'
            )

        return html

    def open_in_browser(self, game_state: GameState,
                        output_path: Optional[str] = None) -> str:
        """Generate and open interactive visualization in browser."""
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix='.html', prefix='axis_allies_interactive_')
            os.close(fd)

        self.generate_interactive_html(game_state, output_path)
        webbrowser.open(f'file://{os.path.abspath(output_path)}')
        return output_path


# ==============================================================================
# Convenience Functions
# ==============================================================================

def visualize_game_interactive(game_state: GameState,
                                output_path: str = None,
                                open_browser: bool = True) -> str:
    """
    Create fully interactive visualization with pre-computed valid moves/attacks.

    Args:
        game_state: The game state to visualize
        output_path: Optional path to save the HTML file
        open_browser: If True, open in browser

    Returns:
        Path to the generated HTML file
    """
    visualizer = InteractiveGameVisualizer()

    if open_browser:
        return visualizer.open_in_browser(game_state, output_path)
    else:
        path = output_path or "interactive_game.html"
        visualizer.generate_interactive_html(game_state, path)
        return path


def visualize_game(game_state: GameState, open_browser: bool = True,
                   output_path: str = None) -> str:
    """
    Quick visualization of a game state.

    Args:
        game_state: The game state to visualize
        open_browser: If True, open the visualization in browser
        output_path: Optional path to save the HTML file

    Returns:
        Path to the generated HTML file
    """
    visualizer = GameVisualizer()

    if open_browser:
        return visualizer.open_in_browser(game_state, output_path=output_path)
    else:
        path = output_path or "game_visualization.html"
        visualizer.save_to_file(game_state, path)
        return path


def create_sample_visualization() -> str:
    """
    Create a sample visualization for testing/demo purposes.

    Returns:
        Path to the generated HTML file
    """
    from game_state import create_test_game_state
    from facing import HexDirection

    # Create test game with some interesting state
    game_state = create_test_game_state(board_size=10)

    # Add some terrain variety
    board = game_state.board
    board.set_terrain(4, 4, 'forest')
    board.set_terrain(4, 5, 'forest')
    board.set_terrain(5, 4, 'forest')
    board.set_terrain(6, 6, 'building')
    board.set_terrain(6, 7, 'building')
    board.set_terrain(3, 7, 'road')
    board.set_terrain(4, 7, 'road')
    board.set_terrain(5, 7, 'road')
    board.set_terrain(6, 7, 'road')
    board.set_terrain(7, 7, 'road')
    board.set_terrain(8, 3, 'hill')
    board.set_terrain(8, 4, 'hill')
    board.set_terrain(2, 8, 'water')

    # Add some smoke screens
    game_state.add_smoke(5, 5)

    # Set varied facing directions for vehicles
    facing_directions = [
        HexDirection.EAST,      # 0
        HexDirection.SOUTHWEST, # 2
        HexDirection.WEST,      # 3
        HexDirection.NORTHEAST, # 5
    ]
    vehicle_count = 0
    for unit_id, unit_state in game_state.units.items():
        if unit_state.unit.unit_type == 'Vehicle':
            unit_state.facing = facing_directions[vehicle_count % len(facing_directions)]
            vehicle_count += 1

    # Modify some unit states for demonstration
    for unit_id, unit_state in list(game_state.units.items())[:1]:
        unit_state.is_disrupted = True

    return visualize_game(game_state, open_browser=False, output_path="sample_game.html")


# ==============================================================================
# Test Code
# ==============================================================================

if __name__ == "__main__":
    print("Testing GameVisualizer...")

    # Test 1: Basic visualization
    print("\n1. Creating sample visualization...")
    path = create_sample_visualization()
    print(f"   Generated: {path}")

    # Test 2: Open in browser
    print("\n2. Opening in browser...")
    from game_state import create_test_game_state
    game_state = create_test_game_state(board_size=8)

    visualizer = GameVisualizer()
    html_path = visualizer.open_in_browser(game_state)
    print(f"   Opened: {html_path}")

    # Test 3: Live visualizer
    print("\n3. Testing LiveGameVisualizer...")
    live_viz = LiveGameVisualizer(game_state, "live_game.html")
    live_viz.update()
    print(f"   Created: live_game.html")

    # Select a unit if available
    if game_state.units:
        unit_id = list(game_state.units.keys())[0]
        live_viz.select_unit(unit_id)
        print(f"   Selected unit: {unit_id}")

    print("\n✓ All tests passed!")
    print("\nGenerated files:")
    print("  - sample_game.html")
    print("  - live_game.html")
    print("\nOpen these files in a browser to view the visualizations.")
