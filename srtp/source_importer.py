"""Whole-project source ingestion for SRTP Function 1.

The importer deliberately separates three claims:

* the original project can run;
* a source behaviour has been identified with evidence;
* that behaviour has been lifted to 3D.

None of those claims implies either of the others.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .parser import RuleFileParser
from .report import Diagnostic, ParseReport, SourceEvidence, deduplicate_diagnostics
from .schema import classify_schema, set_path, validate_rule_schema
from .source_game import (
    MechanicLift,
    RuntimeSpec,
    SourceGamePackage,
    SourceLocation,
    SourceParameter,
    TransformationPlan,
    coverage_from_schema,
)


MAX_PROJECT_FILES = 300
MAX_PROJECT_BYTES = 12 * 1024 * 1024
IGNORED_PARTS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", "build", "dist"}
PYTHON_ENTRY_NAMES = ("main.py", "game.py", "run.py", "app.py", "__main__.py")
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".wav", ".ogg", ".mp3", ".ttf", ".otf"}
FRAMEWORK_IMPORTS = {
    "pygame": "pygame",
    "turtle": "turtle",
    "ursina": "ursina",
    "arcade": "arcade",
    "pyxel": "pyxel",
    "tkinter": "tkinter",
    "pyglet": "pyglet",
}
STANDARD_LIBRARY = {
    "argparse", "ast", "collections", "copy", "dataclasses", "datetime", "enum", "functools",
    "hashlib", "heapq", "importlib", "itertools", "json", "math", "os", "pathlib", "random",
    "re", "sys", "time", "tkinter", "turtle", "typing", "unittest", "uuid",
}


class SourceGameImporter:
    """Inspect a complete source tree while keeping the source untouched."""

    def __init__(self, python_executable: Optional[str] = None) -> None:
        self.python_executable = python_executable or sys.executable
        self.file_parser = RuleFileParser()

    def import_path(self, path: Path) -> SourceGamePackage:
        selected = Path(path).resolve()
        entrypoint, root = self._resolve_entrypoint(selected)
        files = self._inventory(root, entrypoint)
        relative_files = [self._relative(item, root) for item in files]
        assets = [item for item in relative_files if Path(item).suffix.lower() in ASSET_SUFFIXES]
        python_files = [item for item in files if item.suffix.lower() in (".py", ".pyw")]
        trees, sources, syntax_diagnostics = self._parse_python_files(python_files, root)
        framework = self._detect_framework(trees)
        dependencies, missing = self._dependencies(trees, root)
        runtime = self._runtime_spec(entrypoint, root, framework, dependencies, missing)
        title = self._title(entrypoint, root, trees)

        report = self.file_parser.parse(entrypoint) if entrypoint.is_file() else ParseReport({}, source_path=str(entrypoint))
        facts = _PythonProjectFacts(trees, sources, root)
        self._apply_project_facts(report, facts, framework, root, files)
        report.diagnostics.extend(syntax_diagnostics)
        self._refinalize_report(report, root, files)

        parameters = self._source_parameters(facts, report, framework, root)
        transformation = self._transformation_plan(report, facts, framework)
        license_name, license_path = self._license(root)
        upstream_url = self._upstream(root)
        coverage = coverage_from_schema(report.schema, proven_overrides=("visual/assets",) if assets else ())
        diagnostics = self._package_diagnostics(runtime, coverage, transformation, entrypoint)
        return SourceGamePackage(
            title=title,
            root=root,
            entrypoint=entrypoint,
            runtime=runtime,
            rule_report=report,
            parameters=parameters,
            transformation=transformation,
            coverage=coverage,
            files=relative_files,
            assets=assets,
            license_name=license_name,
            license_path=license_path,
            upstream_url=upstream_url,
            diagnostics=diagnostics,
        )

    def _resolve_entrypoint(self, selected: Path) -> Tuple[Path, Path]:
        if selected.is_file():
            entrypoint = selected
            if selected.parent.joinpath("__init__.py").exists():
                return entrypoint, selected.parent.parent
            return entrypoint, selected.parent
        if not selected.is_dir():
            return selected, selected.parent
        for name in PYTHON_ENTRY_NAMES:
            candidate = selected / name
            if candidate.is_file():
                return candidate, selected
        html = selected / "index.html"
        if html.is_file():
            return html, selected
        python_files = sorted(selected.glob("*.py"))
        if python_files:
            return python_files[0], selected
        return selected, selected

    def _inventory(self, root: Path, entrypoint: Path) -> List[Path]:
        if not root.is_dir():
            return [root] if root.is_file() else []
        result: List[Path] = []
        total = 0
        for item in sorted(root.rglob("*")):
            if any(part in IGNORED_PARTS for part in item.parts) or not item.is_file():
                continue
            try:
                size = item.stat().st_size
            except OSError:
                continue
            if len(result) >= MAX_PROJECT_FILES or total + size > MAX_PROJECT_BYTES:
                break
            result.append(item)
            total += size
        if entrypoint.parent.joinpath("__init__.py").is_file():
            package_sources = self._package_source_closure(entrypoint)
            result = [
                item for item in result
                if item.suffix.lower() not in (".py", ".pyw") or item in package_sources
            ]
        return result

    @staticmethod
    def _package_source_closure(entrypoint: Path) -> set:
        """Keep one independent package game plus its relative imports."""

        package = entrypoint.parent
        pending = [entrypoint, package / "__init__.py"]
        result = set()
        while pending:
            path = pending.pop()
            if path in result or not path.is_file():
                continue
            result.add(path)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level and node.module:
                    candidate = package / (node.module.split(".")[0] + ".py")
                    if candidate.is_file():
                        pending.append(candidate)
        return result

    def _parse_python_files(
        self, files: Sequence[Path], root: Path
    ) -> Tuple[Dict[str, ast.Module], Dict[str, str], List[Diagnostic]]:
        trees: Dict[str, ast.Module] = {}
        sources: Dict[str, str] = {}
        diagnostics: List[Diagnostic] = []
        for path in files:
            relative = self._relative(path, root)
            try:
                source = path.read_text(encoding="utf-8-sig")
                tree = ast.parse(source, filename=relative)
                setattr(tree, "_source_path", relative)
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as error:
                diagnostics.append(Diagnostic(
                    "warning", "project.python_unparsed", relative,
                    "A Python source file could not be analysed.", evidence=str(error), requires_llm=True,
                ))
                continue
            if sum(1 for _ in ast.walk(tree)) > 30000:
                diagnostics.append(Diagnostic(
                    "warning", "project.python_complex", relative,
                    "A Python source file exceeded the per-file AST safety limit.", requires_llm=True,
                ))
                continue
            trees[relative] = tree
            sources[relative] = source
        return trees, sources, diagnostics

    def _detect_framework(self, trees: Mapping[str, ast.Module]) -> str:
        imports = _import_roots(trees.values())
        for module, framework in FRAMEWORK_IMPORTS.items():
            if module in imports:
                return framework
        return "python-stdlib" if trees else "unknown"

    def _dependencies(self, trees: Mapping[str, ast.Module], root: Path) -> Tuple[List[str], List[str]]:
        imports = sorted(_import_roots(trees.values()))
        local_roots = {item.stem for item in root.rglob("*.py")}
        local_roots.update(item.name for item in root.iterdir() if item.is_dir()) if root.is_dir() else None
        dependencies: List[str] = []
        missing: List[str] = []
        for module in imports:
            if module in STANDARD_LIBRARY or module in local_roots:
                continue
            dependencies.append(module)
            if importlib.util.find_spec(module) is None:
                missing.append(module)
        return dependencies, missing

    def _runtime_spec(
        self, entrypoint: Path, root: Path, framework: str,
        dependencies: List[str], missing: List[str],
    ) -> RuntimeSpec:
        if entrypoint.suffix.lower() in (".py", ".pyw"):
            if entrypoint.parent.joinpath("__init__.py").exists():
                module = "{0}.{1}".format(entrypoint.parent.name, entrypoint.stem)
                command = [self.python_executable, "-m", module]
            else:
                command = [self.python_executable, entrypoint.name]
            compatibility_notes: List[str] = []
            if framework == "pygame" and entrypoint.name != "runtime_bootstrap.py":
                bootstrap = Path(__file__).with_name("runtime_bootstrap.py")
                command = [self.python_executable, str(bootstrap), str(entrypoint)]
                compatibility_notes.append(
                    "Pygame source is executed unchanged behind a Windows font-registry compatibility boundary."
                )
            return RuntimeSpec(
                kind="python", command=command, cwd=str(root), framework=framework,
                language="python", dependencies=dependencies, missing_dependencies=missing,
                window_mode="framework_adapter" if framework in ("pygame", "turtle") else "external",
                compatibility_notes=compatibility_notes,
            )
        if entrypoint.suffix.lower() in (".html", ".htm"):
            return RuntimeSpec(
                kind="html", command=[str(entrypoint)], cwd=str(root), framework="browser",
                language="javascript/html", window_mode="browser",
            )
        return RuntimeSpec(
            kind="unsupported", command=[], cwd=str(root), framework=framework,
            language="unknown", dependencies=dependencies, missing_dependencies=missing,
        )

    def _title(self, entrypoint: Path, root: Path, trees: Mapping[str, ast.Module]) -> str:
        relative = self._relative(entrypoint, root)
        tree = trees.get(relative)
        if tree:
            docstring = ast.get_docstring(tree)
            if docstring:
                return docstring.splitlines()[0].strip().rstrip(".")
        readme = next((item for item in (root / "README.md", root / "README.rst") if item.is_file()), None)
        if readme:
            try:
                for line in readme.read_text(encoding="utf-8-sig").splitlines():
                    if line.strip().startswith("#"):
                        return line.strip().lstrip("#").strip()
            except OSError:
                pass
        return entrypoint.stem.replace("_", " ").title()

    def _apply_project_facts(
        self, report: ParseReport, facts: "_PythonProjectFacts", framework: str,
        root: Path, files: Sequence[Path],
    ) -> None:
        schema = report.schema
        dimensions = facts.logical_dimensions()
        if dimensions:
            x_size, y_size, detail, confidence, locations = dimensions
            set_path(schema, "space.dimensions", {"x": x_size, "y": y_size, "z": 1})
            for axis in ("x", "y"):
                for location in locations:
                    report.add_evidence(
                        "space.dimensions.{0}".format(axis),
                        SourceEvidence("project_static", location.path, confidence, detail, location.line),
                    )
            report.diagnostics = [item for item in report.diagnostics if item.code not in {
                "python.grid_unresolved", "space.dimension_missing", "space.dimension_type"
            }]

        controls = facts.controls()
        actions = facts.actions(controls)
        if actions:
            schema["actions"] = actions
            report.diagnostics = [item for item in report.diagnostics if item.code != "actions.unresolved"]
        flow = facts.flow()
        if flow:
            schema["flow"].update(flow)
            report.diagnostics = [item for item in report.diagnostics if item.code != "flow.unresolved"]
        participants = facts.participants(controls)
        if participants:
            schema["participants"] = participants
            if schema["flow"].get("model") == "turn_based" and not schema["flow"].get("turn_order"):
                schema["flow"]["turn_order"] = [item["id"] for item in participants]
        entities, state_variables = facts.entities_state(framework)
        if entities:
            schema["entities"] = entities
        if state_variables:
            schema["state"]["variables"] = state_variables
        if facts.hidden_information():
            schema["state"]["information"] = "hidden"
        elif controls:
            schema["state"]["information"] = "perfect"
        randomness = facts.randomness()
        if randomness:
            schema["randomness"] = randomness
        outcomes, goals = facts.outcomes()
        if outcomes:
            schema["outcomes"] = outcomes
            schema["goals"] = goals
        anchor = facts.coordinate_anchor(framework)
        if anchor:
            schema["space"]["coordinate_anchor"] = anchor
            report.diagnostics = [item for item in report.diagnostics if item.code != "space.anchor_unresolved"]
        schema["ui_hints"].update({
            "preserve_source_renderer": True,
            "source_framework": framework,
            "controls": controls,
            "asset_files": [self._relative(item, root) for item in files if item.suffix.lower() in ASSET_SUFFIXES],
        })
        modes = facts.modes()
        if modes:
            schema["modes"] = modes
        schema["extensions"]["source_project"] = {
            "root": str(root),
            "files_analysed": len(facts.trees),
            "analysis": "Whole-project AST and data-file evidence; project was not imported during analysis.",
            "visual_source_proven": framework != "unknown" and facts.has_render_calls(),
            "source_modes_proven": bool(modes),
        }

    def _refinalize_report(self, report: ParseReport, root: Path, files: Sequence[Path]) -> None:
        report.source_path = str(root)
        report.source_format = "source_project"
        classify_schema(report.schema)
        source = report.schema.setdefault("source", {})
        source["path"] = str(root)
        source["format"] = "source_project"
        digest = hashlib.sha256()
        for path in files:
            digest.update(self._relative(path, root).encode("utf-8"))
            try:
                digest.update(path.read_bytes())
            except OSError:
                continue
        source["sha256"] = digest.hexdigest()
        source["provenance"] = {
            path: [item.to_mapping() for item in evidence]
            for path, evidence in report.provenance.items()
        }
        stale_codes = {
            "space.dimension_missing", "space.dimension_type", "space.anchor_unresolved",
            "flow.unresolved", "actions.unresolved", "action.semantic_handoff",
            "outcome.semantic_handoff", "randomness.distribution_missing",
        }
        preserved = [item for item in report.diagnostics if item.code not in stale_codes]
        report.diagnostics = deduplicate_diagnostics(preserved + validate_rule_schema(report.schema))
        if any(item.severity == "error" for item in report.diagnostics):
            report.readiness = "blocked"
        elif any(item.requires_llm or item.severity == "warning" for item in report.diagnostics):
            report.readiness = "partial"
        else:
            report.readiness = "complete"
        report.schema["extensions"]["function1_status"] = {
            "readiness": report.readiness,
            "original_runtime_separate": True,
            "compiled_to_stal": False,
        }

    def _source_parameters(
        self, facts: "_PythonProjectFacts", report: ParseReport,
        framework: str, root: Path,
    ) -> List[SourceParameter]:
        result: List[SourceParameter] = []
        dimensions = facts.logical_dimensions()
        if dimensions:
            x_size, y_size, detail, _confidence, locations = dimensions
            for axis, value in (("x", x_size), ("y", y_size)):
                result.append(SourceParameter(
                    id="source_grid_{0}".format(axis), label="Source grid {0}".format(axis.upper()),
                    category="space", value=value, value_type="integer",
                    applicability="applicable", edit_mode="source_patch",
                    reason="{0}. It is coupled to source code and is not safe to change as an isolated field.".format(detail),
                    locations=list(locations), constraints={"minimum": 1},
                    affects=["source gameplay", "2D baseline", "3D target default"],
                ))
        cell = facts.cell_size()
        if cell:
            value, location, detail = cell
            result.append(SourceParameter(
                id="source_cell_size", label="Source spatial step / cell size", category="space",
                value=value, value_type="integer", applicability="applicable", edit_mode="source_patch",
                reason=detail, locations=[location], constraints={"minimum": 1},
                affects=["movement lattice", "rendering", "spawn coordinates"],
            ))
        tick = facts.timer_interval()
        if tick:
            value, location = tick
            result.append(SourceParameter(
                id="source_tick_ms", label="Source update interval (ms)", category="flow",
                value=value, value_type="integer", applicability="applicable", edit_mode="literal_patch",
                reason="The interval is one proven timer literal and can be changed in a derived source copy.", locations=[location],
                constraints={"minimum": 1}, affects=["game speed"],
            ))
        mine_count = facts.mine_count()
        if mine_count:
            value, location = mine_count
            result.append(SourceParameter(
                id="source_mine_count", label="Mine count", category="randomness",
                value=value, value_type="integer", applicability="applicable", edit_mode="literal_patch",
                reason="The source repeats mine placement with one proven loop literal; a derived copy can change it.", locations=[location],
                constraints={"minimum": 1}, affects=["difficulty", "mine distribution"],
            ))
        for key, value, location in facts.json_display_parameters():
            constraints = {
                "size": {"minimum": 200, "maximum": 1200},
                "padding": {"minimum": 0, "maximum": 50},
                "font_size": {"minimum": 8, "maximum": 120},
            }.get(key, {})
            result.append(SourceParameter(
                id="source_visual_{0}".format(key), label="Source visual: {0}".format(key), category="visual",
                value=value, value_type=type(value).__name__, applicability="applicable", edit_mode="data_file",
                reason="This value is explicitly stored in a source JSON data file and can be changed in a derived copy.",
                locations=[location], constraints=constraints, affects=["source presentation"],
            ))
        result.extend([
            SourceParameter(
                id="coordinate_anchor", label="Placement anchor", category="space",
                value=report.schema.get("space", {}).get("coordinate_anchor", "unknown"), value_type="enum",
                applicability="fixed_by_source" if report.schema.get("space", {}).get("coordinate_anchor") != "unknown" else "unresolved",
                edit_mode="none" if report.schema.get("space", {}).get("coordinate_anchor") != "unknown" else "llm",
                reason="This is inferred from the source input and drawing logic; changing it would change the game rather than lift it.",
                affects=["interaction semantics"],
            ),
            SourceParameter(
                id="temporal_model", label="Temporal model", category="flow",
                value=report.schema.get("flow", {}).get("model", "unknown"), value_type="enum",
                applicability="fixed_by_source" if report.schema.get("flow", {}).get("model") != "unknown" else "unresolved",
                edit_mode="none" if report.schema.get("flow", {}).get("model") != "unknown" else "llm",
                reason="Turn, timer and event-loop behaviour belongs to the original mechanic and is not a generic option.",
                affects=["action timing", "system updates"],
            ),
        ])
        return result

    def _transformation_plan(
        self, report: ParseReport, facts: "_PythonProjectFacts", framework: str,
    ) -> TransformationPlan:
        source_dimensions = dict(report.schema.get("space", {}).get("dimensions", {"x": None, "y": None, "z": 1}))
        action_ids = {item.get("id") for item in report.schema.get("actions", []) if isinstance(item, Mapping)}
        source_names = {Path(name).stem.lower() for name in facts.sources}
        adapter_id = ""
        if "snake" in source_names and "change_direction" in action_ids:
            adapter_id = "snake"
        elif "minesweeper" in source_names and "reveal_cell" in action_ids:
            adapter_id = "minesweeper"
        elif "connect" in source_names and "place_at_click" in action_ids:
            adapter_id = "connect"
        elif {"main", "game", "logic"}.issubset(source_names) and "shift_merge" in action_ids:
            adapter_id = "2048"
        adapter_status = "ready" if adapter_id else "needs_adapter"
        adapter_reason = (
            "A tested Ursina transformation adapter is registered for this source mechanic."
            if adapter_id
            else "A source-framework transformation adapter has not been registered."
        )
        lifts = [
            MechanicLift(
                "add_z_axis", "Add Z axis", "The source uses a 2D logical plane.",
                "Preserve source X/Y and let the designer choose a positive Z extent.",
                "ready", "Z is exposed as the single primary spatial input in the Workbench Inspector.",
            ),
            MechanicLift(
                "source_renderer", "Preserve source visual identity",
                "The original framework owns drawing, assets, layout and feedback.",
                "Map the same visual vocabulary to 3D geometry instead of replacing it with generic cubes.",
                adapter_status, adapter_reason,
            ),
        ]
        if "change_direction" in action_ids:
            lifts.append(MechanicLift(
                "direction_lift", "Lift directional control", "The player selects one of four planar directions.",
                "Retain the four source directions and add +Z/-Z while preserving reversal and collision rules.",
                adapter_status, adapter_reason,
            ))
        if "reveal_cell" in action_ids:
            lifts.append(MechanicLift(
                "neighbourhood_lift", "Lift reveal neighbourhood", "A clicked cell reveals source-defined planar neighbours.",
                "Extend the exact source neighbourhood through Z and update mine counts/flood fill consistently.",
                adapter_status, adapter_reason,
            ))
        if "shift_merge" in action_ids:
            lifts.append(MechanicLift(
                "merge_lift", "Lift shift/merge", "All tiles shift and merge along four planar directions.",
                "Apply the same ordered merge invariant along six 3D directions and spawn on empty 3D sites.",
                adapter_status, adapter_reason,
            ))
        if report.schema.get("randomness", {}).get("model") in ("stochastic", "mixed"):
            lifts.append(MechanicLift(
                "random_lift", "Lift random generation", "Random events choose positions on the source plane.",
                "Use the same distribution over valid 3D sites without changing event probabilities.",
                adapter_status, adapter_reason,
            ))
        if report.schema.get("outcomes"):
            lifts.append(MechanicLift(
                "outcome_lift", "Lift terminal and scoring conditions", "Source outcomes inspect 2D state.",
                "Re-express the same objective over the lifted state without inventing a new win condition.",
                adapter_status, adapter_reason,
            ))
        return TransformationPlan(
            source_dimensions=source_dimensions,
            target_dimensions={"x": source_dimensions.get("x"), "y": source_dimensions.get("y"), "z": 3},
            adapter_id=adapter_id,
            lifts=lifts,
        )

    def _license(self, root: Path) -> Tuple[str, str]:
        for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"):
            path = root / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")[:2000]
            except OSError:
                text = ""
            name_value = "Apache-2.0" if "Apache License" in text else "MIT" if "MIT License" in text or "Permission is hereby granted" in text else "detected"
            return name_value, str(path)
        return "unknown", ""

    @staticmethod
    def _upstream(root: Path) -> str:
        token = root.as_posix().lower()
        if "free_python_games" in token:
            return "https://github.com/grantjenks/free-python-games"
        if "pygame_2048" in token or "2048-pygame" in token:
            return "https://github.com/rajitbanerjee/2048-pygame"
        return ""

    @staticmethod
    def _package_diagnostics(
        runtime: RuntimeSpec, coverage: Any, transformation: TransformationPlan, entrypoint: Path,
    ) -> List[Diagnostic]:
        result: List[Diagnostic] = []
        if not entrypoint.is_file():
            result.append(Diagnostic("error", "project.entrypoint", "$", "No runnable entrypoint was found."))
        if runtime.missing_dependencies:
            result.append(Diagnostic(
                "error", "project.dependencies", "runtime.dependencies",
                "The original game cannot run until its dependencies are available.",
                evidence=", ".join(runtime.missing_dependencies),
            ))
        if coverage.ratio < 1.0:
            result.append(Diagnostic(
                "warning", "project.coverage", "analysis_coverage",
                "Function 1 has not proven every required rule category.",
                evidence="{0}/{1} categories proven".format(coverage.understood, coverage.total),
                requires_llm=True,
            ))
        if transformation.readiness != "ready":
            result.append(Diagnostic(
                "info", "transform.not_compiled", "transformation",
                "The original 2D game is available, but a faithful 3D reconstruction has not yet been compiled.",
            ))
        return result

    @staticmethod
    def _relative(path: Path, root: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.name


class _PythonProjectFacts:
    def __init__(self, trees: Mapping[str, ast.Module], sources: Mapping[str, str], root: Path) -> None:
        self.trees = dict(trees)
        self.sources = dict(sources)
        self.root = root

    def logical_dimensions(self) -> Optional[Tuple[int, int, str, float, List[SourceLocation]]]:
        candidates: List[Tuple[int, int, str, float, SourceLocation]] = []
        for path, tree in self.trees.items():
            constants = _module_constants(tree)
            for node in ast.walk(tree):
                shape = _nested_board_shape(node, constants)
                if shape:
                    candidates.append((shape[0], shape[1], "nested logical board construction", 0.98, SourceLocation(path, getattr(node, "lineno", None))))
            for function in _functions(tree):
                shape = _draw_loop_shape(function, constants)
                if shape:
                    candidates.append((shape[0], shape[1], "nested source draw/input lattice", 0.96, SourceLocation(path, getattr(function, "lineno", None), function.name)))
                shape = _inside_lattice_shape(function, tree)
                if shape:
                    candidates.append((shape[0], shape[1], "movement boundary divided by proven spatial step", 0.88, SourceLocation(path, getattr(function, "lineno", None), function.name)))
        if not candidates:
            return None
        grouped = Counter((x, y, detail) for x, y, detail, _confidence, _location in candidates)
        x_size, y_size, detail = max(grouped, key=lambda item: (grouped[item], _dimension_priority(item[2])))
        matching = [item for item in candidates if item[:3] == (x_size, y_size, detail)]
        return x_size, y_size, detail, max(item[3] for item in matching), [item[4] for item in matching]

    def controls(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for path, tree in self.trees.items():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = _call_name(node.func)
                if name in ("onkey", "turtle.onkey") and len(node.args) >= 2:
                    key = _string_value(node.args[1])
                    if key:
                        result.append({"device": "keyboard", "input": key, "handler": _handler_name(node.args[0]), "source": SourceLocation(path, node.lineno).to_mapping()})
                elif name in ("onscreenclick", "turtle.onscreenclick") and node.args:
                    result.append({"device": "mouse", "input": "left_click", "handler": _handler_name(node.args[0]), "source": SourceLocation(path, node.lineno).to_mapping()})
        for path, value in self._json_files():
            keys = value.get("keys") if isinstance(value, Mapping) else None
            if isinstance(keys, Mapping):
                for raw, action in keys.items():
                    if str(raw).startswith("_"):
                        continue
                    result.append({"device": "keyboard", "input": str(raw), "handler": str(action), "source": SourceLocation(path).to_mapping()})
        unique: List[Dict[str, Any]] = []
        seen = set()
        for item in result:
            key = (item["device"], item["input"], item["handler"])
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    def actions(self, controls: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        names = self._function_names()
        actions: List[Dict[str, Any]] = []
        if "change" in names and "move" in names and any(item.get("device") == "keyboard" for item in controls):
            actions.extend([
                _action("change_direction", "Change direction", "control", "human_player", "change", "input_driven"),
                _action("advance", "Advance moving body", "move", "system", "move", "timer"),
            ])
        elif {"moveleft", "moveright", "moveup", "movedown"}.issubset({name.lower() for name in names}):
            actions.append(_action("shift_merge", "Shift and merge every tile", "transform", "human_player", "move", "input_driven"))
        if "tap" in names and any(item.get("device") == "mouse" for item in controls):
            verb = "reveal" if self.hidden_information() else "place"
            identifier = "reveal_cell" if verb == "reveal" else "place_at_click"
            actions.append(_action(identifier, "Reveal cell" if verb == "reveal" else "Act at clicked cell", verb, "human_player", "tap", "input_driven"))
        return actions

    def flow(self) -> Dict[str, Any]:
        timer = self.timer_interval()
        if timer:
            return {"model": "tick_based", "turn_order": [], "phases": ["input", "update", "render"], "tick_rate": 1000.0 / timer[0]}
        if any(_call_name(node.func) == "pygame.event.get" for tree in self.trees.values() for node in ast.walk(tree) if isinstance(node, ast.Call)):
            return {"model": "event_driven", "turn_order": [], "phases": ["input", "update", "render"], "tick_rate": None}
        if self.controls():
            return {"model": "event_driven", "turn_order": [], "phases": ["input", "update", "render"], "tick_rate": None}
        return {}

    def participants(self, controls: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        if not controls:
            return []
        if self._has_turn_toggle():
            return [
                {"id": "player_1", "name": "Player 1", "kind": "human", "symmetry_group": "source_players"},
                {"id": "player_2", "name": "Player 2", "kind": "human", "symmetry_group": "source_players"},
            ]
        return [{"id": "human_player", "name": "Human player", "kind": "human"}]

    def entities_state(self, framework: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        names = self._assigned_names()
        entities: List[Dict[str, Any]] = []
        variables: List[Dict[str, Any]] = []
        if "snake" in names:
            entities.append({"id": "snake", "name": "Snake", "kind": "avatar", "owner": "human_player", "supply": {"model": "grows"}})
            variables.append({"id": "snake_body", "type": "ordered_coordinate_list", "source_ref": names["snake"].to_mapping()})
        if "food" in names:
            entities.append({"id": "food", "name": "Food", "kind": "collectible", "owner": "system", "supply": {"model": "respawn"}})
            variables.append({"id": "food_position", "type": "coordinate", "source_ref": names["food"].to_mapping()})
        if all(name in names for name in ("bombs", "shown", "counts")):
            entities.append({"id": "mine_field", "name": "Hidden mine field", "kind": "cell_marker", "owner": "system", "supply": {"model": "generated"}})
            variables.extend([
                {"id": "bombs", "type": "hidden_cell_map", "source_ref": names["bombs"].to_mapping()},
                {"id": "shown", "type": "visibility_cell_map", "source_ref": names["shown"].to_mapping()},
                {"id": "neighbour_counts", "type": "integer_cell_map", "source_ref": names["counts"].to_mapping()},
            ])
        if {"moveleft", "moveright", "moveup", "movedown"}.issubset({name.lower() for name in self._function_names()}):
            entities.append({"id": "number_tiles", "name": "Number tiles", "kind": "token", "owner": "system", "supply": {"model": "spawned"}})
            variables.extend([
                {"id": "tile_board", "type": "integer_grid"},
                {"id": "target_tile", "type": "integer"},
            ])
        return entities, variables

    def hidden_information(self) -> bool:
        names = set(self._assigned_names())
        return {"bombs", "shown"}.issubset(names) or any("unopened" in source.lower() for source in self.sources.values())

    def randomness(self) -> Optional[Dict[str, Any]]:
        calls: List[Tuple[str, str, int]] = []
        for path, tree in self.trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = _call_name(node.func)
                    if name in ("randrange", "randint", "choice", "shuffle", "sample") or name.startswith("random."):
                        calls.append((name, path, node.lineno))
        if not calls:
            return {"model": "deterministic", "events": []}
        return {
            "model": "stochastic",
            "events": [{
                "id": "source_random_events", "distribution": "source_defined",
                "calls": [{"name": name, "path": path, "line": line} for name, path, line in calls],
            }],
        }

    def outcomes(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        outcomes: List[Dict[str, Any]] = []
        goals: List[Dict[str, Any]] = []
        function_map = self._function_map()
        status = next((item for name, item in function_map.items() if name.lower() in ("checkgamestatus", "check_game_status")), None)
        if status:
            path, node = status
            returns = {child.value for child in ast.walk(node) if isinstance(child, ast.Constant) and isinstance(child.value, str)}
            if "WIN" in returns:
                outcomes.append(_outcome("source_win", "win", True, path, node.lineno, "source checkGameStatus returns WIN"))
                goals.append({"id": "reach_source_target", "type": "terminal", "condition_ref": "source_win", "owner": "human_player"})
            if "LOSE" in returns:
                outcomes.append(_outcome("source_loss", "loss", True, path, node.lineno, "source checkGameStatus returns LOSE"))
        inside = next((item for name, item in function_map.items() if name.lower() == "inside"), None)
        move = next((item for name, item in function_map.items() if name.lower() == "move"), None)
        if inside and move and any(isinstance(node, ast.Compare) for node in ast.walk(inside[1])):
            path, node = move
            outcomes.append(_outcome("collision_loss", "loss", True, path, node.lineno, "movement stops when outside bounds or colliding with the body"))
            goals.append({"id": "consume_collectibles", "type": "intermediate", "owner": "human_player"})
        tap = next((item for name, item in function_map.items() if name.lower() == "tap"), None)
        if tap and self.hidden_information():
            path, node = tap
            outcomes.append(_outcome("mine_loss", "loss", True, path, node.lineno, "clicking a mined cell reveals all mines and ends the handler"))
            goals.append({"id": "reveal_safe_cells", "type": "terminal", "owner": "human_player", "executable": False})
        return outcomes, goals

    def coordinate_anchor(self, framework: str) -> Optional[str]:
        if self.hidden_information():
            return "cell_center"
        names = {name.lower() for name in self._function_names()}
        if framework == "pygame" and ("display" in names or "playgame" in names):
            return "cell_center"
        if "tap" in names or "move" in names:
            return "cell_center"
        return None

    def cell_size(self) -> Optional[Tuple[int, SourceLocation, str]]:
        values: List[Tuple[int, SourceLocation, str]] = []
        for path, tree in self.trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _call_name(node.func) in ("floor", "square", "stamp") and len(node.args) >= 2:
                    value = _int_value(node.args[1])
                    if value and value > 1:
                        values.append((value, SourceLocation(path, node.lineno), "Literal spatial quantisation used by the source input/drawing code."))
        if values:
            return Counter(item[0] for item in values).most_common(1)[0][0], values[0][1], values[0][2]
        step = _movement_step(self.trees.values())
        if step:
            return step[0], SourceLocation(step[1], step[2]), "Movement and spawn coordinates share this source lattice step."
        return None

    def timer_interval(self) -> Optional[Tuple[int, SourceLocation]]:
        for path, tree in self.trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _call_name(node.func) in ("ontimer", "turtle.ontimer") and len(node.args) >= 2:
                    value = _int_value(node.args[1])
                    if value and value > 0:
                        return value, SourceLocation(path, node.lineno)
        return None

    def mine_count(self) -> Optional[Tuple[int, SourceLocation]]:
        for path, tree in self.trees.items():
            for function in _functions(tree):
                if function.name.lower() not in ("initialize", "new_game", "game_new"):
                    continue
                for node in ast.walk(function):
                    if isinstance(node, ast.For) and isinstance(node.iter, ast.Call) and _call_name(node.iter.func) == "range" and node.iter.args:
                        target_name = node.target.id.lower() if isinstance(node.target, ast.Name) else ""
                        if target_name not in ("count", "mine", "bomb", "mine_index", "bomb_index"):
                            continue
                        value = _int_value(node.iter.args[-1])
                        if value and any(
                            isinstance(child, ast.Assign) and any(_subscript_root(target) == "bombs" for target in child.targets)
                            for child in ast.walk(node)
                        ):
                            return value, SourceLocation(path, node.lineno, function.name)
        return None

    def json_display_parameters(self) -> List[Tuple[str, Any, SourceLocation]]:
        result: List[Tuple[str, Any, SourceLocation]] = []
        for path, value in self._json_files():
            if not isinstance(value, Mapping):
                continue
            for key in ("size", "padding", "font", "font_size"):
                if key in value and isinstance(value[key], (str, int, float, bool)):
                    result.append((key, value[key], SourceLocation(path)))
        return result

    def modes(self) -> List[Dict[str, Any]]:
        for path, value in self._json_files():
            colours = value.get("colour") if isinstance(value, Mapping) else None
            if isinstance(colours, Mapping) and len(colours) > 1:
                return [
                    {"id": str(name), "name": str(name).title(), "overrides": {"source_data": "{0}#colour.{1}".format(path, name)}}
                    for name in colours
                ]
        return []

    def has_render_calls(self) -> bool:
        render_names = {"square", "stamp", "dot", "blit", "rect", "write", "pygame.display.update", "pygame.display.flip"}
        return any(
            isinstance(node, ast.Call) and _call_name(node.func) in render_names
            for tree in self.trees.values() for node in ast.walk(tree)
        )

    def _json_files(self) -> Iterable[Tuple[str, Any]]:
        for path in sorted(self.root.rglob("*.json")) if self.root.is_dir() else []:
            if any(part in IGNORED_PARTS for part in path.parts):
                continue
            try:
                yield path.relative_to(self.root).as_posix(), json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue

    def _function_names(self) -> set:
        return set(self._function_map())

    def _function_map(self) -> Dict[str, Tuple[str, ast.AST]]:
        return {
            function.name: (path, function)
            for path, tree in self.trees.items()
            for function in _functions(tree)
        }

    def _assigned_names(self) -> Dict[str, SourceLocation]:
        result: Dict[str, SourceLocation] = {}
        for path, tree in self.trees.items():
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            result[target.id] = SourceLocation(path, node.lineno)
        return result

    def _has_turn_toggle(self) -> bool:
        return any(
            isinstance(node, ast.Dict) and len(node.keys) == 2 and any(
                isinstance(key, ast.Constant) and str(key.value).lower() in ("red", "yellow", "x", "o")
                for key in node.keys if key is not None
            )
            for tree in self.trees.values() for node in ast.walk(tree)
        )


def _import_roots(trees: Iterable[ast.Module]) -> set:
    result = set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                result.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                result.add(node.module.split(".")[0])
    return result


def _module_constants(tree: ast.Module) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        value = _literal_value(node.value, result)
        if value is not None:
            result[node.targets[0].id] = value
    return result


def _literal_value(node: ast.AST, names: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, (ast.Tuple, ast.List)):
        values = [_literal_value(item, names) for item in node.elts]
        return values if all(value is not None for value in values) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _literal_value(node.operand, names)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv)):
        left, right = _literal_value(node.left, names), _literal_value(node.right, names)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right:
                return left // right
    return None


def _nested_board_shape(node: ast.AST, constants: Mapping[str, Any]) -> Optional[Tuple[int, int]]:
    if not isinstance(node, ast.ListComp) or len(node.generators) != 1:
        return None
    outer = _range_count(node.generators[0].iter, constants)
    if not outer:
        return None
    inner = None
    if isinstance(node.elt, ast.ListComp) and len(node.elt.generators) == 1:
        inner = _range_count(node.elt.generators[0].iter, constants)
    elif isinstance(node.elt, ast.BinOp) and isinstance(node.elt.op, ast.Mult):
        inner = _int_value(node.elt.right, constants) or _int_value(node.elt.left, constants)
    if inner and inner > 1 and outer > 1:
        return inner, outer
    return None


def _draw_loop_shape(function: ast.AST, constants: Mapping[str, Any]) -> Optional[Tuple[int, int]]:
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    if function.name.lower() not in ("draw", "grid", "display", "initialize", "create_board", "newgame"):
        return None
    x_counts, y_counts = [], []
    has_grid_call = any(
        isinstance(node, ast.Call) and _call_name(node.func).split(".")[-1] in ("square", "stamp", "rect", "dot", "blit")
        for node in ast.walk(function)
    )
    if not has_grid_call:
        return None
    for outer in ast.walk(function):
        if not isinstance(outer, ast.For) or not isinstance(outer.target, ast.Name):
            continue
        outer_axis = _loop_axis(outer.target.id)
        outer_count = _range_count(outer.iter, constants)
        if not outer_axis or not outer_count:
            continue
        for inner in ast.walk(outer):
            if inner is outer or not isinstance(inner, ast.For) or not isinstance(inner.target, ast.Name):
                continue
            inner_axis = _loop_axis(inner.target.id)
            inner_count = _range_count(inner.iter, constants)
            has_site_draw = any(
                isinstance(child, ast.Call)
                and _call_name(child.func).split(".")[-1] in ("square", "stamp", "rect", "dot", "blit")
                for child in ast.walk(inner)
            )
            if inner_axis and inner_axis != outer_axis and inner_count and has_site_draw:
                counts = {outer_axis: outer_count, inner_axis: inner_count}
                return counts["x"], counts["y"]
    for node in ast.walk(function):
        if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
            continue
        count = _range_count(node.iter, constants)
        if not count:
            continue
        if node.target.id.lower() in ("x", "j", "col", "column"):
            x_counts.append(count)
        elif node.target.id.lower() in ("y", "i", "row"):
            y_counts.append(count)
    if x_counts and y_counts:
        return min(x_counts), min(y_counts)
    return None


def _loop_axis(name: str) -> str:
    token = name.lower()
    if token in ("x", "j", "col", "column"):
        return "x"
    if token in ("y", "i", "row"):
        return "y"
    return ""


def _inside_lattice_shape(function: ast.AST, tree: ast.Module) -> Optional[Tuple[int, int]]:
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or function.name.lower() not in ("inside", "in_bounds", "is_inside"):
        return None
    bounds: Dict[str, Tuple[float, float]] = {}
    for node in ast.walk(function):
        if not isinstance(node, ast.Compare) or len(node.ops) != 2 or len(node.comparators) != 2:
            continue
        low = _number_value(node.left)
        high = _number_value(node.comparators[1])
        middle = node.comparators[0]
        axis = middle.attr.lower() if isinstance(middle, ast.Attribute) else ""
        if axis in ("x", "y") and low is not None and high is not None:
            bounds[axis] = (low, high)
    step = _movement_step([tree])
    if "x" in bounds and "y" in bounds and step:
        size_x = max(1, int((bounds["x"][1] - bounds["x"][0] - 1) // step[0]))
        size_y = max(1, int((bounds["y"][1] - bounds["y"][0] - 1) // step[0]))
        return size_x, size_y
    return None


def _movement_step(trees: Iterable[ast.Module]) -> Optional[Tuple[int, str, int]]:
    candidates: List[Tuple[int, str, int]] = []
    for tree in trees:
        source_path = getattr(tree, "_source_path", "")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node.func).split(".")[-1] == "change":
                values = [abs(value) for value in (_number_value(arg) for arg in node.args) if value not in (None, 0)]
                candidates.extend((int(value), source_path, node.lineno) for value in values if float(value).is_integer())
    if not candidates:
        return None
    value = Counter(item[0] for item in candidates).most_common(1)[0][0]
    return next(item for item in candidates if item[0] == value)


def _range_count(node: ast.AST, constants: Mapping[str, Any]) -> Optional[int]:
    if not isinstance(node, ast.Call) or _call_name(node.func) != "range":
        return None
    args = [_int_value(arg, constants) for arg in node.args]
    if any(value is None for value in args):
        return None
    values = [int(value) for value in args if value is not None]
    try:
        if len(values) == 1:
            return len(range(values[0]))
        if len(values) == 2:
            return len(range(values[0], values[1]))
        if len(values) == 3:
            return len(range(values[0], values[1], values[2]))
    except ValueError:
        return None
    return None


def _functions(tree: ast.Module) -> List[ast.AST]:
    return [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _int_value(node: ast.AST, constants: Optional[Mapping[str, Any]] = None) -> Optional[int]:
    value = _literal_value(node, constants or {})
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _number_value(node: ast.AST) -> Optional[float]:
    value = _literal_value(node, {})
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _string_value(node: ast.AST) -> Optional[str]:
    return str(node.value) if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return "{0}.{1}".format(prefix, node.attr) if prefix else node.attr
    return ""


def _handler_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Lambda):
        calls = [_call_name(item.func) for item in ast.walk(node) if isinstance(item, ast.Call)]
        return calls[0] if calls else "lambda"
    return _call_name(node)


def _subscript_root(node: ast.AST) -> str:
    current = node
    while isinstance(current, ast.Subscript):
        current = current.value
    return current.id if isinstance(current, ast.Name) else ""


def _action(identifier: str, name: str, verb: str, actor: str, source_ref: str, trigger: str) -> Dict[str, Any]:
    return {
        "id": identifier, "name": name, "actor": actor, "verb": verb,
        "target": {"kind": "source_defined"}, "parameters": [],
        "preconditions": [{"op": "source_function", "ref": source_ref}],
        "effects": [{"op": "source_function", "ref": source_ref}],
        "timing": {"trigger": trigger}, "executable": False,
        "semantic_status": "proven", "execution": "source_runtime", "source_ref": source_ref,
    }


def _outcome(identifier: str, status: str, terminal: bool, path: str, line: int, detail: str) -> Dict[str, Any]:
    return {
        "id": identifier, "priority": 100,
        "condition": {"op": "source_predicate", "detail": detail},
        "result": {"status": status, "is_terminal": terminal},
        "executable": False, "semantic_status": "proven", "execution": "source_runtime",
        "source_ref": "{0}:{1}".format(path, line),
    }


def _dimension_priority(detail: str) -> int:
    return {"nested logical board construction": 3, "nested source draw/input lattice": 2, "movement boundary divided by proven spatial step": 1}.get(detail, 0)
