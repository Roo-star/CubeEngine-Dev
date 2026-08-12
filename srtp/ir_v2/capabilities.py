"""Honest executable capability profile for the Rule Runtime alpha."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Mapping

from .rule_ir import RuleIRDiagnostic


RULE_RUNTIME_CAPABILITY_PATH = Path(__file__).with_name("rule-runtime-capabilities.json")
RULE_RUNTIME_CAPABILITIES = json.loads(RULE_RUNTIME_CAPABILITY_PATH.read_text(encoding="utf-8"))
RULE_RUNTIME_CAPABILITY_ID = str(RULE_RUNTIME_CAPABILITIES["capability_id"])


def runtime_capability_diagnostics(document: Mapping[str, Any]) -> List[RuleIRDiagnostic]:
    diagnostics: List[RuleIRDiagnostic] = []
    for index, topology in enumerate(document.get("topologies", [])):
        if not isinstance(topology, Mapping):
            continue
        path = "/topologies/{0}".format(index)
        if topology.get("kind") not in RULE_RUNTIME_CAPABILITIES["topology"]["kinds"]:
            diagnostics.append(_error("capability.topology_kind", path + "/kind", "Rule Runtime alpha executes rect_grid topologies only."))
        if topology.get("anchor") not in RULE_RUNTIME_CAPABILITIES["topology"]["anchors"]:
            diagnostics.append(_error("capability.topology_anchor", path + "/anchor", "Rule Runtime alpha executes cell or vertex anchors only."))
        for axis_index, axis in enumerate(topology.get("axes", [])):
            if isinstance(axis, Mapping) and axis.get("boundary") not in RULE_RUNTIME_CAPABILITIES["topology"]["boundaries"]:
                diagnostics.append(_error(
                    "capability.boundary", path + "/axes/{0}/boundary".format(axis_index),
                    "Rule Runtime alpha executes bounded axes only; wrap/reflect/open require a future capability.",
                ))

    flow = document.get("flow", {})
    if isinstance(flow, Mapping):
        if flow.get("model") not in RULE_RUNTIME_CAPABILITIES["flow"]["models"]:
            diagnostics.append(_error(
                "capability.flow_model", "/flow/model",
                "Rule Runtime does not execute this flow model.",
            ))
        scheduler = flow.get("scheduler", {})
        if isinstance(scheduler, Mapping) and scheduler.get("clock") not in RULE_RUNTIME_CAPABILITIES["flow"]["clocks"]:
            diagnostics.append(_error(
                "capability.clock", "/flow/scheduler/clock",
                "Rule Runtime does not execute this scheduler clock.",
            ))

    information_model = document.get("state", {}).get("information_model")
    if information_model in ("imperfect", "hidden", "mixed"):
        diagnostics.append(_error(
            "capability.information_model", "/state/information_model",
            "Observation projection for non-perfect information is not implemented.",
        ))

    for index, system in enumerate(document.get("systems", [])):
        if not isinstance(system, Mapping):
            continue
        trigger = system.get("trigger", {})
        kind = trigger.get("kind") if isinstance(trigger, Mapping) else None
        if kind not in RULE_RUNTIME_CAPABILITIES["flow"]["system_triggers"]:
            diagnostics.append(_error(
                "capability.system_trigger", "/systems/{0}/trigger/kind".format(index),
                "Rule Runtime does not execute this system trigger.",
            ))

    for index, action in enumerate(document.get("actions", [])):
        if not isinstance(action, Mapping):
            continue
        encoding = action.get("encoding", {})
        kind = encoding.get("kind") if isinstance(encoding, Mapping) else None
        if kind not in RULE_RUNTIME_CAPABILITIES["action_encodings"]:
            diagnostics.append(_error(
                "capability.action_encoding", "/actions/{0}/encoding/kind".format(index),
                "Runtime-enumerated action catalogues are not implemented.",
            ))

    return diagnostics


def _error(code: str, path: str, message: str) -> RuleIRDiagnostic:
    return RuleIRDiagnostic("error", code, path, message)
