"""
Visualization System for Axis & Allies Miniatures

Generates interactive HTML visualizations with SVG hex rendering,
unit markers, hover tooltips, and click-to-select functionality.
"""

import math
from typing import Dict, List, Tuple, Optional, Set, Any
from dataclasses import dataclass

from board import Board, Hex
from game_state import GameState, UnitState, GamePhase
from units import Unit
from facing import HexDirection, get_direction_name


# ==============================================================================
# Hex Rendering
# ==============================================================================

class HexRenderer:
    """
    Handles hex grid coordinate conversion and SVG polygon generation.
    Uses flat-top hexes with axial (q, r) coordinates.
    """

    def __init__(self, hex_size: float = 40):
        """
        Initialize hex renderer.

        Args:
            hex_size: Distance from center to vertex (circumradius)
        """
        self.hex_size = hex_size
        # Flat-top hex dimensions
        self.hex_width = hex_size * 2  # Point-to-point width
        self.hex_height = hex_size * math.sqrt(3)  # Flat-to-flat height

    def axial_to_pixel(self, q: int, r: int) -> Tuple[float, float]:
        """
        Convert axial hex coordinates to pixel coordinates.
        Returns center point of the hex.

        For flat-top hexes:
        x = size * 3/2 * q
        y = size * sqrt(3) * (r + q/2)
        """
        x = self.hex_size * 3/2 * q
        y = self.hex_size * math.sqrt(3) * (r + q / 2)
        return (x, y)

    def pixel_to_axial(self, x: float, y: float) -> Tuple[int, int]:
        """
        Convert pixel coordinates to axial hex coordinates.
        Returns the hex containing the point.
        """
        q = (2/3 * x) / self.hex_size
        r = (-1/3 * x + math.sqrt(3)/3 * y) / self.hex_size

        # Round to nearest hex
        return self._axial_round(q, r)

    def _axial_round(self, q: float, r: float) -> Tuple[int, int]:
        """Round fractional axial coordinates to nearest hex."""
        # Convert to cube coordinates
        x = q
        z = r
        y = -x - z

        # Round each coordinate
        rx = round(x)
        ry = round(y)
        rz = round(z)

        # Fix rounding errors
        x_diff = abs(rx - x)
        y_diff = abs(ry - y)
        z_diff = abs(rz - z)

        if x_diff > y_diff and x_diff > z_diff:
            rx = -ry - rz
        elif y_diff > z_diff:
            ry = -rx - rz
        else:
            rz = -rx - ry

        return (rx, rz)  # Convert back to axial

    def get_hex_vertices(self, q: int, r: int) -> List[Tuple[float, float]]:
        """
        Get the 6 vertices of a flat-top hex.
        Vertices are ordered clockwise starting from the right point.
        """
        cx, cy = self.axial_to_pixel(q, r)
        vertices = []

        for i in range(6):
            # Flat-top: first vertex at 0 degrees (right point)
            angle = math.pi / 3 * i  # 0, 60, 120, 180, 240, 300 degrees
            vx = cx + self.hex_size * math.cos(angle)
            vy = cy + self.hex_size * math.sin(angle)
            vertices.append((vx, vy))

        return vertices

    def get_hex_polygon_points(self, q: int, r: int) -> str:
        """Get SVG polygon points string for a hex."""
        vertices = self.get_hex_vertices(q, r)
        return " ".join(f"{x:.2f},{y:.2f}" for x, y in vertices)

    def get_viewbox(self, board: Board, padding: float = 50) -> str:
        """
        Calculate SVG viewBox for the board.
        Returns "minX minY width height" string.
        """
        if not board.hexes:
            return "0 0 100 100"

        # Find bounds
        min_x = float('inf')
        min_y = float('inf')
        max_x = float('-inf')
        max_y = float('-inf')

        for (q, r) in board.hexes.keys():
            cx, cy = self.axial_to_pixel(q, r)
            # Account for hex size
            min_x = min(min_x, cx - self.hex_size)
            min_y = min(min_y, cy - self.hex_height / 2)
            max_x = max(max_x, cx + self.hex_size)
            max_y = max(max_y, cy + self.hex_height / 2)

        # Add padding
        min_x -= padding
        min_y -= padding
        width = max_x - min_x + padding * 2
        height = max_y - min_y + padding * 2

        return f"{min_x:.2f} {min_y:.2f} {width:.2f} {height:.2f}"


# ==============================================================================
# Terrain Rendering
# ==============================================================================

class TerrainRenderer:
    """Renders terrain with appropriate colors and styling."""

    # Terrain colors - matches plan specification
    TERRAIN_COLORS = {
        'open': '#e8e4c9',      # Tan
        'forest': '#228b22',    # Green
        'building': '#808080',  # Gray
        'water': '#4169e1',     # Blue
        'road': '#a0522d',      # Brown
        'hill': '#8fbc8f',      # Sage
        'marsh': '#556b2f',     # Olive
        'town': '#cd853f',      # Tan/Peru
        'ruins': '#696969',     # Dim gray
    }

    # Terrain that provides cover (gets gold border)
    COVER_TERRAIN = {'forest', 'building', 'hill', 'town', 'ruins'}

    # Terrain that is difficult
    DIFFICULT_TERRAIN = {'forest', 'hill', 'marsh', 'ruins'}

    def get_terrain_color(self, terrain: str) -> str:
        """Get the fill color for a terrain type."""
        return self.TERRAIN_COLORS.get(terrain, '#e8e4c9')

    def provides_cover(self, terrain: str) -> bool:
        """Check if terrain provides cover."""
        return terrain in self.COVER_TERRAIN

    def get_terrain_stroke(self, terrain: str) -> str:
        """Get stroke color - gold for cover terrain, dark gray otherwise."""
        if self.provides_cover(terrain):
            return '#d4af37'  # Gold
        return '#555555'  # Dark gray

    def get_terrain_stroke_width(self, terrain: str) -> float:
        """Get stroke width - thicker for cover terrain."""
        if self.provides_cover(terrain):
            return 2.5
        return 1.5

    def render_hex_svg(self, hex_renderer: HexRenderer, hex_tile: Hex,
                       extra_class: str = "", extra_attrs: str = "") -> str:
        """Generate SVG polygon element for a hex."""
        points = hex_renderer.get_hex_polygon_points(hex_tile.q, hex_tile.r)
        fill = self.get_terrain_color(hex_tile.terrain)
        stroke = self.get_terrain_stroke(hex_tile.terrain)
        stroke_width = self.get_terrain_stroke_width(hex_tile.terrain)

        classes = f"hex hex-{hex_tile.terrain}"
        if extra_class:
            classes += f" {extra_class}"

        return f'''<polygon
            points="{points}"
            fill="{fill}"
            stroke="{stroke}"
            stroke-width="{stroke_width}"
            class="{classes}"
            data-q="{hex_tile.q}"
            data-r="{hex_tile.r}"
            data-terrain="{hex_tile.terrain}"
            {extra_attrs}
        />'''


