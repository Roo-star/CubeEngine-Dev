"""Source-game package model for fidelity-first SRTP ingestion.

Function 1 does not replace a game with a generic board.  It inventories an
entire runnable source project, records what can be proven from that project,
and keeps the original runtime available as the fidelity reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .report import Diagnostic, ParseReport


@dataclass(frozen=True)
class SourceLocation:
    path: str
    line: Optional[int] = None
    symbol: str = ""

    def to_mapping(self) -> Dict[str, Any]:
        value: Dict[str, Any] = {"path": self.path}
        if self.line is not None:
            value["line"] = int(self.line)
        if self.symbol:
            value["symbol"] = self.symbol
        return value


@dataclass
class SourceParameter:
    """A setting proven to exist in the source, not a universal designer knob."""

    id: str
    label: str
    category: str
    value: Any
    value_type: str
    applicability: str
    edit_mode: str
    reason: str
    locations: List[SourceLocation] = field(default_factory=list)
    constraints: Dict[str, Any] = field(default_factory=dict)
    affects: List[str] = field(default_factory=list)

    @property
    def safely_editable(self) -> bool:
        return self.applicability == "applicable" and self.edit_mode in {
            "runtime_argument", "data_file", "adapter_setting"
        }

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "category": self.category,
            "value": self.value,
            "value_type": self.value_type,
            "applicability": self.applicability,
            "edit_mode": self.edit_mode,
            "safely_editable": self.safely_editable,
            "reason": self.reason,
            "locations": [item.to_mapping() for item in self.locations],
            "constraints": self.constraints,
            "affects": self.affects,
        }


@dataclass
class RuntimeSpec:
    kind: str
    command: List[str]
    cwd: str
    framework: str
    language: str
    dependencies: List[str] = field(default_factory=list)
    missing_dependencies: List[str] = field(default_factory=list)
    window_mode: str = "external"
    compatibility_notes: List[str] = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        return bool(self.command) and not self.missing_dependencies and self.kind != "unsupported"

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "command": list(self.command),
            "cwd": self.cwd,
            "framework": self.framework,
            "language": self.language,
            "dependencies": list(self.dependencies),
            "missing_dependencies": list(self.missing_dependencies),
            "window_mode": self.window_mode,
            "compatibility_notes": list(self.compatibility_notes),
            "runnable": self.runnable,
        }


@dataclass
class MechanicLift:
    id: str
    label: str
    source_behavior: str
    z_behavior: str
    status: str
    reason: str
    source_locations: List[SourceLocation] = field(default_factory=list)

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "source_behavior": self.source_behavior,
            "z_behavior": self.z_behavior,
            "status": self.status,
            "reason": self.reason,
            "source_locations": [item.to_mapping() for item in self.source_locations],
        }


@dataclass
class TransformationPlan:
    source_dimensions: Dict[str, Optional[int]]
    target_dimensions: Dict[str, Optional[int]]
    preserve_x: bool = True
    preserve_y: bool = True
    renderer_policy: str = "preserve_source_identity"
    lifts: List[MechanicLift] = field(default_factory=list)

    @property
    def readiness(self) -> str:
        statuses = {item.status for item in self.lifts}
        if "blocked" in statuses:
            return "blocked"
        if statuses.intersection({"needs_adapter", "needs_llm", "designer_decision"}):
            return "partial"
        return "ready" if self.lifts else "unresolved"

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "source_dimensions": self.source_dimensions,
            "target_dimensions": self.target_dimensions,
            "preserve_x": self.preserve_x,
            "preserve_y": self.preserve_y,
            "renderer_policy": self.renderer_policy,
            "readiness": self.readiness,
            "lifts": [item.to_mapping() for item in self.lifts],
        }


@dataclass
class AnalysisCoverage:
    categories: Dict[str, str]

    @property
    def understood(self) -> int:
        return sum(value == "proven" for value in self.categories.values())

    @property
    def total(self) -> int:
        return sum(value != "not_applicable" for value in self.categories.values())

    @property
    def ratio(self) -> float:
        return self.understood / self.total if self.total else 0.0

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "categories": dict(self.categories),
            "proven": self.understood,
            "total": self.total,
            "ratio": round(self.ratio, 3),
        }


@dataclass
class SourceGamePackage:
    title: str
    root: Path
    entrypoint: Path
    runtime: RuntimeSpec
    rule_report: ParseReport
    parameters: List[SourceParameter]
    transformation: TransformationPlan
    coverage: AnalysisCoverage
    files: List[str] = field(default_factory=list)
    assets: List[str] = field(default_factory=list)
    license_name: str = "unknown"
    license_path: str = ""
    upstream_url: str = ""
    diagnostics: List[Diagnostic] = field(default_factory=list)

    @property
    def original_preview_status(self) -> str:
        return "runnable" if self.runtime.runnable else "blocked"

    @property
    def needs_llm(self) -> bool:
        if self.rule_report.needs_llm:
            return True
        if any(item.requires_llm for item in self.diagnostics):
            return True
        return any(item.status == "needs_llm" for item in self.transformation.lifts)

    def parameter(self, identifier: str) -> Optional[SourceParameter]:
        return next((item for item in self.parameters if item.id == identifier), None)

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "package_version": "cubeengine.srtp/source-game-package-v1",
            "title": self.title,
            "root": str(self.root),
            "entrypoint": str(self.entrypoint),
            "original_preview_status": self.original_preview_status,
            "runtime": self.runtime.to_mapping(),
            "license": {
                "name": self.license_name,
                "path": self.license_path,
                "upstream_url": self.upstream_url,
            },
            "inventory": {"files": self.files, "assets": self.assets},
            "analysis_coverage": self.coverage.to_mapping(),
            "source_parameters": [item.to_mapping() for item in self.parameters],
            "rule_report": self.rule_report.to_mapping(),
            "transformation": self.transformation.to_mapping(),
            "diagnostics": [item.to_mapping() for item in self.diagnostics],
            "needs_llm": self.needs_llm,
        }


def coverage_from_schema(schema: Mapping[str, Any], proven_overrides: Optional[Sequence[str]] = None) -> AnalysisCoverage:
    """Build a designer-facing coverage gate instead of a misleading success flag."""

    dimensions = schema.get("space", {}).get("dimensions", {}) if isinstance(schema.get("space"), Mapping) else {}
    actions = schema.get("actions") if isinstance(schema.get("actions"), list) else []
    outcomes = schema.get("outcomes") if isinstance(schema.get("outcomes"), list) else []
    goals = schema.get("goals") if isinstance(schema.get("goals"), list) else []
    source_project = schema.get("extensions", {}).get("source_project", {}) if isinstance(schema.get("extensions"), Mapping) else {}
    complete_goal = bool(outcomes) and bool(goals) and all(
        not isinstance(goal, Mapping) or goal.get("executable", True) is not False
        for goal in goals
    )
    categories = {
        "space": "proven" if all(isinstance(dimensions.get(axis), int) for axis in ("x", "y")) else "unresolved",
        "entities/state": "proven" if schema.get("entities") or schema.get("state", {}).get("variables") else "partial",
        "input/actions": "proven" if actions else "unresolved",
        "temporal flow": "proven" if schema.get("flow", {}).get("model") not in (None, "unknown") else "unresolved",
        "randomness": "proven" if schema.get("randomness", {}).get("model") != "unknown" else "unresolved",
        "goals/outcomes": "proven" if complete_goal else "partial" if outcomes or goals else "unresolved",
        "visual/assets": "proven" if source_project.get("visual_source_proven") else "partial",
        "modes": "proven" if source_project.get("source_modes_proven") else "not_applicable",
    }
    for category in proven_overrides or ():
        if category in categories:
            categories[category] = "proven"
    return AnalysisCoverage(categories)
