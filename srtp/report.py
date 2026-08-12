"""Structured evidence and diagnostics for SRTP Function 1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class SourceEvidence:
    """Why a canonical field has a particular value."""

    method: str
    source: str
    confidence: float
    detail: str = ""
    line: Optional[int] = None

    def to_mapping(self) -> Dict[str, Any]:
        value: Dict[str, Any] = {
            "method": self.method,
            "source": self.source,
            "confidence": round(float(self.confidence), 3),
        }
        if self.detail:
            value["detail"] = self.detail
        if self.line is not None:
            value["line"] = self.line
        return value


@dataclass(frozen=True)
class Diagnostic:
    """A designer-facing validation or inference result."""

    severity: str
    code: str
    path: str
    message: str
    evidence: str = ""
    suggestion: str = ""
    requires_llm: bool = False

    def to_mapping(self) -> Dict[str, Any]:
        value: Dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "requires_llm": self.requires_llm,
        }
        if self.evidence:
            value["evidence"] = self.evidence
        if self.suggestion:
            value["suggestion"] = self.suggestion
        return value


@dataclass
class ParseReport:
    """Complete output from one file-read attempt."""

    schema: Dict[str, Any]
    diagnostics: List[Diagnostic] = field(default_factory=list)
    provenance: Dict[str, List[SourceEvidence]] = field(default_factory=dict)
    source_path: str = ""
    source_format: str = ""
    readiness: str = "partial"

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.diagnostics)

    @property
    def needs_llm(self) -> bool:
        return any(item.requires_llm for item in self.diagnostics)

    @property
    def previewable(self) -> bool:
        space = self.schema.get("space", {})
        dimensions = space.get("dimensions", {}) if isinstance(space, Mapping) else {}
        raw_actions = self.schema.get("actions", [])
        executable_actions = [
            item for item in (raw_actions if isinstance(raw_actions, list) else [])
            if isinstance(item, Mapping) and item.get("executable", True)
        ]
        needs_actor_state = any(
            effect.get("value") == "$actor_state"
            for action in executable_actions
            for effect in action.get("effects", [])
            if isinstance(effect, Mapping)
        )
        raw_entities = self.schema.get("entities", [])
        has_actor_state = any(
            isinstance(entity, Mapping) and isinstance(entity.get("state_value"), int)
            for entity in (raw_entities if isinstance(raw_entities, list) else [])
        )
        return (
            not self.has_errors
            and all(isinstance(dimensions.get(axis), int) and dimensions[axis] > 0 for axis in ("x", "y", "z"))
            and bool(executable_actions)
            and (not needs_actor_state or has_actor_state)
        )

    def add_evidence(self, path: str, evidence: SourceEvidence) -> None:
        self.provenance.setdefault(path, []).append(evidence)

    def add_diagnostic(self, diagnostic: Diagnostic) -> None:
        self.diagnostics.append(diagnostic)

    def diagnostics_text(self) -> str:
        if not self.diagnostics:
            return "No diagnostics. Rule Schema is complete for Function 1."
        lines = []
        for item in self.diagnostics:
            route = " -> LLM handoff" if item.requires_llm else ""
            lines.append(
                "[{0}] {1} {2}: {3}{4}".format(
                    item.severity.upper(), item.code, item.path, item.message, route
                )
            )
            if item.evidence:
                lines.append("  evidence: {0}".format(item.evidence))
            if item.suggestion:
                lines.append("  suggestion: {0}".format(item.suggestion))
        return "\n".join(lines)

    def llm_handoff(self) -> Dict[str, Any]:
        """Machine-readable package reserved for SRTP Function 2."""

        return {
            "handoff_version": "cubeengine.srtp/llm-handoff-v2",
            "task": "complete_rule_ir_from_source_evidence",
            "source": {
                "path": self.source_path,
                "format": self.source_format,
            },
            "partial_schema": self.schema,
            "unresolved": [
                item.to_mapping()
                for item in self.diagnostics
                if item.requires_llm
            ],
            "provenance": {
                path: [item.to_mapping() for item in evidence]
                for path, evidence in self.provenance.items()
            },
            "required_outputs": [
                "rule_ir_patch",
                "scene_ir_patch",
                "asset_role_bindings",
                "input_contract",
                "spatial_lift_proposals",
                "acceptance_tests",
                "clarification_questions",
            ],
            "hard_constraints": [
                "Cite source file and line evidence for every inferred mechanic.",
                "Do not overwrite or execute the source project.",
                "Use unresolved when evidence is insufficient; do not invent rules.",
                "Runtime legality and outcomes must be deterministic compiled logic, not live LLM decisions.",
                "A proposed 3D lift must preserve the proven 2D behavior when z equals one.",
            ],
            "downstream_game_api": [
                "getInitBoard",
                "getBoardSize",
                "getActionSize",
                "getNextState",
                "getValidMoves",
                "getGameEnded",
                "getCanonicalForm",
                "getSymmetries",
                "stringRepresentation",
            ],
        }

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "readiness": self.readiness,
            "previewable": self.previewable,
            "needs_llm": self.needs_llm,
            "source_path": self.source_path,
            "source_format": self.source_format,
            "schema": self.schema,
            "diagnostics": [item.to_mapping() for item in self.diagnostics],
            "provenance": {
                path: [item.to_mapping() for item in evidence]
                for path, evidence in self.provenance.items()
            },
        }


def deduplicate_diagnostics(items: Sequence[Diagnostic]) -> List[Diagnostic]:
    result: List[Diagnostic] = []
    seen = set()
    for item in items:
        key = (item.severity, item.code, item.path, item.message)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
