"""Interactive controller for the IR v2 Acceptance Workbench.

The controller has no GUI dependency. Every cell interaction goes through the
compiled Input IR, Rule Runtime and Scene projection owned by Project Session.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from srtp.alphazero_v1 import assess_alphazero_conformance, compile_alphazero_game
from srtp.asset_ir_v2 import load_asset_ir
from srtp.input_ir_v2 import PhysicalInputEvent, load_input_ir
from srtp.integration_gate_v1.reference_fixture import (
    ReferenceGateFixture,
    build_project_artifacts,
    build_reference_gate,
)
from srtp.integration_gate_v1.runner import ProjectArtifacts
from srtp.ir_v2 import (
    assess_rule_ir_conformance,
    load_rule_ir,
    replay_rule_ir,
    seal_rule_ir,
)
from srtp.project_manifest_v2 import compile_project_manifest, load_project_manifest
from srtp.scene_ir_v2 import load_scene_ir


class IRAcceptanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectViewState:
    key: str
    label: str
    project_id: str
    variant: str
    rule_id: str
    dimensions: Tuple[int, ...]
    grid: Any
    current_actor: Optional[str]
    current_actor_name: str
    revision: int
    state_hash: str
    legal_actions: int
    total_actions: int
    outcome_status: str
    terminal: bool
    winners: Tuple[str, ...]
    replay_entries: int
    scene_sites: int
    last_scene_commands: int

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "project_id": self.project_id,
            "variant": self.variant,
            "rule_id": self.rule_id,
            "dimensions": list(self.dimensions),
            "grid": self.grid,
            "current_actor": self.current_actor,
            "current_actor_name": self.current_actor_name,
            "revision": self.revision,
            "state_hash": self.state_hash,
            "legal_actions": self.legal_actions,
            "total_actions": self.total_actions,
            "outcome_status": self.outcome_status,
            "terminal": self.terminal,
            "winners": list(self.winners),
            "replay_entries": self.replay_entries,
            "scene_sites": self.scene_sites,
            "last_scene_commands": self.last_scene_commands,
        }


@dataclass(frozen=True)
class InteractionResult:
    project_key: str
    coordinate: Tuple[int, ...]
    accepted: bool
    code: str
    message: str
    transition_count: int
    scene_command_count: int
    state: ProjectViewState

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "project_key": self.project_key,
            "coordinate": list(self.coordinate),
            "accepted": self.accepted,
            "code": self.code,
            "message": self.message,
            "transition_count": self.transition_count,
            "scene_command_count": self.scene_command_count,
            "state": self.state.to_mapping(),
        }


class IRAcceptanceController:
    """Own compiled Project Sessions used by a renderer/editor front end."""

    def __init__(
        self, repository_root: Optional[Path] = None, *,
        autoload_reference: bool = True,
    ) -> None:
        self.repository_root = Path(
            repository_root or Path(__file__).resolve().parents[1]
        ).resolve()
        self.fixture: Optional[ReferenceGateFixture] = None
        self.projects: Dict[str, Any] = {}
        self.bundles: Dict[str, Any] = {}
        self.sessions: Dict[str, Any] = {}
        self.labels: Dict[str, str] = {}
        self.sequence: Dict[str, int] = {}
        self.last_scene_commands: Dict[str, int] = {}
        self.activity: List[str] = []
        self.active_key = ""
        if autoload_reference:
            self.load_reference()

    @property
    def project_keys(self) -> Tuple[str, ...]:
        ordered = [key for key in ("source", "target", "loaded") if key in self.projects]
        ordered.extend(sorted(set(self.projects) - set(ordered)))
        return tuple(ordered)

    @property
    def has_active_project(self) -> bool:
        return bool(self.active_key and self.active_key in self.sessions)

    def load_reference(self) -> None:
        self.close()
        self.fixture = build_reference_gate(self.repository_root)
        self.projects = {
            "source": self.fixture.source,
            "target": self.fixture.target,
        }
        self.labels = {
            "source": "Source 2D",
            "target": "Target 3D",
        }
        self.bundles = {
            "source": self._compile(self.fixture.source),
            "target": self._compile(
                self.fixture.target, source_manifest=self.fixture.source.manifest,
            ),
        }
        self.sessions = {}
        self.sequence = {}
        self.last_scene_commands = {}
        for key in ("source", "target"):
            self._new_session(key)
        self.active_key = "source"
        self.activity = [
            "Loaded sealed 2D source and 3D target reference Projects.",
            "All interactions use Input IR → Rule Runtime → Scene projection.",
        ]

    def open_rule_preview(self, path: Path) -> str:
        rule_path = Path(path).resolve()
        try:
            document = load_rule_ir(rule_path)
        except Exception as exc:
            raise IRAcceptanceError("Could not load Rule IR: {0}".format(exc)) from exc
        if not document.get("content_hash"):
            document = seal_rule_ir(document)
        report = assess_rule_ir_conformance(document)
        if not report.compile_ready:
            detail = next(
                (item.message for item in report.diagnostics if item.severity == "error"),
                "Rule IR is not compile-ready.",
            )
            raise IRAcceptanceError(detail)
        self._require_preview_contract(document)
        slug = _slug(str(document.get("document_id", "loaded_rule")))
        artifacts = build_project_artifacts(
            document, "project:preview.{0}".format(slug), self.repository_root,
        )
        if "loaded" in self.sessions:
            self.sessions.pop("loaded").close()
        self.projects["loaded"] = artifacts
        self.labels["loaded"] = "Loaded Rule IR"
        self.bundles["loaded"] = self._compile(artifacts)
        self._new_session("loaded")
        self.active_key = "loaded"
        self.activity.append("Opened Rule IR preview: {0}".format(rule_path.name))
        return "loaded"

    def open_project_bundle(
        self, path: Path, asset_project_root: Optional[Path] = None,
    ) -> str:
        """Open a sealed five-document Project bundle from one directory.

        This is the product bridge expected by the future LLM compiler.  The
        compiler may choose any filenames; documents are matched by their
        declared IDs and hashes rather than by filename.
        """

        selected = Path(path).resolve()
        root = selected if selected.is_dir() else selected.parent
        manifest_path = selected if selected.is_file() else None
        candidates = _json_documents(root)
        if manifest_path is None:
            manifest_path = _find_document_path(
                candidates, lambda item: str(item.get("manifest_version", "")).startswith(
                    "cubeengine.project-manifest."
                ),
            )
        if manifest_path is None:
            raise IRAcceptanceError(
                "No Project Manifest was found. Select a folder containing the sealed "
                "Project Manifest plus Rule, Scene, Asset and Input IR files."
            )
        try:
            manifest = load_project_manifest(manifest_path)
        except Exception as exc:
            raise IRAcceptanceError("Could not load Project Manifest: {0}".format(exc)) from exc
        documents = {}
        loaders = {
            "rule_ir": load_rule_ir,
            "scene_ir": load_scene_ir,
            "asset_ir": load_asset_ir,
            "input_ir": load_input_ir,
        }
        for slot, loader in loaders.items():
            pin = manifest.get("documents", {}).get(slot, {})
            document_path = _find_document_path(
                candidates,
                lambda item, pin=pin: (
                    item.get("document_id") == pin.get("document_id")
                    and item.get("content_hash") == pin.get("content_hash")
                ),
            )
            if document_path is None:
                raise IRAcceptanceError(
                    "Project bundle is missing the pinned {0} document.".format(slot)
                )
            try:
                documents[slot] = loader(document_path)
            except Exception as exc:
                raise IRAcceptanceError(
                    "Could not load {0}: {1}".format(slot, exc)
                ) from exc
        source_manifest = None
        source_pin = manifest.get("source_manifest")
        if isinstance(source_pin, Mapping):
            source_path = _find_document_path(
                candidates,
                lambda item: (
                    item.get("project_id") == source_pin.get("project_id")
                    and item.get("content_hash") == source_pin.get("content_hash")
                ),
                exclude=manifest_path,
            )
            if source_path is None:
                raise IRAcceptanceError(
                    "Target Project requires its pinned source Project Manifest in the same bundle."
                )
            source_manifest = load_project_manifest(source_path)
        artifacts = ProjectArtifacts(
            manifest=manifest,
            rule=documents["rule_ir"],
            scene=documents["scene_ir"],
            asset=documents["asset_ir"],
            input=documents["input_ir"],
            asset_project_root=Path(asset_project_root).resolve() if asset_project_root else root,
        )
        # #region agent log
        try:
            import time as _time
            _scene = documents.get("scene_ir") or {}
            _prefabs = [str(item.get("id")) for item in (_scene.get("prefabs") or []) if isinstance(item, Mapping)]
            _viz = []
            for _node in _scene.get("nodes") or []:
                if not isinstance(_node, Mapping):
                    continue
                for _comp in _node.get("components") or []:
                    if isinstance(_comp, Mapping) and _comp.get("type") in (
                        "topology_visualizer", "rule_entity_visualizer",
                    ):
                        _props = _comp.get("properties") if isinstance(_comp.get("properties"), Mapping) else {}
                        _viz.append({
                            "node": _node.get("id"),
                            "kind": _comp.get("type"),
                            "prefab": _props.get("prefab"),
                        })
            with open("debug-f3e2af.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps({
                    "sessionId": "f3e2af", "runId": "pre-fix", "hypothesisId": "A,B,E",
                    "location": "ir_acceptance.py:open_project_bundle",
                    "message": "bundle scene prefab contract before compile",
                    "data": {
                        "root": str(root),
                        "asset_project_root": str(artifacts.asset_project_root),
                        "prefab_ids": _prefabs,
                        "visualizers": _viz,
                    },
                    "timestamp": int(_time.time() * 1000),
                }) + "\n")
        except Exception:
            pass
        # #endregion
        if "loaded" in self.sessions:
            self.sessions.pop("loaded").close()
        self.projects["loaded"] = artifacts
        title = manifest.get("metadata", {}).get("title") or manifest.get("project_id")
        self.labels["loaded"] = "Project · {0}".format(title)
        self.bundles["loaded"] = self._compile(
            artifacts, source_manifest=source_manifest,
        )
        self._new_session("loaded")
        self.active_key = "loaded"
        self.activity.append("Opened sealed Project bundle: {0}".format(manifest_path.name))
        return "loaded"

    def select_project(self, key: str) -> ProjectViewState:
        if key not in self.projects:
            raise IRAcceptanceError("Unknown Workbench project: " + str(key))
        self.active_key = key
        return self.snapshot(key)

    def reset(self, key: Optional[str] = None) -> ProjectViewState:
        selected = key or self.active_key
        if selected not in self.bundles:
            raise IRAcceptanceError("Unknown Workbench project: " + str(selected))
        if selected in self.sessions:
            self.sessions.pop(selected).close()
        self._new_session(selected)
        self.activity.append("Reset {0}.".format(self.labels[selected]))
        return self.snapshot(selected)

    def click(self, coordinate: Sequence[int], key: Optional[str] = None) -> InteractionResult:
        selected = key or self.active_key
        state = self.snapshot(selected)
        coord = tuple(int(item) for item in coordinate)
        if len(coord) != len(state.dimensions) or any(
            value < 0 or value >= state.dimensions[index]
            for index, value in enumerate(coord)
        ):
            raise IRAcceptanceError("Coordinate is outside the active topology.")
        self.sequence[selected] += 1
        session = self.sessions[selected]
        result = session.handle_input(PhysicalInputEvent(
            self.sequence[selected], "mouse", "mouse.button.primary", "press",
            position=(0, 0), data={"rule_coordinate": list(coord)},
        ))
        accepted = bool(result.transitions) and not result.rejections
        if accepted:
            code = "transition_committed"
            message = "Accepted {0}; Rule revision is now {1}.".format(
                coord, session.rule_runtime.state.revision,
            )
        elif result.rejections:
            code = result.rejections[0].code
            message = result.rejections[0].message
        else:
            code = "input_not_resolved"
            message = "Input IR did not resolve this event to a Rule action."
        self.last_scene_commands[selected] = len(result.scene_delta.commands)
        marker = "ACCEPT" if accepted else "REJECT"
        self.activity.append("{0} {1} {2}: {3}".format(
            marker, self.labels[selected], coord, code,
        ))
        return InteractionResult(
            selected, coord, accepted, code, message,
            len(result.transitions), len(result.scene_delta.commands),
            self.snapshot(selected),
        )

    def dispatch_physical(
        self,
        event: PhysicalInputEvent,
        key: Optional[str] = None,
        *,
        focus: str = "viewport",
    ) -> InteractionResult:
        """Forward a host physical event through Input IR → Rule → Scene."""

        selected = key or self.active_key
        if selected not in self.sessions:
            raise IRAcceptanceError("Unknown Workbench project: " + str(selected))
        session = self.sessions[selected]
        result = session.handle_input(event, focus=focus)
        accepted = bool(result.transitions) and not result.rejections
        label = getattr(event, "control", "input")
        if accepted:
            code = "transition_committed"
            message = "Accepted {0}; Rule revision is now {1}.".format(
                label, session.rule_runtime.state.revision,
            )
        elif result.rejections:
            code = result.rejections[0].code
            message = result.rejections[0].message
        else:
            code = "input_not_resolved"
            message = "Input IR did not resolve this event to a Rule action."
        self.last_scene_commands[selected] = len(result.scene_delta.commands)
        marker = "ACCEPT" if accepted else "REJECT"
        self.activity.append("{0} {1} {2}: {3}".format(
            marker, self.labels[selected], label, code,
        ))
        return InteractionResult(
            selected, (), accepted, code, message,
            len(result.transitions), len(result.scene_delta.commands),
            self.snapshot(selected),
        )

    def snapshot(self, key: Optional[str] = None) -> ProjectViewState:
        selected = key or self.active_key
        if selected not in self.sessions:
            raise IRAcceptanceError("Unknown Workbench project: " + str(selected))
        artifacts = self.projects[selected]
        session = self.sessions[selected]
        runtime = session.rule_runtime
        if not runtime.state.topologies or not runtime.state.grids:
            raise IRAcceptanceError("Workbench preview requires a topology-site grid.")
        topology_id, dimensions = next(iter(runtime.state.topologies.items()))
        _, grid = next(iter(runtime.state.grids.items()))
        outcome = runtime.evaluate_outcome()
        participant_names = {
            str(item["id"]): str(item.get("name", item["id"]))
            for item in artifacts.rule.get("participants", [])
        }
        actor = runtime.state.current_actor
        winners = tuple(participant_names.get(str(item), str(item)) for item in outcome.winners)
        scene_sites = sum(len(items) for items in session.scene.topology_sites.values())
        return ProjectViewState(
            key=selected,
            label=self.labels[selected],
            project_id=str(artifacts.manifest["project_id"]),
            variant=str(artifacts.manifest["variant"]),
            rule_id=str(artifacts.rule["document_id"]),
            dimensions=tuple(int(item) for item in dimensions),
            grid=grid.tolist(),
            current_actor=actor,
            current_actor_name=participant_names.get(str(actor), str(actor or "N/A")),
            revision=int(runtime.state.revision),
            state_hash=runtime.state.state_hash(),
            legal_actions=len(runtime.legal_actions()),
            total_actions=runtime.action_count,
            outcome_status=str(outcome.status),
            terminal=bool(outcome.terminal),
            winners=winners,
            replay_entries=len(runtime.export_replay_trace()),
            scene_sites=scene_sites,
            last_scene_commands=self.last_scene_commands.get(selected, 0),
        )

    def rule_summary(self, key: Optional[str] = None) -> Mapping[str, Any]:
        selected = key or self.active_key
        if selected not in self.projects:
            raise IRAcceptanceError("No compiled Project is active.")
        rule = self.projects[selected].rule
        topologies = []
        for item in rule.get("topologies", []):
            axes = item.get("axes", [])
            topologies.append({
                "id": item.get("id"),
                "name": item.get("name"),
                "kind": item.get("kind"),
                "anchor": item.get("anchor"),
                "dimensions": [axis.get("extent") for axis in axes],
            })
        return {
            "document_id": rule.get("document_id"),
            "title": rule.get("metadata", {}).get("title", ""),
            "parameters": [
                {
                    "id": item.get("id"), "name": item.get("name"),
                    "type": item.get("type"), "default": item.get("default"),
                    "minimum": item.get("minimum"), "maximum": item.get("maximum"),
                    "choices": item.get("choices", []),
                }
                for item in rule.get("parameters", [])
            ],
            "modes": [
                {"id": item.get("id"), "name": item.get("name")}
                for item in rule.get("modes", [])
            ],
            "topologies": topologies,
            "actions": [
                {"id": item.get("id"), "name": item.get("name")}
                for item in rule.get("actions", [])
            ],
            "outcomes": [
                {"id": item.get("id"), "name": item.get("name")}
                for item in rule.get("outcomes", [])
            ],
            "unresolved": list(rule.get("unresolved", [])),
        }

    def legal_coordinates(self, key: Optional[str] = None) -> Tuple[Tuple[int, ...], ...]:
        selected = key or self.active_key
        values = []
        for action in self.sessions[selected].rule_runtime.legal_actions():
            target = action.parameters.get("target")
            if isinstance(target, (list, tuple)):
                values.append(tuple(int(item) for item in target))
        return tuple(values)

    def verify_replay(self, key: Optional[str] = None) -> Mapping[str, Any]:
        selected = key or self.active_key
        session = self.sessions[selected]
        trace = tuple(item.to_mapping() for item in session.rule_runtime.export_replay_trace())
        replay = replay_rule_ir(self.projects[selected].rule, trace)
        try:
            expected = session.rule_runtime.state.state_hash()
            actual = replay.state.state_hash()
        finally:
            replay.close()
        passed = actual == expected
        self.activity.append("Replay {0}: {1}.".format(
            self.labels[selected], "PASS" if passed else "FAIL",
        ))
        return {
            "passed": passed,
            "entries": len(trace),
            "expected_state_hash": expected,
            "actual_state_hash": actual,
        }

    def run_integration_gate(self) -> Mapping[str, Any]:
        if self.fixture is None:
            raise IRAcceptanceError("Reference Integration Gate is not loaded.")
        report = self.fixture.run().to_mapping()
        self.activity.append("Non-LLM Integration Gate: {0}.".format(
            "PASS" if report["passed"] else "FAIL",
        ))
        return report

    def run_ai_conformance(self) -> Mapping[str, Any]:
        if self.fixture is None:
            raise IRAcceptanceError("Reference AI Adapter is not loaded.")
        if self.active_key != "target":
            raise IRAcceptanceError(
                "The AlphaZero Adapter is pinned to the built-in Target 3D Project. "
                "Switch the Project selector to Target 3D first."
            )
        game = compile_alphazero_game(
            self.fixture.target.rule, self.fixture.ai_manifest,
        )
        report = assess_alphazero_conformance(game, maximum_plies=64).to_mapping()
        self.activity.append("AlphaZero nine-API conformance: {0}.".format(
            "PASS" if report["passed"] else "FAIL",
        ))
        return report

    def activity_text(self, maximum: int = 80) -> str:
        return "\n".join(self.activity[-maximum:])

    def close(self) -> None:
        for session in list(getattr(self, "sessions", {}).values()):
            session.close()
        self.sessions = {}

    def _compile(self, artifacts: Any, source_manifest: Optional[Mapping[str, Any]] = None):
        registry = self.fixture.extension_registry if self.fixture is not None else None
        return compile_project_manifest(
            artifacts.manifest,
            rule_document=artifacts.rule,
            scene_document=artifacts.scene,
            asset_document=artifacts.asset,
            input_document=artifacts.input,
            asset_project_root=artifacts.asset_project_root,
            source_manifest=source_manifest,
            extension_registry=registry,
        )

    def _new_session(self, key: str) -> None:
        session = self.bundles[key].create_session()
        self.sessions[key] = session
        self.sequence[key] = 0
        self.last_scene_commands[key] = len(session.initial_scene_delta.commands)

    @staticmethod
    def _require_preview_contract(document: Mapping[str, Any]) -> None:
        topology_ids = {str(item.get("id")) for item in document.get("topologies", [])}
        state_ids = {str(item.get("id")) for item in document.get("state", {}).get("variables", [])}
        action_ids = {str(item.get("id")) for item in document.get("actions", [])}
        required = (
            "rule:topology.board" in topology_ids,
            "rule:state.board_cell" in state_ids,
            "rule:action.place" in action_ids,
        )
        if not all(required):
            raise IRAcceptanceError(
                "Rule-only preview currently requires rule:topology.board, "
                "rule:state.board_cell and rule:action.place. A full Project "
                "package is required for other mechanics."
            )


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not result or not result[0].isalpha():
        result = "rule_" + result
    return result[:80]


def _json_documents(root: Path) -> Mapping[Path, Mapping[str, Any]]:
    documents: Dict[Path, Mapping[str, Any]] = {}
    for path in sorted(root.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, Mapping):
            documents[path.resolve()] = value
    return documents


def _find_document_path(
    documents: Mapping[Path, Mapping[str, Any]], predicate, *,
    exclude: Optional[Path] = None,
) -> Optional[Path]:
    excluded = Path(exclude).resolve() if exclude is not None else None
    matches = [
        path for path, document in documents.items()
        if path != excluded and predicate(document)
    ]
    if len(matches) > 1:
        raise IRAcceptanceError(
            "Project bundle contains more than one document matching a sealed pin."
        )
    return matches[0] if matches else None