# ==============================================================================
# Unit Rendering
# ==============================================================================

class UnitRenderer:
    """Renders units with appropriate icons based on type."""

    # Player colors
    PLAYER_COLORS = {
        'player1': '#3b82f6',  # Blue
        'player2': '#ef4444',  # Red
    }

    PLAYER_COLORS_LIGHT = {
        'player1': '#93c5fd',  # Light blue
        'player2': '#fca5a5',  # Light red
    }

    def __init__(self, hex_renderer: HexRenderer):
        self.hex_renderer = hex_renderer

    def get_player_color(self, owner: str) -> str:
        """Get the color for a player."""
        return self.PLAYER_COLORS.get(owner, '#888888')

    def get_player_color_light(self, owner: str) -> str:
        """Get light color variant for a player."""
        return self.PLAYER_COLORS_LIGHT.get(owner, '#cccccc')

    def render_unit_svg(self, unit_state: UnitState, show_status: bool = True) -> str:
        """Generate SVG elements for a unit."""
        q, r = unit_state.position
        cx, cy = self.hex_renderer.axial_to_pixel(q, r)
        unit = unit_state.unit
        owner = unit_state.owner

        color = self.get_player_color(owner)
        light_color = self.get_player_color_light(owner)

        # Determine opacity based on state
        opacity = 1.0
        if unit_state.has_moved and unit_state.has_attacked:
            opacity = 0.5
        elif unit_state.has_moved or unit_state.has_attacked:
            opacity = 0.75

        elements = []

        # Unit ID for interactivity
        unit_id = unit.id if hasattr(unit, 'id') else id(unit)

        # Create group for unit
        elements.append(f'<g class="unit unit-{unit.unit_type.lower()}" '
                       f'data-unit-id="{unit_id}" '
                       f'data-owner="{owner}" '
                       f'data-q="{q}" data-r="{r}" '
                       f'style="opacity: {opacity}">')

        # Render based on unit type
        if unit.unit_type == 'Soldier':
            elements.append(self._render_soldier(cx, cy, color, light_color))
        elif unit.unit_type == 'Vehicle':
            elements.append(self._render_vehicle(cx, cy, color, light_color, unit_state.facing))
        elif unit.unit_type == 'Aircraft':
            elements.append(self._render_aircraft(cx, cy, color, light_color))
        else:
            # Default: simple circle
            elements.append(f'<circle cx="{cx}" cy="{cy}" r="12" fill="{color}" stroke="#000" stroke-width="1.5"/>')

        # Add status indicators
        if show_status:
            elements.append(self._render_status_indicators(cx, cy, unit_state))

        elements.append('</g>')

        return '\n'.join(elements)

    def _render_soldier(self, cx: float, cy: float, color: str, light_color: str) -> str:
        """Render a soldier unit (circle with infantry icon)."""
        # Main circle
        svg = f'<circle cx="{cx}" cy="{cy}" r="14" fill="{color}" stroke="#000" stroke-width="2"/>'

        # Infantry icon (simple person silhouette)
        # Head
        svg += f'<circle cx="{cx}" cy="{cy - 5}" r="3" fill="#fff"/>'
        # Body (simple line)
        svg += f'<line x1="{cx}" y1="{cy - 2}" x2="{cx}" y2="{cy + 4}" stroke="#fff" stroke-width="2"/>'
        # Arms
        svg += f'<line x1="{cx - 4}" y1="{cy}" x2="{cx + 4}" y2="{cy}" stroke="#fff" stroke-width="1.5"/>'
        # Legs
        svg += f'<line x1="{cx}" y1="{cy + 4}" x2="{cx - 3}" y2="{cy + 8}" stroke="#fff" stroke-width="1.5"/>'
        svg += f'<line x1="{cx}" y1="{cy + 4}" x2="{cx + 3}" y2="{cy + 8}" stroke="#fff" stroke-width="1.5"/>'

        return svg

    def _render_vehicle(self, cx: float, cy: float, color: str, light_color: str,
                        facing: Optional[int] = None) -> str:
        """Render a vehicle unit (rounded rectangle with tank silhouette + facing arrow)."""
        # Rounded rectangle for tank body
        width, height = 24, 18
        rx = cx - width / 2
        ry = cy - height / 2

        svg = f'<rect x="{rx}" y="{ry}" width="{width}" height="{height}" rx="4" '
        svg += f'fill="{color}" stroke="#000" stroke-width="2"/>'

        # Tank turret (smaller rectangle on top)
        turret_w, turret_h = 10, 8
        svg += f'<rect x="{cx - turret_w/2}" y="{cy - turret_h/2 - 1}" width="{turret_w}" height="{turret_h}" rx="2" '
        svg += f'fill="{light_color}" stroke="#000" stroke-width="1"/>'

        # Gun barrel
        svg += f'<line x1="{cx + turret_w/2}" y1="{cy - 1}" x2="{cx + width/2 + 4}" y2="{cy - 1}" '
        svg += f'stroke="#000" stroke-width="3" stroke-linecap="round"/>'

        # Add facing arrow if specified
        if facing is not None:
            svg += self._render_facing_arrow(cx, cy, facing)

        return svg

    def _render_aircraft(self, cx: float, cy: float, color: str, light_color: str) -> str:
        """Render an aircraft unit (diamond shape with wing symbol)."""
        # Diamond shape
        size = 16
        points = [
            (cx, cy - size),      # Top
            (cx + size, cy),      # Right
            (cx, cy + size),      # Bottom
            (cx - size, cy),      # Left
        ]
        points_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)

        svg = f'<polygon points="{points_str}" fill="{color}" stroke="#000" stroke-width="2"/>'

        # Wings (horizontal line through center)
        wing_span = 12
        svg += f'<line x1="{cx - wing_span}" y1="{cy}" x2="{cx + wing_span}" y2="{cy}" '
        svg += f'stroke="#fff" stroke-width="3" stroke-linecap="round"/>'

        # Fuselage (vertical line)
        svg += f'<line x1="{cx}" y1="{cy - 8}" x2="{cx}" y2="{cy + 6}" '
        svg += f'stroke="#fff" stroke-width="2" stroke-linecap="round"/>'

        # Tail
        svg += f'<line x1="{cx - 4}" y1="{cy + 4}" x2="{cx + 4}" y2="{cy + 4}" '
        svg += f'stroke="#fff" stroke-width="2" stroke-linecap="round"/>'

        return svg

    def _render_facing_arrow(self, cx: float, cy: float, facing: int) -> str:
        """Render facing indicator for vehicles.

        Draws:
          1. A bold yellow arrow pointing toward the adjacent hex in the facing direction.
          2. A thick yellow edge highlight on the front face of the hex.

        Corrected angles for flat-top axial layout (x = 3/2*q*s, y = sqrt(3)*(r+q/2)*s):
          East  (1,0)  → neighbor at 30°   below horizontal
          SE    (1,-1) → neighbor at -30°  above horizontal
          SW    (0,-1) → neighbor at -90°  straight up (SVG y down)
          West  (-1,0) → neighbor at -150°
          NW    (-1,1) → neighbor at 150°
          NE    (0,1)  → neighbor at 90°   straight down
        """
        angles = {
            0: math.pi / 6,          # East      30°
            1: -math.pi / 6,         # Southeast -30°
            2: -math.pi / 2,         # Southwest -90°
            3: -5 * math.pi / 6,     # West      -150°
            4: 5 * math.pi / 6,      # Northwest  150°
            5: math.pi / 2,          # Northeast  90°
        }
        angle = angles.get(facing % 6, 0)
        perp  = angle + math.pi / 2
        s     = self.hex_renderer.hex_size

        # ── Arrow shaft + head ────────────────────────────────────────────────
        shaft_len  = s * 0.55
        head_half  = s * 0.20
        head_back  = s * 0.22

        tip_x  = cx + shaft_len * math.cos(angle)
        tip_y  = cy + shaft_len * math.sin(angle)
        base_x = cx + s * 0.12 * math.cos(angle)
        base_y = cy + s * 0.12 * math.sin(angle)

        h1x = tip_x - head_back * math.cos(angle) - head_half * math.cos(perp)
        h1y = tip_y - head_back * math.sin(angle) - head_half * math.sin(perp)
        h2x = tip_x - head_back * math.cos(angle) + head_half * math.cos(perp)
        h2y = tip_y - head_back * math.sin(angle) + head_half * math.sin(perp)

        svg  = (f'<line x1="{base_x:.1f}" y1="{base_y:.1f}" '
                f'x2="{tip_x:.1f}" y2="{tip_y:.1f}" '
                f'stroke="#ffe600" stroke-width="3" stroke-linecap="round"/>')
        svg += (f'<polygon points="{tip_x:.1f},{tip_y:.1f} '
                f'{h1x:.1f},{h1y:.1f} {h2x:.1f},{h2y:.1f}" '
                f'fill="#ffe600" stroke="#222" stroke-width="1"/>')

        # ── Front-edge highlight on the hex ────────────────────────────────────
        # For flat-top hex, vertex i is at angle i*60°.
        # The edge facing direction f corresponds to edge between
        # vertex (6-f)%6 and vertex (7-f)%6.
        edge_i  = (6 - facing % 6) % 6
        edge_j  = (edge_i + 1) % 6
        v_angle_i = math.radians(edge_i * 60)
        v_angle_j = math.radians(edge_j * 60)
        vix = cx + s * math.cos(v_angle_i)
        viy = cy + s * math.sin(v_angle_i)
        vjx = cx + s * math.cos(v_angle_j)
        vjy = cy + s * math.sin(v_angle_j)
        svg += (f'<line x1="{vix:.1f}" y1="{viy:.1f}" '
                f'x2="{vjx:.1f}" y2="{vjy:.1f}" '
                f'stroke="#ffe600" stroke-width="4" stroke-linecap="round" opacity="0.9"/>')

        return svg

    def _render_status_indicators(self, cx: float, cy: float, unit_state: UnitState) -> str:
        """Render status indicators (health bar, disrupted/damaged badges)."""
        svg = ""

        # Health bar below unit
        bar_width = 24
        bar_height = 4
        bar_x = cx - bar_width / 2
        bar_y = cy + 16

        # Get max health (defense_front as proxy for max health)
        unit = unit_state.unit
        max_health = getattr(unit, 'defense_front', 3) or 3
        current_health = unit_state.current_health
        health_pct = current_health / max_health if max_health > 0 else 0

        # Health bar color
        if health_pct > 0.6:
            health_color = '#22c55e'  # Green
        elif health_pct > 0.3:
            health_color = '#eab308'  # Yellow
        else:
            health_color = '#ef4444'  # Red

        # Background
        svg += f'<rect x="{bar_x}" y="{bar_y}" width="{bar_width}" height="{bar_height}" '
        svg += f'fill="#333" rx="1"/>'

        # Health fill
        fill_width = bar_width * health_pct
        if fill_width > 0:
            svg += f'<rect x="{bar_x}" y="{bar_y}" width="{fill_width:.1f}" height="{bar_height}" '
            svg += f'fill="{health_color}" rx="1"/>'

        # Status badges
        badge_y = cy - 20
        badge_x = cx + 10

        # Disrupted badge (yellow lightning)
        if unit_state.is_disrupted:
            svg += f'<circle cx="{badge_x}" cy="{badge_y}" r="6" fill="#fbbf24" stroke="#000" stroke-width="1"/>'
            # Lightning bolt
            svg += f'<text x="{badge_x}" y="{badge_y + 3}" text-anchor="middle" font-size="8" fill="#000">!</text>'
            badge_x += 14

        # Damaged badge (orange marker)
        if unit_state.is_damaged:
            svg += f'<circle cx="{badge_x}" cy="{badge_y}" r="6" fill="#f97316" stroke="#000" stroke-width="1"/>'
            svg += f'<text x="{badge_x}" y="{badge_y + 3}" text-anchor="middle" font-size="8" fill="#fff">D</text>'
            badge_x += 14

        # Transport indicator (if carrying a unit)
        if unit_state.carried_unit_id:
            svg += f'<circle cx="{badge_x}" cy="{badge_y}" r="6" fill="#8b5cf6" stroke="#000" stroke-width="1"/>'
            svg += f'<text x="{badge_x}" y="{badge_y + 3}" text-anchor="middle" font-size="8" fill="#fff">T</text>'
            badge_x += 14

        # Being carried indicator (small icon if unit is inside a transport)
        if unit_state.carried_by_id:
            svg += f'<circle cx="{badge_x}" cy="{badge_y}" r="6" fill="#a855f7" stroke="#000" stroke-width="1"/>'
            svg += f'<text x="{badge_x}" y="{badge_y + 3}" text-anchor="middle" font-size="8" fill="#fff">C</text>'

        # Move/Attack status indicators on the left side
        left_badge_x = cx - 18
        if unit_state.has_moved:
            svg += f'<circle cx="{left_badge_x}" cy="{badge_y}" r="5" fill="#666" stroke="#000" stroke-width="1"/>'
            svg += f'<text x="{left_badge_x}" y="{badge_y + 3}" text-anchor="middle" font-size="7" fill="#fff">M</text>'
            left_badge_x -= 12
        if unit_state.has_attacked:
            svg += f'<circle cx="{left_badge_x}" cy="{badge_y}" r="5" fill="#666" stroke="#000" stroke-width="1"/>'
            svg += f'<text x="{left_badge_x}" y="{badge_y + 3}" text-anchor="middle" font-size="7" fill="#fff">A</text>'

        return svg


