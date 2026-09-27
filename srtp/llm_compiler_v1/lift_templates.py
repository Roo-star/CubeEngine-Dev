"""Engine-owned Spatial Lift templates: choose parameters, the engine rewrites Rule IR.

Most 2D→3D lifts are one of a few transforms: add an axis, resize existing
axes, and keep counts. Runtime functions (sites, neighbors, has_line,
flood_region, count_*) are already rank-generic, so a Rule written with them
lifts without semantic rewriting. The engine therefore rewrites only what is
rank- or size-specific and reports every change:

* the topology axes (added / resized);
* literal coordinates in coordinate positions (padded with 0 on new axes);
  a literal "set every cell" block becomes one foreach over topology.sites;
* integer constants derived from the site count: the total site count S and
  S - K, where K is a count sampled from the sites (e.g. mines), become the
  expressions count(topology.sites) and count(sites) - K, so they stay true
  on any board, including the Z=1 collapse. K is kept, or with
  ``count_policy=scale`` becomes max(1, K * count(sites) // S).

Anything else rank-specific (e.g. a coordinate built as [x + 1, y]) is a
residual: the template does not guess, and the model repairs only that part.
Relative movement (vector.add, rays, coordinate-valued state) is also a
residual unless the intent says movement stays planar: whether a piece can move
along the new axis, and with which controls, is a design decision.
Verification is executable: the Target collapsed to one layer must replay
like the Source (resized first when the designer changed X/Y).
"""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

LIFT_TEMPLATE_VERSION = "cubeengine.srtp/lift-template/1.0"
_COUNT_POLICIES = ("keep", "scale")
_COMPARISONS = {"eq", "ne", "lt", "lte", "gt", "gte"}


@dataclass
class LiftTemplate:
    topology: str
    set_extents: Dict[str, int] = field(default_factory=dict)
    add_axes: List[Dict[str, Any]] = field(default_factory=list)
    count_policy: str = "keep"
    movement: Optional[str] = None  # "planar": relative movement stays in its layer

    def to_mapping(self) -> Dict[str, Any]:
        return {"template_version": LIFT_TEMPLATE_VERSION, "topology": self.topology,
                "set_extents": dict(self.set_extents), "add_axes": deepcopy(self.add_axes),
                "count_policy": self.count_policy, "movement": self.movement}

    def resize_only(self) -> "LiftTemplate":
        return LiftTemplate(self.topology, dict(self.set_extents), [], self.count_policy, "planar")


@dataclass
class LiftResult:
    rule: Dict[str, Any]
    template: LiftTemplate
    transforms: List[str] = field(default_factory=list)
    replaced: List[Dict[str, Any]] = field(default_factory=list)
    padded: List[str] = field(default_factory=list)
    residual: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.residual

    def report(self) -> Dict[str, Any]:
        return {"template": self.template.to_mapping(), "transforms": list(self.transforms),
                "replaced_constants": deepcopy(self.replaced), "padded_coordinates": list(self.padded),
                "residual": list(self.residual), "warnings": list(self.warnings)}


# ------------------------------------------------------------------ templates

def template_from_changes(
    changes: Sequence[Any], source_rule: Mapping[str, Any], scene: Optional[Mapping[str, Any]] = None,
) -> Tuple[Optional[LiftTemplate], List[str]]:
    """Structured Design Intent changes → template ({kind:set_extent,axis:x|y|z,value})."""
    extents = [item for item in changes or [] if isinstance(item, Mapping) and item.get("kind") == "set_extent"]
    if not extents:
        return None, ["no structured set_extent change; the template needs explicit target dimensions"]
    topology, problems = _grid_topology(source_rule)
    if topology is None:
        return None, problems
    world = _world_axes(topology, scene)
    template = LiftTemplate(str(topology["id"]))
    names = [str(axis["name"]) for axis in topology["axes"]]
    for item in extents:
        axis, value = item.get("axis"), item.get("value")
        if axis not in ("x", "y", "z") or isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 256:
            problems.append("set_extent needs axis x|y|z and an integer value 1..256: {0}".format(dict(item)))
            continue
        if axis in world:
            name = names[world[axis]]
            if topology["axes"][world[axis]].get("extent") != value:
                template.set_extents[name] = value
        elif value >= 2:
            template.add_axes.append({"name": _fresh_axis(axis, names), "extent": value, "boundary": "bounded"})
    policy = next((item.get("value") for item in changes or []
                   if isinstance(item, Mapping) and item.get("kind") == "count_policy"), "keep")
    if policy not in _COUNT_POLICIES:
        problems.append("count_policy must be keep or scale")
    template.count_policy = policy if policy in _COUNT_POLICIES else "keep"
    movement = next((item.get("value") for item in changes or []
                     if isinstance(item, Mapping) and item.get("kind") == "movement"), None)
    template.movement = "planar" if movement == "planar" else None
    if not template.add_axes and not template.set_extents:
        problems.append("the requested dimensions equal the Source; nothing to lift")
    return (template if not problems else None), problems


