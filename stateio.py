"""
Save / load / report: a whole game written as a scenario file.

A saved game is the scenario YAML format (see scenario.py) plus a `meta` block
(mode, AI, seed, setup options) and a `controller` block (where in the turn the
game is). That means one file does three jobs:

- Save game / Load game: the server rebuilds the session from it and resumes in
  the same phase with the same player to act.
- Report this moment: the same file plus the player's note and the recent log,
  written to reports/. It reproduces the exact position, and dropping it into
  scenarios/ with an `expect:` block turns the report into a regression test.
- Plain scenario: `python3 scenario.py reports/<file>.yaml` loads it like any
  other scenario.

Coordinates are axial (the engine's own), so nothing is lost in conversion.
"""

import dataclasses
import datetime
import os
import re
from typing import Any, Dict, List, Optional

from game_state import GameState, UnitState

FORMAT = 'aam-save/1'
ROOT = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(ROOT, 'saves')
REPORT_DIR = os.path.join(ROOT, 'reports')

# UnitState fields written explicitly (or not at all)
_BASIC = {'unit', 'position', 'owner', 'current_health', 'facing', 'is_disrupted', 'is_damaged',
          'carried_by_id', 'carried_unit_id', 'has_moved', 'has_attacked'}


def _plain(v):
    if isinstance(v, (set, frozenset)):
        return sorted(_plain(x) for x in v)
    if isinstance(v, tuple):
        return [_plain(x) for x in v]
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    return v


def _unit_spec(us: UnitState, catalog: Dict[str, Any]) -> dict:
    u = us.unit
    spec: Dict[str, Any] = {'id': u.id, 'owner': us.owner, 'at': list(us.position)}
    same = catalog.get(u.name)
    if same is not None and _same_stats(same, u):
        spec['name'] = u.name
    else:
        spec['name'] = u.name
        d = f"{u.defense_front}/{u.defense_rear}" if u.defense_front is not None else '-'
        spec['stats'] = {
            'unit_type': u.unit_type, 'defense': d, 'speed': u.speed,
            'per': [u.per_short, u.per_medium, u.per_long], 'veh': [u.veh_short, u.veh_medium, u.veh_long],
            'abilities': ', '.join(u.abilities or []), 'nation': u.nation, 'year': u.year or 1942,
            'cost': u.cost,
        }
    if us.facing is not None:
        spec['facing'] = us.facing
    if us.current_health != (u.defense_front or 1):
        spec['health'] = us.current_health
    for flag, key in (('is_disrupted', 'disrupted'), ('is_damaged', 'damaged'),
                      ('has_moved', 'has_moved'), ('has_attacked', 'has_attacked')):
        if getattr(us, flag):
            spec[key] = True
    if us.carried_by_id:
        spec['in_transport'] = us.carried_by_id
    for f in dataclasses.fields(UnitState):
        if f.name in _BASIC:
            continue
        value = getattr(us, f.name)
        default = f.default if f.default is not dataclasses.MISSING else (
            f.default_factory() if f.default_factory is not dataclasses.MISSING else None)
        if value != default:
            spec[f.name] = _plain(value)
    return spec


def _same_stats(a, b) -> bool:
    keys = ('unit_type', 'defense_front', 'defense_rear', 'speed', 'per_short', 'per_medium', 'per_long',
            'veh_short', 'veh_medium', 'veh_long', 'nation', 'cost')
    return all(getattr(a, k, None) == getattr(b, k, None) for k in keys) and \
        list(a.abilities or []) == list(b.abilities or [])