# ==============================================================================
# Tooltip Generation
# ==============================================================================

class TooltipGenerator:
    """Generates HTML tooltips with unit information."""

    def generate_tooltip_content(self, unit_state: UnitState) -> str:
        """Generate HTML content for unit tooltip."""
        unit = unit_state.unit

        # Header
        html = f'<div class="tooltip-header">'
        html += f'<strong>{unit.name}</strong>'
        if hasattr(unit, 'nation') and unit.nation:
            html += f' <span class="nation">({unit.nation})</span>'
        html += '</div>'

        # Type and cost
        html += f'<div class="tooltip-row">'
        html += f'<span>Type: {unit.unit_type}</span>'
        if hasattr(unit, 'cost') and unit.cost:
            html += f' | <span>Cost: {unit.cost} pts</span>'
        html += '</div>'

        # Defense
        html += f'<div class="tooltip-row">'
        front = getattr(unit, 'defense_front', '-')
        rear = getattr(unit, 'defense_rear', front)
        html += f'Defense: {front}/{rear}'
        if hasattr(unit, 'speed'):
            html += f' | Speed: {unit.speed}'
        html += '</div>'

        # Attack values
        html += '<div class="tooltip-attacks">'

        # Anti-Vehicle
        veh_s = getattr(unit, 'veh_short', 0) or '-'
        veh_m = getattr(unit, 'veh_medium', 0) or '-'
        veh_l = getattr(unit, 'veh_long', 0) or '-'
        html += f'<div>Anti-Vehicle: {veh_s}/{veh_m}/{veh_l}</div>'

        # Anti-Personnel
        per_s = getattr(unit, 'per_short', 0) or '-'
        per_m = getattr(unit, 'per_medium', 0) or '-'
        per_l = getattr(unit, 'per_long', 0) or '-'
        html += f'<div>Anti-Personnel: {per_s}/{per_m}/{per_l}</div>'

        html += '</div>'

        # Abilities
        abilities = getattr(unit, 'abilities', [])
        if abilities:
            html += '<div class="tooltip-abilities">'
            html += '<strong>Abilities:</strong> '
            html += ', '.join(abilities[:5])  # Limit to first 5
            if len(abilities) > 5:
                html += f' (+{len(abilities) - 5} more)'
            html += '</div>'

        # Status
        status_parts = []
        if unit_state.is_disrupted:
            status_parts.append('Disrupted')
        if unit_state.is_damaged:
            status_parts.append('Damaged')
        if unit_state.has_moved:
            status_parts.append('Moved')
        if unit_state.has_attacked:
            status_parts.append('Attacked')

        html += f'<div class="tooltip-status">'
        if status_parts:
            html += f'Status: {", ".join(status_parts)} | '
        html += f'Health: {unit_state.current_health}'
        html += '</div>'

        return html