def template_from_plan(plan: Mapping[str, Any], source_rule: Mapping[str, Any]) -> Tuple[Optional[LiftTemplate], List[str]]:
    """Planner reply (topology.add_axes, optional set_extents/count_policy) → template."""
    topology_plan = plan.get("topology") if isinstance(plan.get("topology"), Mapping) else {}
    topology, problems = _grid_topology(source_rule, str(topology_plan.get("id") or "") or None)
    if topology is None:
        return None, problems
    names = [str(axis["name"]) for axis in topology["axes"]]
    template = LiftTemplate(str(topology["id"]))
    for axis in topology_plan.get("add_axes") or []:
        extent = axis.get("extent") if isinstance(axis, Mapping) else None
        name = str(axis.get("name") or "") if isinstance(axis, Mapping) else ""
        if not name or name in names or isinstance(extent, bool) or not isinstance(extent, int) or extent < 2:
            problems.append("add_axes entries need a new axis name and an integer extent >= 2")
            continue
        template.add_axes.append({"name": name, "extent": extent, "boundary": str(axis.get("boundary") or "bounded")})
    for name, extent in (topology_plan.get("set_extents") or {}).items():
        if name not in names or isinstance(extent, bool) or not isinstance(extent, int) or extent < 1:
            problems.append("set_extents must map existing axis names to positive integers")
            continue
        template.set_extents[str(name)] = extent
    policy = plan.get("count_policy", "keep")
    if policy not in _COUNT_POLICIES:
        problems.append("count_policy must be keep or scale")
    template.count_policy = policy if policy in _COUNT_POLICIES else "keep"
    template.movement = "planar" if plan.get("movement_policy") == "planar" else None
    if not template.add_axes:
        problems.append("the plan adds no axis")
    return (template if not problems else None), problems


# ------------------------------------------------------------------ transform

def apply_lift_template(source_rule: Mapping[str, Any], template: LiftTemplate) -> LiftResult:
    rule = deepcopy(dict(source_rule))
    result = LiftResult(rule, template)
    topology = next((item for item in rule.get("topologies") or []
                     if isinstance(item, Mapping) and item.get("id") == template.topology), None)
    if topology is None:
        result.residual.append("topology {0} not found".format(template.topology))
        return result
    old_extents = [int(axis["extent"]) for axis in topology["axes"]]
    names = [str(axis["name"]) for axis in topology["axes"]]
    for name, extent in template.set_extents.items():
        index = names.index(name)
        topology["axes"][index]["extent"] = extent
        result.transforms.append("resize axis {0}: {1} -> {2}".format(name, old_extents[index], extent))
    for axis in template.add_axes:
        topology["axes"].append({"name": axis["name"], "extent": axis["extent"], "boundary": axis.get("boundary", "bounded")})
        result.transforms.append("add axis {0} with extent {1}".format(axis["name"], axis["extent"]))
    new_extents = [int(axis["extent"]) for axis in topology["axes"]]
    if template.add_axes and template.movement != "planar":
        movement = _movement_paths(rule)
        if movement:
            result.residual.append(
                "relative movement at {0}: whether it extends along the new axis (new directions and controls) is a "
                "design decision; the intent must say so, or keep it planar with movement=planar".format(movement[:6]))
    context = _Context(rule, template, old_extents, new_extents, result)
    context.fill_blocks(rule, "")
    context.walk(rule, "")
    context.counts()
    if len([t for t in rule.get("topologies") or [] if isinstance(t, Mapping)]) > 1:
        result.warnings.append("the Rule has several topologies; only {0} was lifted".format(template.topology))
    from srtp.ir_v2 import seal_rule_ir
    result.rule = seal_rule_ir(rule, revision=int(rule.get("revision") or 0))
    return result