def export_state(gs: GameState, controller=None, meta: Optional[dict] = None,
                 note: str = '', log: Optional[List[str]] = None, name: str = '') -> dict:
    """The complete game as a scenario-format dict."""
    from scenario import unit_catalog
    catalog = unit_catalog()
    b = gs.board
    terrain: Dict[str, list] = {}
    loose_roads = []
    linked = b._linked_road_hexes() if hasattr(b, '_linked_road_hexes') else set()
    for (q, r), h in sorted(b.hexes.items()):
        if h.terrain != 'open':
            terrain.setdefault(h.terrain, []).append([q, r])
        if h.road and (q, r) not in linked:
            loose_roads.append([q, r])
    board = {
        'width': b.width, 'height': b.height, 'terrain': terrain,
        'road_links': [sorted([list(p) for p in e]) for e in sorted(b.road_edges, key=lambda e: sorted(e))],
        'edge_obstacles': [{'a': list(sorted(k)[0]), 'b': list(sorted(k)[1]), 'type': v}
                           for k, v in sorted(b.edge_obstacles.items(), key=lambda kv: sorted(kv[0]))],
    }
    if loose_roads:
        board['roads'] = loose_roads
    units = [_unit_spec(us, catalog) for us in gs.units.values() if us.is_alive]
    for spec in units:
        ph = gs.pending_hits.get(spec['id'])
        if ph and ph.counters:
            spec['pending_counters'] = [c.counter_type.value for c in ph.counters if not c.face_up]
    out: Dict[str, Any] = {
        'format': FORMAT,
        'name': name or f"saved game, turn {gs.turn_number}",
        'saved': datetime.datetime.now().isoformat(timespec='seconds'),
        'source': note or 'saved game',
        'coords': 'axial',
        'board': board,
        'objective': list(gs.objective_position) if gs.objective_position else None,
        'turn': gs.turn_number,
        'phase': gs.current_phase if isinstance(gs.current_phase, str) else str(gs.current_phase),
        'active_player': gs.active_player,
        'units': units,
        'smoke': [list(h) for h in sorted(gs.smoke_screens)] if gs.smoke_screens else [],
        'defensive_fire_used': sorted(gs.defensive_fire_used),
        'face_up_disrupted': sorted(gs.face_up_disrupted),
        'seed': int(getattr(gs, 'rng_seed', 0) or 0),
        'actions': [],
    }
    if meta:
        out['meta'] = _plain(meta)
    if controller is not None:
        out['controller'] = {
            'phase_queue': [list(p) for p in controller.phase_queue],
            'phase_idx': controller.phase_idx,
            'turn_order': list(controller.turn_order),
            'initiative_winner': getattr(controller, 'initiative_winner', None),
            'vanguard_moved': sorted(controller._vanguard_moved),
            'deploy_winner': getattr(controller, 'deploy_winner', None),
        }
    if note:
        out['note'] = note
    if log:
        out['log'] = list(log)
    return out


def dump_yaml(data: dict) -> str:
    import yaml

    class _Flow(yaml.SafeDumper):
        def ignore_aliases(self, data):      # never write &anchors / *aliases
            return True

    def _list(dumper, value):
        # short lists of numbers/strings on one line (coordinates, dice), others as blocks
        scalars = all(isinstance(x, (int, float, str)) and len(str(x)) < 30 for x in value)
        coords = all(isinstance(x, list) and len(x) <= 3 and
                     all(isinstance(y, (int, float)) or (isinstance(y, list) and len(y) <= 3) for y in x)
                     for x in value)
        flow = bool(value) and ((scalars and len(value) <= 12) or coords)
        return dumper.represent_sequence('tag:yaml.org,2002:seq', value, flow_style=flow)
    _Flow.add_representer(list, _list)
    header = ("# Axis & Allies Miniatures — saved game / report.\n"
              "# Load it from the game (Load), or run it as a scenario: python3 scenario.py <this file>.\n"
              "# To turn a report into a regression test: copy it into scenarios/, add `actions:` and `expect:`.\n")
    return header + yaml.dump(data, Dumper=_Flow, sort_keys=False, allow_unicode=True, width=120)


def slug(text: str) -> str:
    s = re.sub(r'[^a-zA-Z0-9]+', '-', text or '').strip('-').lower()
    return s[:40] or 'game'


def write_file(folder: str, stem: str, data: dict) -> str:
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    path = os.path.join(folder, f"{stamp}-{slug(stem)}.yaml")
    with open(path, 'w') as fh:
        fh.write(dump_yaml(data))
    return path


def list_files(folder: str) -> List[dict]:
    import yaml
    out = []
    if not os.path.isdir(folder):
        return out
    for fn in sorted(os.listdir(folder), reverse=True):
        if not fn.endswith('.yaml'):
            continue
        try:
            with open(os.path.join(folder, fn)) as fh:
                raw = yaml.safe_load(fh) or {}
        except Exception:
            continue
        out.append({'file': fn, 'name': raw.get('name', fn), 'saved': raw.get('saved'),
                    'turn': raw.get('turn'), 'note': raw.get('note', ''),
                    'mode': (raw.get('meta') or {}).get('mode')})
    return out