# ==============================================================================
# Game Info Panel
# ==============================================================================

class GameInfoRenderer:
    """Renders game information panels."""

    def render_header_panel(self, game_state: GameState) -> str:
        """Generate HTML for header panel with turn/phase info."""
        html = '<div class="game-header">'
        html += f'<div class="turn-info">Turn {game_state.turn_number}</div>'
        html += f'<div class="phase-info">{game_state.current_phase.upper()} Phase</div>'

        # Active player indicator
        player_class = 'player1' if game_state.active_player == 'player1' else 'player2'
        player_name = 'Player 1 (Blue)' if game_state.active_player == 'player1' else 'Player 2 (Red)'
        html += f'<div class="active-player {player_class}">{player_name}\'s Turn</div>'

        html += '</div>'
        return html

    def render_sidebar(self, game_state: GameState) -> str:
        """Generate HTML for sidebar with unit lists."""
        html = '<div class="sidebar">'

        # Legend section
        html += self._render_legend()

        # Player 1 units
        html += '<div class="player-section player1-section">'
        html += '<h3>Player 1 (Blue)</h3>'
        html += '<ul class="unit-list">'
        for us in game_state.get_units_by_owner('player1'):
            html += self._render_unit_list_item(us)
        html += '</ul>'
        html += '</div>'

        # Player 2 units
        html += '<div class="player-section player2-section">'
        html += '<h3>Player 2 (Red)</h3>'
        html += '<ul class="unit-list">'
        for us in game_state.get_units_by_owner('player2'):
            html += self._render_unit_list_item(us)
        html += '</ul>'
        html += '</div>'

        html += '</div>'
        return html

    def _render_legend(self) -> str:
        """Render terrain and unit legend."""
        html = '<div class="legend-section">'
        html += '<h3>Legend</h3>'

        # Terrain legend
        html += '<div class="legend-group">'
        html += '<h4>Terrain</h4>'
        terrains = [
            ('open', '#e8e4c9', 'Open'),
            ('forest', '#228b22', 'Forest (Cover)'),
            ('building', '#808080', 'Building (Cover)'),
            ('hill', '#8fbc8f', 'Hill (Cover)'),
            ('water', '#4169e1', 'Water'),
            ('road', '#a0522d', 'Road'),
        ]
        for terrain_id, color, label in terrains:
            border = '#d4af37' if 'Cover' in label else '#555'
            html += f'<div class="legend-item">'
            html += f'<span class="legend-swatch" style="background:{color};border-color:{border}"></span>'
            html += f'<span class="legend-label">{label}</span>'
            html += '</div>'
        html += '</div>'

        # Unit type legend
        html += '<div class="legend-group">'
        html += '<h4>Units</h4>'
        html += '<div class="legend-item"><span class="legend-icon">&#9679;</span> Soldier</div>'
        html += '<div class="legend-item"><span class="legend-icon">&#9632;</span> Vehicle</div>'
        html += '<div class="legend-item"><span class="legend-icon">&#9670;</span> Aircraft</div>'
        html += '</div>'

        # Status legend
        html += '<div class="legend-group">'
        html += '<h4>Status</h4>'
        html += '<div class="legend-item"><span class="status-badge disrupted-badge">!</span> Disrupted</div>'
        html += '<div class="legend-item"><span class="status-badge damaged-badge">D</span> Damaged</div>'
        html += '<div class="legend-item"><span class="status-badge transport-badge">T</span> Transport</div>'
        html += '</div>'

        # Markers legend
        html += '<div class="legend-group">'
        html += '<h4>Markers</h4>'
        html += '<div class="legend-item"><span class="legend-icon" style="color:#fbbf24">&#9733;</span> Objective</div>'
        html += '<div class="legend-item"><span class="legend-icon" style="color:#aaa">&#9729;</span> Smoke</div>'
        html += '</div>'

        # Instructions
        html += '<div class="legend-group">'
        html += '<h4>Controls</h4>'
        html += '<div class="legend-item" style="display:block;line-height:1.4">'
        html += '<small>Click unit to see valid moves (green) and attacks (red). '
        html += 'Hover for stats.</small>'
        html += '</div>'
        html += '</div>'

        html += '</div>'
        return html

    def _render_unit_list_item(self, unit_state: UnitState) -> str:
        """Render a single unit in the sidebar list."""
        unit = unit_state.unit
        unit_id = unit.id if hasattr(unit, 'id') else id(unit)

        classes = ['unit-item']
        if unit_state.is_disrupted:
            classes.append('disrupted')
        if unit_state.is_damaged:
            classes.append('damaged')
        if not unit_state.is_alive:
            classes.append('destroyed')

        # Unit type icon
        type_icon = {'Soldier': '&#9679;', 'Vehicle': '&#9632;', 'Aircraft': '&#9670;'}.get(unit.unit_type, '?')

        # Status indicators
        status = []
        if unit_state.has_moved:
            status.append('M')
        if unit_state.has_attacked:
            status.append('A')
        if unit_state.is_disrupted:
            status.append('!')
        if unit_state.is_damaged:
            status.append('D')
        status_str = ''.join(status) if status else ''

        html = f'<li class="{" ".join(classes)}" data-unit-id="{unit_id}">'
        html += f'<span class="unit-type-icon">{type_icon}</span>'
        html += f'<span class="unit-name">{unit.name}</span>'
        html += f'<span class="unit-status">{status_str}</span>'
        html += f'<span class="unit-hp">{unit_state.current_health}/{getattr(unit, "defense_front", "?")}</span>'
        html += '</li>'
        return html