class _Context:
    def __init__(self, rule, template, old_extents, new_extents, result):
        from srtp.ir_v2.runtime import _core_functions
        self.rule, self.template, self.result = rule, template, result
        self.old_extents, self.new_extents = old_extents, new_extents
        self.pad = len(new_extents) - len(old_extents)
        self.functions = {name: spec.argument_types for name, spec in _core_functions(None).items()}
        variables = (rule.get("state") or {}).get("variables") or []
        self.grid_states = {str(v.get("id")) for v in variables if isinstance(v, Mapping)
                            and v.get("scope") == "topology_site" and v.get("topology") == template.topology}
        self.coord_states = {str(v.get("id")) for v in variables if isinstance(v, Mapping) and v.get("type") == "core:coord"}

    # -- literal "every cell" blocks ------------------------------------------------
    def fill_blocks(self, node: Any, path: str) -> None:
        if isinstance(node, list):
            if node and all(isinstance(item, Mapping) and "op" in item for item in node):
                self._fold_fill(node, path)
            for index, item in enumerate(node):
                self.fill_blocks(item, "{0}/{1}".format(path, index))
        elif isinstance(node, Mapping):
            for key, value in node.items():
                self.fill_blocks(value, "{0}/{1}".format(path, key))

    def _fold_fill(self, commands: List[Any], path: str) -> None:
        sites = set(product(*(range(extent) for extent in self.old_extents)))
        groups: Dict[Tuple[str, str], List[int]] = {}
        for index, command in enumerate(commands):
            if (isinstance(command, Mapping) and command.get("op") == "grid.set" and command.get("state") in self.grid_states
                    and _literal_coordinate(command.get("coordinate"), len(self.old_extents)) is not None
                    and isinstance(command.get("value"), Mapping) and command["value"].get("op") == "literal"):
                key = (str(command["state"]), repr(command["value"].get("value")))
                groups.setdefault(key, []).append(index)
        removed = set()
        for (state, _), indexes in groups.items():
            covered = {_literal_coordinate(commands[i]["coordinate"], len(self.old_extents)) for i in indexes}
            if covered != sites or len(indexes) != len(sites):
                continue
            first = commands[indexes[0]]
            loop = {"op": "foreach", "query": {"op": "call", "function": "core:topology.sites",
                                               "args": [{"op": "literal", "value": self.template.topology}]},
                    "as": "lift_site", "effects": [dict({k: deepcopy(v) for k, v in first.items() if k != "coordinate"},
                                                         coordinate={"op": "var", "name": "lift_site"})]}
            commands[indexes[0]] = loop
            removed.update(indexes[1:])
            self.result.transforms.append("{0}: {1} literal grid.set on every site of {2} -> foreach over topology.sites".format(
                path, len(indexes), state))
        for index in sorted(removed, reverse=True):
            del commands[index]

    # -- coordinates -------------------------------------------------------------
    def walk(self, node: Any, path: str) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                self.walk(item, "{0}/{1}".format(path, index))
            return
        if not isinstance(node, dict):
            return
        op = node.get("op")
        if op == "call" and isinstance(node.get("args"), list):
            types = self.functions.get(str(node.get("function")), ())
            for index, arg in enumerate(node["args"]):
                if index < len(types) and types[index] == "core:coord" and self._topology_call(node):
                    node["args"][index] = self.coordinate(arg, "{0}/args/{1}".format(path, index))
        if op in ("grid.set", "grid.toggle") and node.get("state") in self.grid_states and "coordinate" in node:
            node["coordinate"] = self.coordinate(node["coordinate"], path + "/coordinate")
        if op == "entity.spawn" and "at" in node:
            node["at"] = self.coordinate(node["at"], path + "/at")
        if node.get("type") == "core:coord":
            for key in ("initial", "default", "domain"):
                if key in node:
                    node[key] = self.coordinate(node[key], "{0}/{1}".format(path, key))
        for key, value in node.items():
            self.walk(value, "{0}/{1}".format(path, key))

    def _topology_call(self, node: Mapping[str, Any]) -> bool:
        first = (node.get("args") or [None])[0]
        literal = first.get("value") if isinstance(first, Mapping) and first.get("op") == "literal" else None
        if node.get("function") == "core:vector.add":
            return True
        return literal is None or literal == self.template.topology or literal in self.grid_states

    def coordinate(self, expression: Any, path: str) -> Any:
        rank = len(self.old_extents)
        if not isinstance(expression, dict) or not self.pad:
            return self._check_bounds(expression, path)
        literal = _literal_coordinate(expression, rank)
        if literal is not None:
            padded = list(literal) + [0] * self.pad
            self.result.padded.append(path)
            if expression.get("op") == "literal":
                return self._check_bounds(dict(expression, value=padded), path)
            return self._check_bounds(dict(expression, items=list(expression["items"]) + [
                {"op": "literal", "value": 0} for _ in range(self.pad)]), path)
        if expression.get("op") == "list" and isinstance(expression.get("items"), list) and len(expression["items"]) == rank:
            self.result.residual.append("{0}: coordinate built component-wise for rank {1}; the new axis needs a "
                                        "semantic choice".format(path, rank))
            return expression
        if expression.get("op") == "if":
            for key in ("then", "else"):
                if key in expression:
                    expression[key] = self.coordinate(expression[key], "{0}/{1}".format(path, key))
        if expression.get("op") == "literal" and isinstance(expression.get("value"), list):
            values = expression["value"]
            if values and all(_int_list(item, rank) for item in values):
                self.result.padded.append(path)
                return dict(expression, value=[list(item) + [0] * self.pad for item in values])
        return expression

    def _check_bounds(self, expression: Any, path: str) -> Any:
        literal = _literal_coordinate(expression, len(self.new_extents))
        if literal is not None and any(value >= extent for value, extent in zip(literal, self.new_extents)):
            self.result.residual.append("{0}: literal coordinate {1} lies outside the resized board {2}".format(
                path, list(literal), self.new_extents))
        return expression

    # -- counts ------------------------------------------------------------------
    def counts(self) -> None:
        sites_old = _product(self.old_extents)
        sites_new = _product(self.new_extents)
        if sites_old == sites_new:
            return
        sampled = sorted(k for k in self._sampled_counts() if 0 < k < sites_old)
        sites = {"op": "count", "args": [{"op": "call", "function": "core:topology.sites",
                                          "args": [{"op": "literal", "value": self.template.topology}]}]}
        mapping: Dict[int, Tuple[Dict[str, Any], int, str]] = {
            sites_old: (sites, sites_new, "total site count -> count(topology.sites)")}
        for k in sampled:
            if self.template.count_policy == "scale":
                product_ = {"op": "mul", "args": [{"op": "literal", "value": k}, deepcopy(sites)]}
                floor = {"op": "div", "args": [{"op": "sub", "args": [product_, {"op": "mod", "args": [
                    deepcopy(product_), {"op": "literal", "value": sites_old}]}]}, {"op": "literal", "value": sites_old}]}
                k_expr = {"op": "max", "args": [{"op": "literal", "value": 1}, floor]}
                new_k = max(1, k * sites_new // sites_old)
                mapping.setdefault(k, (k_expr, new_k, "sampled count scaled: max(1, {0} * count(sites) // {1})".format(k, sites_old)))
            else:
                k_expr, new_k = {"op": "literal", "value": k}, k
            mapping.setdefault(sites_old - k, ({"op": "sub", "args": [deepcopy(sites), deepcopy(k_expr)]},
                                               sites_new - new_k, "site count minus sampled count {0}".format(k)))
        self._replace(self.rule, "", mapping, None)
        extents = {value for value, new in zip(self.old_extents, self.new_extents) if value != new}
        self._extent_warnings(self.rule, "", extents - set(mapping) - set(sampled))

    def _sampled_counts(self) -> set:
        """Literal K in slice(sites_sequence, 0, K) / sample(values=sites, count=K)."""
        site_vars = set()
        found = set()

        def site_derived(expression: Any) -> bool:
            if isinstance(expression, Mapping):
                if expression.get("op") == "call" and expression.get("function") == "core:topology.sites":
                    return True
                if expression.get("op") == "var" and expression.get("name") in site_vars:
                    return True
                return any(site_derived(value) for value in expression.values())
            if isinstance(expression, list):
                return any(site_derived(item) for item in expression)
            return False

        def visit(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    visit(item)
                return
            if not isinstance(node, Mapping):
                return
            if node.get("op") in ("random.draw", "random.sample") and isinstance(node.get("as"), str):
                if site_derived(node.get("distribution")) or site_derived(node.get("domain")):
                    site_vars.add(node["as"])
            if node.get("op") == "foreach" and isinstance(node.get("as"), str) and site_derived(node.get("query")):
                site_vars.add(node["as"])
            if node.get("op") == "call" and node.get("function") == "core:sequence.slice":
                args = node.get("args") or []
                if args and site_derived(args[0]):
                    for arg in args[1:]:
                        if isinstance(arg, Mapping) and arg.get("op") == "literal" and _int(arg.get("value")):
                            found.add(arg["value"])
            distribution = node.get("distribution")
            if isinstance(distribution, Mapping) and distribution.get("kind") == "sample" and site_derived(distribution.get("values")):
                count = distribution.get("count")
                if isinstance(count, Mapping) and count.get("op") == "literal" and _int(count.get("value")):
                    found.add(count["value"])
            for value in node.values():
                visit(value)

        for _ in range(2):  # variables bound later in document order
            visit(self.rule)
        return {k for k in found if k > 0}

    def _replace(self, node: Any, path: str, mapping: Mapping[int, Tuple[Dict[str, Any], int, str]],
                 parent_op: Optional[str]) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                value = item.get("value") if isinstance(item, Mapping) and item.get("op") == "literal" else None
                if parent_op in ("compare", "slice") and _int(value) and value in mapping:
                    expression, target_value, reason = mapping[value]
                    if expression != item:
                        node[index] = deepcopy(expression)
                        self.result.replaced.append({"path": "{0}/{1}".format(path, index), "old": value,
                                                     "target_value": target_value, "reason": reason})
                    continue
                self._replace(item, "{0}/{1}".format(path, index), mapping, parent_op)
            return
        if not isinstance(node, dict):
            return
        op = node.get("op")
        for key, value in node.items():
            # Only direct operands of a comparison or slice; never nested call arguments.
            child = None
            if key == "args" and op in _COMPARISONS:
                child = "compare"
            elif key == "args" and op == "call" and node.get("function") == "core:sequence.slice":
                child = "slice"
            self._replace(value, "{0}/{1}".format(path, key), mapping, child)

    def _extent_warnings(self, node: Any, path: str, extents: Iterable[int]) -> None:
        extents = set(extents)
        if not extents:
            return
        stack = [(node, path, False)]
        while stack:
            current, pointer, compared = stack.pop()
            if isinstance(current, list):
                stack.extend((item, "{0}/{1}".format(pointer, i), compared) for i, item in enumerate(current))
            elif isinstance(current, Mapping):
                if compared and current.get("op") == "literal" and current.get("value") in extents and _int(current.get("value")):
                    self.result.warnings.append("{0}: constant {1} equals a resized extent; verify it".format(pointer, current["value"]))
                for key, value in current.items():
                    stack.append((value, "{0}/{1}".format(pointer, key), current.get("op") in _COMPARISONS and key == "args"))


# ------------------------------------------------------------------ checks

def verify_lift(source_rule: Mapping[str, Any], result: LiftResult, tests: Sequence[Mapping[str, Any]] = ()) -> Dict[str, Any]:
    """Z=1 against the (resized) Source, plus optional planner tests."""
    from .lift_tools import run_behavior_tests, z_equals_one_equivalence
    reference = source_rule
    if result.template.set_extents:
        resized = apply_lift_template(source_rule, result.template.resize_only())
        reference = resized.rule
    z1 = z_equals_one_equivalence(reference, result.rule)
    report = {"z_equals_one": z1.to_mapping(), "reference": "resized source" if reference is not source_rule else "source",
              "errors": list(z1.errors)}
    if tests:
        behaviour = run_behavior_tests(result.rule, tests)
        report["behavior_tests"] = behaviour.to_mapping()
        report["errors"].extend(behaviour.errors)
    return report


def sample_behavior_tests(rule: Mapping[str, Any], *, games: int = 2, seed: int = 7, max_steps: int = 400) -> List[Dict[str, Any]]:
    """Replayable staged-format traces of the lifted Rule, for downstream Scene/Input checks.

    They document engine behaviour; they are not independent acceptance.
    """
    from .behavior_runtime import test_runtime
    tests = []
    rng = random.Random(seed)
    for game in range(games):
        runtime = test_runtime(rule, {"name": "trace", "seed": game})
        steps, rejected = [], None
        try:
            for _ in range(max_steps):
                legal = sorted(runtime.legal_actions(), key=lambda a: (a.action_id, repr(dict(a.parameters))))
                if rejected is None:
                    illegal = [a for a in runtime.all_actions() if not runtime.is_legal(a)]
                    if illegal:
                        rejected = illegal[0]
                        steps.append({"action": rejected.action_id, "parameters": _plain(dict(rejected.parameters)), "accepted": False})
                if not legal or runtime.evaluate_outcome().terminal:
                    break
                choice = legal[rng.randrange(len(legal))]
                steps.append({"action": choice.action_id, "parameters": _plain(dict(choice.parameters)), "accepted": True})
                if runtime.apply_action(choice).outcome.terminal:
                    break
            outcome = runtime.evaluate_outcome()
            tests.append({"name": "engine lift trace {0}".format(game), "seed": game, "steps": steps,
                          "expect": {"terminal": outcome.terminal, "status": outcome.status}})
        finally:
            runtime.close()
    return tests


# ------------------------------------------------------------------ helpers

def _movement_paths(rule: Mapping[str, Any]) -> List[str]:
    """Places where a Rule moves something relative to a coordinate."""
    found: List[str] = []
    for index, variable in enumerate((rule.get("state") or {}).get("variables") or []):
        if isinstance(variable, Mapping) and variable.get("type") == "core:coord":
            found.append("/state/variables/{0}".format(index))
    stack = [(rule, "")]
    while stack:
        node, path = stack.pop()
        if isinstance(node, list):
            stack.extend((item, "{0}/{1}".format(path, i)) for i, item in enumerate(node))
        elif isinstance(node, Mapping):
            if node.get("op") == "call" and node.get("function") in ("core:vector.add", "core:topology.ray"):
                found.append(path)
            stack.extend((value, "{0}/{1}".format(path, key)) for key, value in node.items())
    return sorted(found)


def _grid_topology(rule: Mapping[str, Any], wanted: Optional[str] = None) -> Tuple[Optional[Mapping[str, Any]], List[str]]:
    grids = [item for item in rule.get("topologies") or []
             if isinstance(item, Mapping) and item.get("kind") == "rect_grid" and (wanted is None or item.get("id") == wanted)]
    if len(grids) != 1:
        return None, ["the template lifts exactly one rect_grid topology; found {0}".format(len(grids))]
    return grids[0], []


def _world_axes(topology: Mapping[str, Any], scene: Optional[Mapping[str, Any]]) -> Dict[str, int]:
    """Map world x/y to topology axes via the Scene visualizer, else axis order."""
    rank = len(topology.get("axes") or [])
    mapping = {"x": 0, "y": 1, "z": 2}
    for node in (scene or {}).get("nodes") or []:
        for component in node.get("components") or [] if isinstance(node, Mapping) else []:
            properties = component.get("properties") if isinstance(component, Mapping) else None
            if not isinstance(properties, Mapping) or properties.get("rule_topology") != topology.get("id"):
                continue
            matrix = properties.get("index_to_world")
            if isinstance(matrix, list) and len(matrix) == 16:
                found = {}
                for axis in range(min(rank, 3)):
                    column = [abs(matrix[row * 4 + axis]) for row in range(3)]
                    world = "xyz"[column.index(max(column))]
                    found.setdefault(world, axis)
                if len(found) == min(rank, 3):
                    mapping = dict(found)
    return {axis: index for axis, index in mapping.items() if index < rank}


def _fresh_axis(preferred: str, names: Sequence[str]) -> str:
    name, counter = preferred, 1
    while name in names:
        counter += 1
        name = "{0}{1}".format(preferred, counter)
    return name


def _literal_coordinate(expression: Any, rank: int) -> Optional[Tuple[int, ...]]:
    if not isinstance(expression, Mapping):
        return None
    if expression.get("op") == "literal" and _int_list(expression.get("value"), rank):
        return tuple(expression["value"])
    if expression.get("op") == "list" and isinstance(expression.get("items"), list) and len(expression["items"]) == rank:
        values = [item.get("value") if isinstance(item, Mapping) and item.get("op") == "literal" else None
                  for item in expression["items"]]
        if all(_int(value) for value in values):
            return tuple(values)
    return None


def _int_list(value: Any, rank: int) -> bool:
    return isinstance(value, list) and len(value) == rank and all(_int(item) for item in value)


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _product(values: Sequence[int]) -> int:
    total = 1
    for value in values:
        total *= int(value)
    return total


def _plain(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    return value
