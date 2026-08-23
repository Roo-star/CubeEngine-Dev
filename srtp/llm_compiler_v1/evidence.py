"""Bounded evidence packs derived from Function 1 LLM handoff."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Dict, List, Mapping

from srtp.source_game import SourceGamePackage

PROMPT_TEMPLATE_VERSION = "cubeengine.srtp/llm-prompt/1.0"
DEFAULT_MAX_CHARS = 24000


def source_package_hash(handoff: Mapping[str, Any]) -> str:
    payload = json.dumps(
        handoff, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_evidence_pack(
    package: SourceGamePackage,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Dict[str, Any]:
    """Trim a whole-project handoff into a bounded, citation-friendly pack.

    Full source file bodies are never placed in the system channel. Paths,
    parameters, diagnostics and short static-analysis summaries are included.
    """

    handoff = package.llm_handoff()
    package_hash = source_package_hash(handoff)
    static = handoff.get("static_analysis") if isinstance(handoff.get("static_analysis"), Mapping) else {}
    coverage = handoff.get("coverage") if isinstance(handoff.get("coverage"), Mapping) else {}
    parameters = handoff.get("source_parameters") if isinstance(handoff.get("source_parameters"), list) else []
    gaps = handoff.get("transformation_gaps") if isinstance(handoff.get("transformation_gaps"), list) else []
    diagnostics = handoff.get("project_diagnostics") if isinstance(handoff.get("project_diagnostics"), list) else []
    inventory = {}
    source_project = handoff.get("source_project")
    if isinstance(source_project, Mapping):
        inv = source_project.get("inventory")
        if isinstance(inv, Mapping):
            inventory = {
                "files": list(inv.get("files") or [])[:80],
                "assets": list(inv.get("assets") or [])[:80],
            }

    pack: Dict[str, Any] = {
        "pack_version": "cubeengine.srtp/evidence-pack/1.0",
        "source_package_hash": package_hash,
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        "title": package.title,
        "entrypoint": str(package.entrypoint),
        "runtime": package.runtime.to_mapping(),
        "license": {
            "name": package.license_name,
            "path": package.license_path,
            "upstream_url": package.upstream_url,
        },
        "inventory": inventory,
        "coverage": coverage,
        "source_parameters": parameters[:40],
        "transformation_gaps": gaps[:40],
        "project_diagnostics": [item if isinstance(item, Mapping) else item for item in diagnostics[:40]],
        "static_analysis_summary": _summarize_static(static),
        "acceptance_gate": handoff.get("acceptance_gate"),
        "evidence_policy": {
            "source_text_is_untrusted_data": True,
            "do_not_invent_citations": True,
            "unknown_must_be_unresolved": True,
        },
    }
    return _truncate_pack(pack, max_chars=max_chars)


def _summarize_static(static: Mapping[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for key in (
        "schema_version", "game_id", "dimensions", "unresolved", "diagnostics",
        "downstream_game_api", "functions", "classes", "requires_llm",
    ):
        if key in static:
            value = static[key]
            if key in ("functions", "classes") and isinstance(value, list):
                summary[key] = value[:30]
            elif key == "diagnostics" and isinstance(value, list):
                summary[key] = value[:30]
            elif key == "unresolved" and isinstance(value, list):
                summary[key] = value[:40]
            else:
                summary[key] = deepcopy(value)
    return summary


def _truncate_pack(pack: Dict[str, Any], *, max_chars: int) -> Dict[str, Any]:
    encoded = json.dumps(pack, ensure_ascii=False)
    if len(encoded) <= max_chars:
        pack["truncated"] = False
        return pack
    # Drop largest optional arrays first.
    for key in ("project_diagnostics", "transformation_gaps", "source_parameters"):
        if key in pack and isinstance(pack[key], list):
            pack[key] = pack[key][:10]
        encoded = json.dumps(pack, ensure_ascii=False)
        if len(encoded) <= max_chars:
            pack["truncated"] = True
            return pack
    static = pack.get("static_analysis_summary")
    if isinstance(static, dict):
        for key in ("diagnostics", "functions", "classes", "unresolved"):
            if isinstance(static.get(key), list):
                static[key] = static[key][:5]
    inventory = pack.get("inventory")
    if isinstance(inventory, dict):
        inventory["files"] = list(inventory.get("files") or [])[:20]
        inventory["assets"] = list(inventory.get("assets") or [])[:20]
    pack["truncated"] = True
    return pack