# ==============================================================================
# Overlay Rendering (Smoke, Objectives, etc.)
# ==============================================================================

class OverlayRenderer:
    """Renders overlays like smoke screens, objectives, and edge obstacles."""

    def __init__(self, hex_renderer: HexRenderer):
        self.hex_renderer = hex_renderer

    def render_smoke_screen(self, q: int, r: int) -> str:
        """Render a smoke screen overlay on a hex."""
        cx, cy = self.hex_renderer.axial_to_pixel(q, r)
        points = self.hex_renderer.get_hex_polygon_points(q, r)

        svg = f'<polygon points="{points}" fill="rgba(200, 200, 200, 0.6)" '
        svg += f'stroke="none" class="smoke-screen" data-q="{q}" data-r="{r}"/>'

        # Add some "smoke" circles for visual effect
        for i in range(3):
            offset_x = (i - 1) * 8
            offset_y = (i % 2) * 6 - 3
            svg += f'<circle cx="{cx + offset_x}" cy="{cy + offset_y}" r="8" '
            svg += f'fill="rgba(180, 180, 180, 0.5)"/>'

        return svg

    def render_objective(self, q: int, r: int, controller: Optional[str] = None) -> str:
        """Render the objective marker."""
        cx, cy = self.hex_renderer.axial_to_pixel(q, r)

        # Star shape for objective
        outer_r = 12
        inner_r = 5
        points = []
        for i in range(10):
            angle = math.pi / 2 + i * math.pi / 5  # Start from top
            r_val = outer_r if i % 2 == 0 else inner_r
            x = cx + r_val * math.cos(angle)
            y = cy - r_val * math.sin(angle)
            points.append(f"{x:.2f},{y:.2f}")

        # Color based on controller
        if controller == 'player1':
            color = '#3b82f6'
        elif controller == 'player2':
            color = '#ef4444'
        else:
            color = '#fbbf24'  # Yellow/gold for contested/neutral

        svg = f'<polygon points="{" ".join(points)}" fill="{color}" '
        svg += f'stroke="#000" stroke-width="2" class="objective"/>'

        return svg

    def render_edge_obstacle(self, q1: int, r1: int, q2: int, r2: int,
                              obstacle_type: str) -> str:
        """Render an edge obstacle (barbed wire, etc.) between two hexes."""
        # Get centers of both hexes
        cx1, cy1 = self.hex_renderer.axial_to_pixel(q1, r1)
        cx2, cy2 = self.hex_renderer.axial_to_pixel(q2, r2)

        # Midpoint
        mx, my = (cx1 + cx2) / 2, (cy1 + cy2) / 2

        # Direction perpendicular to the line between centers
        dx, dy = cx2 - cx1, cy2 - cy1
        length = math.sqrt(dx*dx + dy*dy)
        if length > 0:
            # Perpendicular unit vector
            px, py = -dy/length, dx/length
        else:
            px, py = 1, 0

        # Draw obstacle (barbed wire pattern)
        wire_length = 20
        svg = ''

        if obstacle_type.lower() == 'barbed wire':
            # Zigzag pattern
            for i in range(-2, 3):
                x1 = mx + px * wire_length * (i - 0.5) / 4
                y1 = my + py * wire_length * (i - 0.5) / 4
                x2 = mx + px * wire_length * i / 4
                y2 = my + py * wire_length * i / 4
                # Alternate up/down
                offset = 3 if i % 2 == 0 else -3
                x1 += dx/length * offset
                y1 += dy/length * offset
                svg += f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                svg += f'stroke="#444" stroke-width="2"/>'
        else:
            # Generic obstacle line
            x1 = mx - px * wire_length / 2
            y1 = my - py * wire_length / 2
            x2 = mx + px * wire_length / 2
            y2 = my + py * wire_length / 2
            svg = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            svg += f'stroke="#666" stroke-width="3" stroke-dasharray="5,3"/>'

        return svg


# ==============================================================================
# Action Highlighting
# ==============================================================================

