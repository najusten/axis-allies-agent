"""
Scenario files: deterministic, hand-built game situations for rules checks.

A scenario is a YAML document:

    name: rear-armor-panzerfaust
    source: "Rulebook p.14; forum thread <url>"
    board:
      width: 10
      height: 10
      terrain: {forest: [[3,4],[3,5]], hill: [[6,6]]}
      edge_obstacles: [{a: [2,2], b: [3,2], type: barbed wire}]
    units:
      - {id: sherman, name: "M4A1 Sherman", owner: player1, at: [4,4], facing: W}
      - {id: pf, name: "Panzerfaust 30", owner: player2, at: [5,4], disrupted: true}
      - {id: custom, owner: player2, at: [5,5],
         stats: {unit_type: Soldier, defense: "4", speed: 2, per: [8,6,4], veh: [3,2,1],
                 abilities: "Close Assault 11"}}
    phase: assault            # deployment | movement | assault | flight | airstrike
    active_player: player2
    turn: 1
    dice: [6, 6, 6, 3]        # consumed in order by every d6 rolled
    actions:
      - {type: attack, unit: pf, target: sherman}
      - {type: move, unit: sherman, to: [3,4]}
      - {type: use_ability, unit: x, ability: "Smoke Screen", target: [4,4]}
      - {type: board, unit: rifles, transport: truck}
      - {type: dismount, unit: rifles, transport: truck, to: [5,5]}
      - {type: casualty}      # resolve the casualty phase
      - {type: end_phase}     # (only meaningful when run through a TurnController)
    expect:
      units:
        sherman: {status: damaged, health: 3, at: [4,4], alive: true}
      results:                # one entry per action, by index; partial matching
        0: {success: true, message_contains: "DAMAGED"}
      events:                 # every listed event must appear (subset match) somewhere
        - {type: attack, defense: 3}
      dice_consumed: 4

`units[].name` looks the unit up in the unit CSV; `stats` builds a synthetic
unit instead. Facing accepts 0-5 or E/SE/SW/W/NW/NE. `status` in expectations
is one of healthy | disrupted | damaged | destroyed.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from abilities import AbilitySystem
from action import (Action, MoveAction, AttackAction, UseAbilityAction,
                    BoardTransportAction, DismountTransportAction, PassAction)
from action_executor import ActionExecutor, ActionResult
from action_generator import ActionGenerator
from board import Board
from dice import DiceSystem, ScriptedDice
from game_setup import load_all_units
from game_state import GameState, GamePhase, UnitState
from initiative import InitiativeSystem
from movement import MovementSystem
from units import Unit


ABILITY_CSV = (
    'Axis_and_Allies_Unit_Data_for_Analysis_-_Special_Abilities.csv'
    if os.path.exists('Axis_and_Allies_Unit_Data_for_Analysis_-_Special_Abilities.csv')
    else 'Axis and Allies Unit Data for Analysis - Special_Abilities.csv'
)

# Screen compass names (flat-top board, see facing.SCREEN_NAMES) -> HexDirection index
FACING_NAMES = {'SE': 0, 'NE': 1, 'N': 2, 'NW': 3, 'SW': 4, 'S': 5}
PHASES = {
    'deployment': GamePhase.DEPLOYMENT, 'movement': GamePhase.MOVEMENT,
    'flight': GamePhase.FLIGHT, 'assault': GamePhase.ASSAULT,
    'airstrike': GamePhase.AIRSTRIKE,
}


# ----------------------------------------------------------------------
# Systems
# ----------------------------------------------------------------------

@dataclass
class Systems:
    ability: AbilitySystem
    movement: MovementSystem
    generator: ActionGenerator
    executor: ActionExecutor
    initiative: InitiativeSystem


_ABILITY_SYSTEM: Optional[AbilitySystem] = None


def build_systems(seed: Optional[int] = None, dice: Optional[DiceSystem] = None) -> Systems:
    """The standard wiring. One shared DiceSystem (seeded or scripted) drives everything."""
    global _ABILITY_SYSTEM
    if _ABILITY_SYSTEM is None:
        _ABILITY_SYSTEM = AbilitySystem(ABILITY_CSV)
    ability = _ABILITY_SYSTEM
    movement = MovementSystem(ability)
    executor = ActionExecutor(movement, None, ability, random_seed=seed, dice=dice)
    generator = ActionGenerator(movement, None, ability)
    initiative = InitiativeSystem(ability, movement, dice=executor.dice)
    return Systems(ability, movement, generator, executor, initiative)


# ----------------------------------------------------------------------
# Units
# ----------------------------------------------------------------------

_UNIT_CATALOG: Optional[Dict[str, Unit]] = None


def unit_catalog() -> Dict[str, Unit]:
    global _UNIT_CATALOG
    if _UNIT_CATALOG is None:
        _UNIT_CATALOG = {u.name: u for u in load_all_units()}
    return _UNIT_CATALOG


def make_unit(name: str, unit_type: str = 'Soldier', defense: Any = 4, speed: int = 2,
              per: Tuple[int, int, int] = (8, 6, 4), veh: Tuple[int, int, int] = (3, 2, 1),
              abilities: Any = '', nation: str = 'Test', year: int = 1942, cost: int = 10) -> Unit:
    """Synthetic unit for tests. `defense` may be 4 or "4/3"; abilities a list or comma string."""
    if isinstance(defense, int):
        defense = f"{defense}/{defense - 1}" if unit_type.startswith('Vehicle') else str(defense)
    if isinstance(abilities, (list, tuple)):
        abilities = ','.join(abilities)
    return Unit(name, nation, unit_type, year, cost, defense, speed,
                veh[0], veh[1], veh[2], per[0], per[1], per[2], abilities or '')


def unit_from_spec(spec: dict) -> Unit:
    """Build a Unit from a scenario unit entry (by CSV name or inline stats)."""
    import copy
    if 'stats' in spec:
        st = dict(spec['stats'])
        return make_unit(spec.get('name', spec.get('id', 'Unit')), **st)
    name = spec['name']
    catalog = unit_catalog()
    if name not in catalog:
        raise KeyError(f"Unknown unit name {name!r}")
    return copy.deepcopy(catalog[name])


# ----------------------------------------------------------------------
# Actions (shared with the server)
# ----------------------------------------------------------------------

def build_action(game_state: GameState, data: dict,
                 aliases: Optional[Dict[str, str]] = None) -> Optional[Action]:
    """
    Build an Action from a plain dict. Accepts both the scenario shorthand
    (unit/target/to/ability) and the server's wire format
    (unit_id/target_q/to_q...). `aliases` maps scenario ids to unit ids.
    """
    aliases = aliases or {}

    def uid(key):
        v = data.get(key)
        return aliases.get(v, v) if isinstance(v, str) else v

    def hexpair(key_pair, key_list):
        if data.get(key_list) is not None:
            q, r = data[key_list]
            return int(q), int(r)
        if data.get(key_pair[0]) is not None:
            return int(data[key_pair[0]]), int(data[key_pair[1]])
        return None

    unit_id = uid('unit') or uid('unit_id')
    if not unit_id:
        return None
    us = game_state.get_unit_state(unit_id)
    if not us:
        return None
    from_q, from_r = us.position
    kind = data['type']

    if kind == 'move':
        to = hexpair(('to_q', 'to_r'), 'to')
        action = MoveAction(unit_id, from_q, from_r, to[0], to[1],
                            path=[tuple(p) for p in data['path']] if data.get('path') else None)
        if game_state.current_phase == GamePhase.ASSAULT:
            if us.strike_and_fade_available:
                action.is_strike_and_fade = True
            else:
                action.is_relocate = True
        return action

    if kind == 'attack':
        target_id = uid('target') or uid('target_id')
        target_hex = hexpair(('target_q', 'target_r'), 'target_hex')
        if not target_id and target_hex:
            target_id = _unit_at(game_state, target_hex, prefer_enemy_of=us.owner)
        if not target_id:
            return None
        ts = game_state.get_unit_state(target_id)
        if not ts:
            return None
        tq, tr = ts.position
        dist = game_state.board.hex_distance(from_q, from_r, tq, tr)
        action = AttackAction(unit_id, from_q, from_r, target_id, tq, tr,
                              MovementSystem.get_range_category(dist), dist)
        # Special attack variants set a flag the executor checks, e.g.
        # special: rocket_salvo -> action.is_rocket_salvo = True
        if data.get('special'):
            setattr(action, f"is_{data['special']}", True)
        return action

    if kind in ('board', 'board_transport'):
        tid = uid('transport') or uid('transport_id')
        pos = hexpair(('pos_q', 'pos_r'), 'at') or us.position
        return BoardTransportAction(unit_id, tid, pos[0], pos[1])

    if kind == 'dismount':
        tid = uid('transport') or uid('transport_id')
        to = hexpair(('to_q', 'to_r'), 'to')
        return DismountTransportAction(unit_id, tid, to[0], to[1])

    if kind == 'use_ability':
        target_id = uid('target') if isinstance(data.get('target'), str) else uid('target_id')
        target_hex = None
        if isinstance(data.get('target'), (list, tuple)):
            target_hex = (int(data['target'][0]), int(data['target'][1]))
        else:
            target_hex = hexpair(('target_q', 'target_r'), 'target_hex')
        params = dict(data.get('parameters') or {})
        if data.get('facing') is not None:
            params['new_facing'] = parse_facing(data['facing'])
        return UseAbilityAction(unit_id, data.get('ability') or data.get('ability_name'),
                                target_id=target_id,
                                target_q=target_hex[0] if target_hex else None,
                                target_r=target_hex[1] if target_hex else None,
                                parameters=params or None)

    if kind == 'pass':
        return PassAction(unit_id)

    return None


SPECIAL_FLAGS = ('is_rocket_salvo', 'is_rockets_8', 'is_top_mounted_rockets', 'is_bombs',
                 'is_remote_control', 'is_additional_hull_cannon', 'is_extra_hull_cannon',
                 'is_strike_and_fade', 'is_relocate')


def find_legal_action(legal: List[Action], data: dict,
                      aliases: Optional[Dict[str, str]] = None) -> Optional[Action]:
    """
    Pick the generator-produced action that a request describes, so that
    flags the generator computed (has_los, indirect_fire, special attack
    kinds, movement paths) are trusted rather than re-derived from the
    request. Returns None when the request matches nothing legal.
    """
    aliases = aliases or {}
    ali = lambda v: aliases.get(v, v) if isinstance(v, str) else v
    kind = data.get('type')
    unit_id = ali(data.get('unit') or data.get('unit_id'))
    special = data.get('special')

    def dest():
        if data.get('to') is not None:
            return int(data['to'][0]), int(data['to'][1])
        if data.get('to_q') is not None:
            return int(data['to_q']), int(data['to_r'])
        return None

    for a in legal:
        if getattr(a, 'unit_id', None) != unit_id:
            continue
        if kind == 'move' and isinstance(a, MoveAction):
            if (a.to_q, a.to_r) == dest():
                return a
        elif kind == 'attack' and isinstance(a, AttackAction):
            target_id = ali(data.get('target') or data.get('target_id'))
            if target_id and a.target_id != target_id:
                continue
            if not target_id and data.get('target_q') is not None and \
                    (a.target_q, a.target_r) != (int(data['target_q']), int(data['target_r'])):
                continue
            flags = [f for f in SPECIAL_FLAGS if getattr(a, f, False) and f not in ('is_strike_and_fade', 'is_relocate')]
            if special:
                if f"is_{special}" not in flags:
                    continue
            elif flags or getattr(a, 'improvised_attack', None):
                continue    # plain attack requested; skip special variants
            return a
        elif kind in ('board', 'board_transport') and isinstance(a, BoardTransportAction):
            tid = ali(data.get('transport') or data.get('transport_id'))
            if not tid or a.transport_id == tid:
                return a
        elif kind == 'dismount' and isinstance(a, DismountTransportAction):
            tid = ali(data.get('transport') or data.get('transport_id'))
            if (not tid or a.transport_id == tid) and (a.to_q, a.to_r) == dest():
                return a
        elif kind == 'use_ability' and isinstance(a, UseAbilityAction):
            name = data.get('ability') or data.get('ability_name')
            if a.ability_name != name:
                continue
            tid = ali(data.get('target_id') if data.get('target_id') else
                      (data.get('target') if isinstance(data.get('target'), str) else None))
            if tid and a.target_id != tid:
                continue
            thex = None
            if isinstance(data.get('target'), (list, tuple)):
                thex = (int(data['target'][0]), int(data['target'][1]))
            elif data.get('target_q') is not None:
                thex = (int(data['target_q']), int(data['target_r']))
            if thex and (a.target_q, a.target_r) != thex:
                continue
            params = data.get('parameters') or {}
            if data.get('facing') is not None:
                params = {**params, 'new_facing': parse_facing(data['facing'])}
            if params and (a.parameters or {}) != params:
                continue
            return a
    return None


def _unit_at(game_state: GameState, pos: Tuple[int, int], prefer_enemy_of: str = None) -> Optional[str]:
    first = None
    for uid, us in game_state.units.items():
        if us.is_alive and tuple(us.position) == tuple(pos):
            if prefer_enemy_of and us.owner != prefer_enemy_of:
                return uid
            first = first or uid
    return first


def parse_facing(value) -> int:
    if isinstance(value, str):
        return FACING_NAMES[value.upper()]
    return int(value)


# ----------------------------------------------------------------------
# Scenario
# ----------------------------------------------------------------------

@dataclass
class StepResult:
    index: int
    spec: dict
    action: Optional[Action]
    result: Optional[ActionResult]
    events: List[dict] = field(default_factory=list)


@dataclass
class Scenario:
    name: str
    source: str
    game_state: GameState
    systems: Systems
    dice: ScriptedDice
    aliases: Dict[str, str]          # scenario id -> unit id
    actions: List[dict]
    expect: dict
    raw: dict

    # -- running --------------------------------------------------------

    def run(self) -> List[StepResult]:
        steps: List[StepResult] = []
        gs = self.game_state
        ex = self.systems.executor
        for i, spec in enumerate(self.actions):
            kind = spec.get('type')
            if kind == 'casualty':
                res = ex.resolve_casualty_phase(gs)
                steps.append(StepResult(i, spec, None, None, [{'type': 'casualty', **res}]))
                continue
            if kind == 'set_phase':
                gs.current_phase = PHASES[spec['phase']]
                if spec.get('active_player'):
                    gs.active_player = spec['active_player']
                if spec.get('reset_defensive_fire', True) and gs.current_phase == GamePhase.MOVEMENT:
                    ex.reset_defensive_fire_phase(gs)
                steps.append(StepResult(i, spec, None, None, [{'type': 'phase', 'phase': spec['phase']}]))
                continue
            if kind == 'set_facing':
                us = gs.get_unit_state(self.aliases.get(spec['unit'], spec['unit']))
                us.facing = parse_facing(spec['facing'])
                steps.append(StepResult(i, spec, None, None, []))
                continue
            if kind == 'new_turn':
                for us in gs.units.values():
                    us.reset_for_turn()
                gs.smoke_screens.clear()
                gs.turn_number += 1
                steps.append(StepResult(i, spec, None, None, [{'type': 'turn_start', 'turn': gs.turn_number}]))
                continue
            if spec.get('active_player'):
                gs.active_player = spec['active_player']
            if spec.get('force'):
                action = build_action(gs, spec, self.aliases)
            else:
                player = spec.get('active_player') or gs.active_player
                action = find_legal_action(self.legal_actions(player), spec, self.aliases)
                if action is None:
                    # Not offered by the generator: record a failed step so
                    # expectations can assert on it (results: {i: {success: false}}).
                    built = build_action(gs, spec, self.aliases)
                    msg = "Not a legal action" if built is not None else f"could not build action: {spec}"
                    steps.append(StepResult(i, spec, built, ActionResult(False, msg), []))
                    continue
            result = ex.execute_action(gs, action)
            steps.append(StepResult(i, spec, action, result, list(result.events)))
        return steps

    def legal_actions(self, player: Optional[str] = None) -> List[Action]:
        player = player or self.game_state.active_player
        return self.systems.generator.get_all_legal_actions(self.game_state, player)

    # -- checking ---------------------------------------------------------

    def check(self, steps: List[StepResult]) -> List[str]:
        """Return a list of failure messages (empty = all expectations met)."""
        failures: List[str] = []
        gs = self.game_state
        exp = self.expect or {}

        for alias, want in (exp.get('units') or {}).items():
            uid = self.aliases.get(alias, alias)
            us = gs.units.get(uid)
            if us is None:
                # Destroyed units are removed from the state entirely
                for key, value in want.items():
                    got = {'status': 'destroyed', 'alive': False}.get(key, '<removed>')
                    if got != value:
                        failures.append(f"unit {alias!r}: removed (destroyed); expected {key}={value!r}")
                continue
            for key, value in want.items():
                got = _unit_attr(gs, us, key)
                if key == 'at':
                    got, value = list(got), list(value)
                if got != value:
                    failures.append(f"unit {alias!r}: expected {key}={value!r}, got {got!r}")

        for idx, want in (exp.get('results') or {}).items():
            idx = int(idx)
            if idx >= len(steps) or steps[idx].result is None:
                failures.append(f"result #{idx}: no action result")
                continue
            res = steps[idx].result
            if 'success' in want and bool(res.success) != bool(want['success']):
                failures.append(f"result #{idx}: expected success={want['success']}, got {res.success} ({res.message})")
            if 'message_contains' in want and want['message_contains'] not in res.message:
                failures.append(f"result #{idx}: message {res.message!r} does not contain {want['message_contains']!r}")
            for key in ('hits',):
                if key in want and getattr(res, key) != want[key]:
                    failures.append(f"result #{idx}: expected {key}={want[key]}, got {getattr(res, key)}")
            for key, value in (want.get('combat') or {}).items():
                got = res.combat_details.get(key)
                if got != value:
                    failures.append(f"result #{idx}: combat.{key} expected {value!r}, got {got!r}")

        all_events = [e for s in steps for e in s.events]
        for want in (exp.get('events') or []):
            want = _resolve_aliases(want, self.aliases)
            if not any(_subset(want, e) for e in all_events):
                failures.append(f"no event matching {want!r} (events: {[e.get('type') for e in all_events]})")

        for want in (exp.get('no_events') or []):
            want = _resolve_aliases(want, self.aliases)
            if any(_subset(want, e) for e in all_events):
                failures.append(f"unexpected event matching {want!r}")

        if 'dice_consumed' in exp and len(self.dice.log) != exp['dice_consumed']:
            failures.append(f"expected {exp['dice_consumed']} dice consumed, got {len(self.dice.log)}: {self.dice.log}")

        if 'legal' in exp:
            for spec in exp['legal']:
                player = spec.get('player') or gs.active_player
                legal = self.legal_actions(player)
                matches = [a for a in legal if _action_matches(a, spec, self.aliases)]
                should = spec.get('allowed', True)
                if should and not matches:
                    failures.append(f"expected legal action {spec!r} not offered")
                if not should and matches:
                    failures.append(f"action {spec!r} should not be legal but was offered: {matches[0]}")
        return failures


def _unit_attr(gs: GameState, us: UnitState, key: str):
    if key == 'status':
        if not us.is_alive:
            return 'destroyed'
        if us.is_disrupted and us.is_damaged:
            return 'disrupted+damaged'
        if us.is_damaged:
            return 'damaged'
        if us.is_disrupted:
            return 'disrupted'
        return 'healthy'
    if key == 'health':
        return us.current_health
    if key == 'at':
        return us.position
    if key == 'alive':
        return us.is_alive
    if key == 'facing':
        return us.facing
    if key == 'pending':
        ph = gs.pending_hits.get(us.unit.id)
        return [c.counter_type.value for c in ph.counters if not c.face_up] if ph else []
    return getattr(us, key)


def _subset(want: dict, have: dict) -> bool:
    for k, v in want.items():
        if k not in have:
            return False
        if isinstance(v, dict) and isinstance(have[k], dict):
            if not _subset(v, have[k]):
                return False
        elif isinstance(v, list) and isinstance(have[k], (list, tuple)):
            if list(v) != list(have[k]):
                return False
        elif have[k] != v:
            return False
    return True


def _resolve_aliases(d: dict, aliases: Dict[str, str]) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, str) and v in aliases:
            v = aliases[v]
        out[k] = v
    return out


def _action_matches(action: Action, spec: dict, aliases: Dict[str, str]) -> bool:
    kind = spec.get('type')
    unit = aliases.get(spec.get('unit'), spec.get('unit'))
    if unit and getattr(action, 'unit_id', None) != unit:
        return False
    if kind == 'move':
        if not isinstance(action, MoveAction):
            return False
        if 'to' in spec and [action.to_q, action.to_r] != list(spec['to']):
            return False
        return True
    if kind == 'attack':
        if not isinstance(action, AttackAction):
            return False
        target = aliases.get(spec.get('target'), spec.get('target'))
        return not target or action.target_id == target
    if kind == 'use_ability':
        return isinstance(action, UseAbilityAction) and (
            not spec.get('ability') or action.ability_name == spec['ability'])
    if kind == 'board':
        return isinstance(action, BoardTransportAction)
    if kind == 'dismount':
        return isinstance(action, DismountTransportAction)
    return type(action).__name__.lower().startswith(str(kind).lower())


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------

def load_scenario(path: str) -> Scenario:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return scenario_from_dict(raw, name=raw.get('name') or os.path.basename(path))


def scenario_from_dict(raw: dict, name: str = 'scenario') -> Scenario:
    board_spec = raw.get('board') or {}
    board = Board(int(board_spec.get('width', 10)), int(board_spec.get('height', 10)))
    for terrain, hexes in (board_spec.get('terrain') or {}).items():
        for q, r in hexes:
            board.set_terrain(int(q), int(r), terrain)
    for eo in (board_spec.get('edge_obstacles') or []):
        a, b = eo['a'], eo['b']
        board.add_edge_obstacle(int(a[0]), int(a[1]), int(b[0]), int(b[1]), eo['type'])

    objective = raw.get('objective')
    game_state = GameState(board, objective_position=tuple(objective) if objective else None)
    game_state.current_phase = PHASES[raw.get('phase', 'movement')]
    game_state.active_player = raw.get('active_player', 'player1')
    game_state.turn_number = int(raw.get('turn', 1))

    aliases: Dict[str, str] = {}
    for spec in raw.get('units') or []:
        unit = unit_from_spec(spec)
        alias = spec.get('id') or unit.name
        unit.id = alias if alias not in aliases else f"{alias}_{len(aliases)}"
        q, r = spec['at']
        health = spec.get('health', unit.defense_front)
        us = UnitState(unit, (int(q), int(r)), spec['owner'], health)
        if spec.get('facing') is not None:
            us.facing = parse_facing(spec['facing'])
        us.is_disrupted = bool(spec.get('disrupted', False))
        us.is_damaged = bool(spec.get('damaged', False))
        us.has_moved = bool(spec.get('has_moved', False))
        us.has_attacked = bool(spec.get('has_attacked', False))
        if spec.get('in_transport'):
            us.carried_by_id = spec['in_transport']
        # Any other UnitState flag can be set directly (hold_defensive_fire, is_deployed, ...)
        reserved = {'id', 'name', 'owner', 'at', 'stats', 'facing', 'health', 'disrupted', 'damaged',
                    'has_moved', 'has_attacked', 'in_transport'}
        for key, value in spec.items():
            if key not in reserved and hasattr(us, key):
                setattr(us, key, value)
        game_state.add_unit(us)
        aliases[alias] = unit.id

    # Resolve transport links after all units exist
    for us in game_state.units.values():
        if us.carried_by_id:
            tid = aliases.get(us.carried_by_id, us.carried_by_id)
            us.carried_by_id = tid
            carrier = game_state.get_unit_state(tid)
            if carrier:
                carrier.carried_unit_id = us.unit.id

    for q, r in (raw.get('smoke') or []):
        game_state.add_smoke(int(q), int(r))

    dice = ScriptedDice(list(raw.get('dice') or []), random_seed=int(raw.get('seed', 0)))
    systems = build_systems(dice=dice)
    if game_state.current_phase == GamePhase.MOVEMENT:
        systems.executor.reset_defensive_fire_phase(game_state)
    game_state.rng_seed = int(raw.get('seed', 0))

    return Scenario(
        name=name, source=raw.get('source', ''), game_state=game_state, systems=systems,
        dice=dice, aliases=aliases, actions=list(raw.get('actions') or []),
        expect=raw.get('expect') or {}, raw=raw,
    )


def run_scenario_file(path: str) -> Tuple[Scenario, List[StepResult], List[str]]:
    sc = load_scenario(path)
    steps = sc.run()
    return sc, steps, sc.check(steps)


if __name__ == '__main__':
    import sys
    ok = True
    for p in sys.argv[1:]:
        sc, steps, failures = run_scenario_file(p)
        status = "PASS" if not failures else "FAIL"
        print(f"[{status}] {sc.name}")
        for s in steps:
            if s.result is not None:
                print(f"    #{s.index} {'✓' if s.result.success else '✗'} {s.result.message}")
        for f in failures:
            print(f"    ! {f}")
        ok = ok and not failures
    sys.exit(0 if ok else 1)