class ActionHighlighter:
    """Highlights valid moves and attacks for selected units."""

    def __init__(self, hex_renderer: HexRenderer):
        self.hex_renderer = hex_renderer

    def render_move_highlight(self, q: int, r: int) -> str:
        """Render a green highlight for valid move destination."""
        points = self.hex_renderer.get_hex_polygon_points(q, r)
        return f'<polygon points="{points}" fill="rgba(34, 197, 94, 0.4)" ' \
               f'stroke="#22c55e" stroke-width="3" class="move-highlight" ' \
               f'data-q="{q}" data-r="{r}"/>'

    def render_attack_highlight(self, q: int, r: int) -> str:
        """Render a red highlight for valid attack target."""
        points = self.hex_renderer.get_hex_polygon_points(q, r)
        return f'<polygon points="{points}" fill="rgba(239, 68, 68, 0.4)" ' \
               f'stroke="#ef4444" stroke-width="3" class="attack-highlight" ' \
               f'data-q="{q}" data-r="{r}"/>'

    def render_selection_highlight(self, q: int, r: int) -> str:
        """Render a selection highlight for the selected unit's hex."""
        points = self.hex_renderer.get_hex_polygon_points(q, r)
        return f'<polygon points="{points}" fill="none" ' \
               f'stroke="#fff" stroke-width="4" class="selection-highlight" ' \
               f'stroke-dasharray="8,4" data-q="{q}" data-r="{r}"/>'


# ==============================================================================
# HTML Generator
# ==============================================================================

class HTMLGenerator:
    """Generates complete HTML document with embedded CSS and JavaScript."""

    def __init__(self):
        self.hex_renderer = HexRenderer()
        self.terrain_renderer = TerrainRenderer()
        self.unit_renderer = UnitRenderer(self.hex_renderer)
        self.tooltip_generator = TooltipGenerator()
        self.game_info_renderer = GameInfoRenderer()
        self.overlay_renderer = OverlayRenderer(self.hex_renderer)
        self.action_highlighter = ActionHighlighter(self.hex_renderer)

    def generate_html(self, game_state: GameState,
                      valid_moves: Set[Tuple[int, int]] = None,
                      valid_attacks: Set[Tuple[int, int]] = None,
                      selected_unit_id: str = None) -> str:
        """Generate complete HTML visualization."""

        # Generate all components
        svg_content = self._generate_svg(game_state, valid_moves, valid_attacks,
                                         selected_unit_id)
        header = self.game_info_renderer.render_header_panel(game_state)
        sidebar = self.game_info_renderer.render_sidebar(game_state)
        css = self._generate_css()
        js = self._generate_javascript(game_state)
        tooltip_data = self._generate_tooltip_data(game_state)

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Axis & Allies Miniatures</title>
    <style>
{css}
    </style>
</head>
<body>
    <div class="game-container">
        {header}
        <div class="main-content">
            <div class="board-container">
                {svg_content}
            </div>
            {sidebar}
        </div>
    </div>

    <div id="tooltip" class="tooltip"></div>

    <script>
        const TOOLTIP_DATA = {tooltip_data};
{js}
    </script>
</body>
</html>'''

    def _generate_svg(self, game_state: GameState,
                      valid_moves: Set[Tuple[int, int]] = None,
                      valid_attacks: Set[Tuple[int, int]] = None,
                      selected_unit_id: str = None) -> str:
        """Generate the SVG board visualization."""
        board = game_state.board
        viewbox = self.hex_renderer.get_viewbox(board)

        elements = []

        # Start SVG
        elements.append(f'<svg id="game-board" viewBox="{viewbox}" '
                       f'preserveAspectRatio="xMidYMid meet">')

        # Render terrain hexes
        elements.append('<g class="terrain-layer">')
        for (q, r), hex_tile in board.hexes.items():
            elements.append(self.terrain_renderer.render_hex_svg(
                self.hex_renderer, hex_tile))
        elements.append('</g>')

        # Render hex coordinates (for debugging/reference)
        elements.append('<g class="coord-layer">')
        for (q, r), hex_tile in board.hexes.items():
            cx, cy = self.hex_renderer.axial_to_pixel(q, r)
            elements.append(f'<text x="{cx}" y="{cy + 28}" '
                          f'text-anchor="middle" font-size="8" '
                          f'fill="#666" class="hex-coord">{q},{r}</text>')
        elements.append('</g>')

        # Render edge obstacles
        elements.append('<g class="obstacle-layer">')
        for edge_key, obstacle_type in board.edge_obstacles.items():
            (q1, r1), (q2, r2) = list(edge_key)
            elements.append(self.overlay_renderer.render_edge_obstacle(
                q1, r1, q2, r2, obstacle_type))
        elements.append('</g>')

        # Render smoke screens
        elements.append('<g class="smoke-layer">')
        for (q, r) in game_state.smoke_screens:
            elements.append(self.overlay_renderer.render_smoke_screen(q, r))
        elements.append('</g>')

        # Render objective
        obj_q, obj_r = game_state.objective_position
        controller = game_state.check_objective_control()
        elements.append('<g class="objective-layer">')
        elements.append(self.overlay_renderer.render_objective(obj_q, obj_r, controller))
        elements.append('</g>')

        # Render action highlights (moves/attacks)
        elements.append('<g class="highlight-layer">')

        if valid_moves:
            for (q, r) in valid_moves:
                elements.append(self.action_highlighter.render_move_highlight(q, r))

        if valid_attacks:
            for (q, r) in valid_attacks:
                elements.append(self.action_highlighter.render_attack_highlight(q, r))

        # Selection highlight
        if selected_unit_id:
            unit_state = game_state.get_unit_state(selected_unit_id)
            if unit_state:
                q, r = unit_state.position
                elements.append(self.action_highlighter.render_selection_highlight(q, r))

        elements.append('</g>')

        # Render units
        elements.append('<g class="unit-layer">')
        for unit_state in game_state.units.values():
            if unit_state.is_alive:
                elements.append(self.unit_renderer.render_unit_svg(unit_state))
        elements.append('</g>')

        elements.append('</svg>')

        return '\n'.join(elements)

    def _generate_tooltip_data(self, game_state: GameState) -> str:
        """Generate JSON tooltip data for all units."""
        import json

        tooltips = {}
        for unit_id, unit_state in game_state.units.items():
            tooltips[unit_id] = self.tooltip_generator.generate_tooltip_content(unit_state)

        return json.dumps(tooltips)

    def _generate_css(self) -> str:
        """Generate CSS styles."""
        return '''
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #e8e8e8;
            min-height: 100vh;
        }

        .game-container {
            display: flex;
            flex-direction: column;
            height: 100vh;
        }

        .game-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 24px;
            background: #16213e;
            border-bottom: 2px solid #0f3460;
        }

        .turn-info {
            font-size: 24px;
            font-weight: bold;
            color: #e94560;
        }

        .phase-info {
            font-size: 18px;
            color: #a8a8a8;
        }

        .active-player {
            padding: 8px 16px;
            border-radius: 8px;
            font-weight: bold;
        }

        .active-player.player1 {
            background: #3b82f6;
            color: white;
        }

        .active-player.player2 {
            background: #ef4444;
            color: white;
        }

        .main-content {
            display: flex;
            flex: 1;
            overflow: hidden;
        }

        .board-container {
            flex: 1;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
            overflow: auto;
        }

        #game-board {
            max-width: 100%;
            max-height: 100%;
            background: #0d1b2a;
            border-radius: 8px;
        }

        .sidebar {
            width: 280px;
            background: #16213e;
            border-left: 2px solid #0f3460;
            padding: 16px;
            overflow-y: auto;
        }

        .player-section {
            margin-bottom: 24px;
        }

        .player-section h3 {
            padding: 8px 12px;
            margin-bottom: 8px;
            border-radius: 4px;
            font-size: 14px;
        }

        .player1-section h3 {
            background: rgba(59, 130, 246, 0.3);
            border-left: 4px solid #3b82f6;
        }

        .player2-section h3 {
            background: rgba(239, 68, 68, 0.3);
            border-left: 4px solid #ef4444;
        }

        .unit-list {
            list-style: none;
        }

        .unit-item {
            display: flex;
            justify-content: space-between;
            padding: 8px 12px;
            margin: 4px 0;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 4px;
            cursor: pointer;
            transition: background 0.2s;
        }

        .unit-item:hover {
            background: rgba(255, 255, 255, 0.1);
        }

        .unit-item.disrupted {
            border-left: 3px solid #fbbf24;
        }

        .unit-item.damaged {
            border-left: 3px solid #f97316;
        }

        .unit-name {
            font-size: 13px;
        }

        .unit-hp {
            font-size: 12px;
            color: #888;
        }

        /* Hex styles */
        .hex {
            cursor: pointer;
            transition: filter 0.2s;
        }

        .hex:hover {
            filter: brightness(1.2);
        }

        .hex-coord {
            pointer-events: none;
            user-select: none;
        }

        /* Unit styles */
        .unit {
            cursor: pointer;
        }

        .unit.selected {
            filter: drop-shadow(0 0 6px white);
        }

        /* Highlight styles */
        .move-highlight, .attack-highlight {
            cursor: pointer;
            transition: opacity 0.2s;
        }

        .move-highlight:hover {
            opacity: 0.8;
        }

        .attack-highlight:hover {
            opacity: 0.8;
        }

        .selection-highlight {
            animation: pulse 1.5s ease-in-out infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        /* Tooltip - pointer-events: none prevents flickering */
        .tooltip {
            position: fixed;
            display: none;
            background: #16213e;
            border: 2px solid #0f3460;
            border-radius: 8px;
            padding: 12px;
            max-width: 280px;
            z-index: 1000;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
            font-size: 13px;
            pointer-events: none;
        }

        .tooltip.visible {
            display: block;
        }

        .tooltip-header {
            font-size: 15px;
            margin-bottom: 8px;
            padding-bottom: 8px;
            border-bottom: 1px solid #0f3460;
        }

        .tooltip-header .nation {
            color: #888;
            font-weight: normal;
        }

        .tooltip-row {
            margin: 4px 0;
            color: #bbb;
        }

        .tooltip-attacks {
            margin: 8px 0;
            padding: 8px;
            background: rgba(0, 0, 0, 0.2);
            border-radius: 4px;
            font-size: 12px;
        }

        .tooltip-abilities {
            margin: 8px 0;
            font-size: 12px;
            color: #a8a8a8;
        }

        .tooltip-status {
            margin-top: 8px;
            padding-top: 8px;
            border-top: 1px solid #0f3460;
            font-size: 12px;
            color: #888;
        }

        /* Legend styles */
        .legend-section {
            margin-bottom: 20px;
            padding-bottom: 16px;
            border-bottom: 1px solid #0f3460;
        }

        .legend-section h3 {
            font-size: 14px;
            color: #e8e8e8;
            margin-bottom: 12px;
            padding: 6px 10px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 4px;
        }

        .legend-group {
            margin-bottom: 12px;
        }

        .legend-group h4 {
            font-size: 11px;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }

        .legend-item {
            display: flex;
            align-items: center;
            font-size: 11px;
            color: #aaa;
            margin: 4px 0;
            gap: 8px;
        }

        .legend-swatch {
            width: 16px;
            height: 14px;
            border-radius: 2px;
            border: 2px solid;
            flex-shrink: 0;
        }

        .legend-icon {
            width: 16px;
            text-align: center;
            font-size: 12px;
            color: #888;
        }

        .status-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 16px;
            height: 16px;
            border-radius: 50%;
            font-size: 10px;
            font-weight: bold;
        }

        .disrupted-badge {
            background: #fbbf24;
            color: #000;
        }

        .damaged-badge {
            background: #f97316;
            color: #fff;
        }

        .transport-badge {
            background: #8b5cf6;
            color: #fff;
        }

        /* Improved unit list items */
        .unit-item {
            display: grid;
            grid-template-columns: 18px 1fr auto auto;
            gap: 6px;
            align-items: center;
            padding: 6px 10px;
            margin: 3px 0;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.2s;
            font-size: 12px;
        }

        .unit-item:hover {
            background: rgba(255, 255, 255, 0.12);
            transform: translateX(2px);
        }

        .unit-item.selected {
            background: rgba(255, 255, 255, 0.2);
            box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.3);
        }

        .unit-item.destroyed {
            opacity: 0.4;
            text-decoration: line-through;
        }

        .unit-type-icon {
            color: #666;
            font-size: 10px;
        }

        .unit-name {
            font-size: 12px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .unit-status {
            font-size: 10px;
            color: #fbbf24;
            font-weight: bold;
        }

        .unit-hp {
            font-size: 11px;
            color: #666;
            font-family: monospace;
        }

        /* Improved hex coordinates */
        .hex-coord {
            pointer-events: none;
            user-select: none;
            font-family: monospace;
            font-weight: bold;
        }

        /* Transport indicator on units */
        .transport-indicator {
            fill: #8b5cf6;
            stroke: #fff;
            stroke-width: 1;
        }
'''

    def _generate_javascript(self, game_state: GameState) -> str:
        """Generate JavaScript for interactivity."""
        import json

        # Serialize game state for JS
        game_data = {
            'turn': game_state.turn_number,
            'phase': game_state.current_phase,
            'activePlayer': game_state.active_player,
            'units': {}
        }

        for unit_id, unit_state in game_state.units.items():
            game_data['units'][unit_id] = {
                'position': unit_state.position,
                'owner': unit_state.owner,
                'hasMoved': unit_state.has_moved,
                'hasAttacked': unit_state.has_attacked,
                'isDisrupted': unit_state.is_disrupted,
                'isDamaged': unit_state.is_damaged,
                'health': unit_state.current_health
            }

        return f'''
        const GAME_STATE = {json.dumps(game_data)};

        let selectedUnitId = null;
        const tooltip = document.getElementById('tooltip');

        // Initialize event listeners
        document.addEventListener('DOMContentLoaded', () => {{
            initHexListeners();
            initUnitListeners();
            initSidebarListeners();
        }});

        function initHexListeners() {{
            document.querySelectorAll('.hex').forEach(hex => {{
                hex.addEventListener('click', handleHexClick);
            }});

            document.querySelectorAll('.move-highlight').forEach(hex => {{
                hex.addEventListener('click', handleMoveClick);
            }});

            document.querySelectorAll('.attack-highlight').forEach(hex => {{
                hex.addEventListener('click', handleAttackClick);
            }});
        }}

        function initUnitListeners() {{
            document.querySelectorAll('.unit').forEach(unit => {{
                unit.addEventListener('click', handleUnitClick);
                unit.addEventListener('mouseenter', showTooltip);
                unit.addEventListener('mouseleave', hideTooltip);
                unit.addEventListener('mousemove', moveTooltip);
            }});
        }}

        function initSidebarListeners() {{
            document.querySelectorAll('.unit-item').forEach(item => {{
                item.addEventListener('click', () => {{
                    const unitId = item.dataset.unitId;
                    selectUnit(unitId);
                }});
            }});
        }}

        function handleHexClick(e) {{
            const q = parseInt(e.target.dataset.q);
            const r = parseInt(e.target.dataset.r);
            console.log(`Hex clicked: (${{q}}, ${{r}})`);

            // Deselect if clicking empty hex
            if (selectedUnitId) {{
                deselectUnit();
            }}
        }}

        function handleUnitClick(e) {{
            e.stopPropagation();
            const unitGroup = e.target.closest('.unit');
            const unitId = unitGroup.dataset.unitId;

            if (selectedUnitId === unitId) {{
                deselectUnit();
            }} else {{
                selectUnit(unitId);
            }}
        }}

        function handleMoveClick(e) {{
            e.stopPropagation();
            const q = parseInt(e.target.dataset.q);
            const r = parseInt(e.target.dataset.r);
            console.log(`Move to: (${{q}}, ${{r}})`);

            if (selectedUnitId) {{
                // In a full implementation, this would send the move to the game engine
                alert(`Moving unit to (${{q}}, ${{r}})`);
            }}
        }}

        function handleAttackClick(e) {{
            e.stopPropagation();
            const q = parseInt(e.target.dataset.q);
            const r = parseInt(e.target.dataset.r);
            console.log(`Attack at: (${{q}}, ${{r}})`);

            if (selectedUnitId) {{
                // In a full implementation, this would send the attack to the game engine
                alert(`Attacking target at (${{q}}, ${{r}})`);
            }}
        }}

        function selectUnit(unitId) {{
            selectedUnitId = unitId;

            // Update visual selection
            document.querySelectorAll('.unit').forEach(u => {{
                u.classList.remove('selected');
            }});

            const unitEl = document.querySelector(`.unit[data-unit-id="${{unitId}}"]`);
            if (unitEl) {{
                unitEl.classList.add('selected');
            }}

            // Update sidebar selection
            document.querySelectorAll('.unit-item').forEach(item => {{
                item.style.background = item.dataset.unitId === unitId
                    ? 'rgba(255, 255, 255, 0.2)'
                    : '';
            }});

            console.log(`Selected unit: ${{unitId}}`);
        }}

        function deselectUnit() {{
            selectedUnitId = null;

            document.querySelectorAll('.unit').forEach(u => {{
                u.classList.remove('selected');
            }});

            document.querySelectorAll('.unit-item').forEach(item => {{
                item.style.background = '';
            }});
        }}

        let tooltipTimeout = null;
        let currentTooltipUnitId = null;

        function showTooltip(e) {{
            const unitGroup = e.target.closest('.unit');
            if (!unitGroup) return;

            const unitId = unitGroup.dataset.unitId;
            currentTooltipUnitId = unitId;

            // Clear any pending hide
            if (tooltipTimeout) {{
                clearTimeout(tooltipTimeout);
                tooltipTimeout = null;
            }}

            if (TOOLTIP_DATA[unitId]) {{
                tooltip.innerHTML = TOOLTIP_DATA[unitId];
                tooltip.classList.add('visible');
                positionTooltip(e.clientX, e.clientY);
            }}
        }}

        function hideTooltip(e) {{
            // Delay hiding to prevent flicker when moving between child elements
            tooltipTimeout = setTimeout(() => {{
                tooltip.classList.remove('visible');
                currentTooltipUnitId = null;
            }}, 50);
        }}

        function moveTooltip(e) {{
            if (tooltip.classList.contains('visible')) {{
                positionTooltip(e.clientX, e.clientY);
            }}
        }}

        function positionTooltip(mouseX, mouseY) {{
            // Position tooltip to the right and below cursor
            let x = mouseX + 20;
            let y = mouseY + 20;

            // Get tooltip dimensions
            const rect = tooltip.getBoundingClientRect();
            const tooltipWidth = rect.width || 250;
            const tooltipHeight = rect.height || 150;

            // Keep tooltip on screen
            if (x + tooltipWidth > window.innerWidth - 10) {{
                x = mouseX - tooltipWidth - 10;
            }}
            if (y + tooltipHeight > window.innerHeight - 10) {{
                y = mouseY - tooltipHeight - 10;
            }}

            tooltip.style.left = Math.max(10, x) + 'px';
            tooltip.style.top = Math.max(10, y) + 'px';
        }}
'''


# ==============================================================================
# Test Code
# ==============================================================================

if __name__ == "__main__":
    from game_state import create_test_game_state

    print("Testing visualization system...")

    # Create test game state
    game_state = create_test_game_state(board_size=10)

    # Generate HTML
    generator = HTMLGenerator()
    html = generator.generate_html(game_state)

    # Save to file
    output_path = "test_visualization.html"
    with open(output_path, 'w') as f:
        f.write(html)

    print(f"Generated visualization: {output_path}")
    print(f"HTML size: {len(html)} bytes")
    print("Open the HTML file in a browser to view the visualization.")
