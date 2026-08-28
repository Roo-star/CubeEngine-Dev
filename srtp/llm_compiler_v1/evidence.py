"""Bounded evidence packs derived from Function 1 LLM handoff."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from srtp.source_game import SourceGamePackage

PROMPT_TEMPLATE_VERSION = "cubeengine.srtp/llm-prompt/2.0"
EVIDENCE_PACK_VERSION = "cubeengine.srtp/evidence-pack/2.0"
DEFAULT_MAX_CHARS = 14000
_SNIPPET_MAX = 160
_TOPIC_ALIASES = {
    "direction": "direction_input",
    "direction_input": "direction_input",
    "input": "direction_input",
    "control": "direction_input",
    "move": "movement",
    "movement": "movement",
    "advance": "movement",
    "tick": "movement",
    "food": "food_spawn",
    "food_spawn": "food_spawn",
    "fruit": "food_spawn",
    "random": "food_spawn",
    "collision": "collision",
    "fail": "collision",
    "wall": "collision",
    "death": "death",
    "game_over": "death",
    "outcome": "death",
    "asset": "asset_load",
    "assets": "asset_load",
    "asset_load": "asset_load",
    "render": "asset_load",
    "graphics": "asset_load",
}


def source_package_hash(handoff: Mapping[str, Any]) -> str:
    payload = json.dumps(
        handoff, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_evidence_pack(
    package: SourceGamePackage,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Dict[str, Any]:
    """Trim a whole-project handoff into a bounded, citation-friendly pack.

    Full source file bodies are never placed in the system channel. Paths,
    parameters, diagnostics, a trimmed Function-1 ``partial_schema``, and
    verifiable source spans (Evidence ID / hash / line span / IR pointer) are
    included instead.
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

    root = Path(package.root)
    partial_schema = static.get("partial_schema") if isinstance(static.get("partial_schema"), Mapping) else {}
    if not partial_schema and isinstance(static, Mapping):
        # Some handoffs nest schema fields at the static_analysis root.
        if static.get("schema_version"):
            partial_schema = static
    evidence_items = collect_evidence_items(
        package_root=root,
        partial_schema=partial_schema if isinstance(partial_schema, Mapping) else {},
        provenance=static.get("provenance") if isinstance(static.get("provenance"), Mapping) else {},
        parameters=parameters,
        inventory=inventory,
        static_summary=_summarize_static(static),
    )

    pack: Dict[str, Any] = {
        "pack_version": EVIDENCE_PACK_VERSION,
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
        "partial_schema": _trim_partial_schema(partial_schema if isinstance(partial_schema, Mapping) else {}),
        "evidence": evidence_items,
        "evidence_by_id": {
            str(item["evidence_id"]): item for item in evidence_items
            if isinstance(item, Mapping) and item.get("evidence_id")
        },
        "acceptance_gate": handoff.get("acceptance_gate"),
        "evidence_policy": {
            "source_text_is_untrusted_data": True,
            "do_not_invent_citations": True,
            "unknown_must_be_unresolved": True,
            "citations_must_verify": True,
        },
    }
    return _truncate_pack(pack, max_chars=max_chars)


def collect_evidence_items(
    *,
    package_root: Path,
    partial_schema: Mapping[str, Any],
    provenance: Mapping[str, Any],
    parameters: Sequence[Any],
    inventory: Mapping[str, Any],
    static_summary: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Build bounded, verifiable evidence rows for the LLM + validator."""

    root = Path(package_root)
    items: List[Dict[str, Any]] = []
    seen: set = set()

    def add(
        *,
        evidence_id: str,
        rel_path: str,
        line_start: Optional[int],
        line_end: Optional[int],
        supports: str,
        topic: str,
        kind: str = "static",
        confidence: float = 0.8,
        detail: str = "",
    ) -> None:
        if evidence_id in seen:
            return
        resolved = _resolve_source_file(root, rel_path)
        if resolved is None:
            return
        rel = _relpath(root, resolved)
        digest = file_sha256(resolved)
        span = _normalize_span(line_start, line_end)
        if span is None and line_start is None:
            # Path-only assets still need a byte span covering the whole file start.
            span = {"byte_start": 0, "byte_end": min(64, resolved.stat().st_size)}
        snippet = _read_snippet(resolved, span) if span else ""
        entry = {
            "evidence_id": evidence_id,
            "path": rel.replace("\\", "/"),
            "file_sha256": digest,
            "kind": kind,
            "span": span,
            "supports": supports,
            "topic": topic,
            "confidence": confidence,
            "snippet": snippet,
        }
        if detail:
            entry["detail"] = detail[:200]
        items.append(entry)
        seen.add(evidence_id)

    ui_hints = partial_schema.get("ui_hints") if isinstance(partial_schema.get("ui_hints"), Mapping) else {}
    controls = ui_hints.get("controls") if isinstance(ui_hints.get("controls"), list) else []
    for index, control in enumerate(controls[:24]):
        if not isinstance(control, Mapping):
            continue
        source = control.get("source") if isinstance(control.get("source"), Mapping) else {}
        path = str(source.get("path") or control.get("path") or "")
        line = source.get("line", control.get("line"))
        label = str(control.get("id") or control.get("action") or control.get("key") or index)
        topic = "direction_input"
        name = str(control.get("name") or control.get("label") or label).lower()
        if any(token in name for token in ("quit", "pause", "escape", "space")):
            topic = "direction_input"
        add(
            evidence_id="ev:input.{0}".format(_slug(label)),
            rel_path=path,
            line_start=int(line) if isinstance(line, int) else None,
            line_end=int(line) if isinstance(line, int) else None,
            supports="/input_ir/bindings",
            topic=topic,
            detail=str(control.get("action") or control.get("name") or label),
        )

    contract = ui_hints.get("interaction_contract") if isinstance(ui_hints.get("interaction_contract"), Mapping) else {}
    bindings = contract.get("source_bindings") if isinstance(contract.get("source_bindings"), list) else []
    for index, binding in enumerate(bindings[:16]):
        if not isinstance(binding, Mapping):
            continue
        source = binding.get("source") if isinstance(binding.get("source"), Mapping) else binding
        path = str(source.get("path") or "")
        line = source.get("line")
        add(
            evidence_id="ev:binding.{0}".format(_slug(str(binding.get("id") or index))),
            rel_path=path,
            line_start=int(line) if isinstance(line, int) else None,
            line_end=int(line) if isinstance(line, int) else None,
            supports="/input_ir/bindings",
            topic="direction_input",
        )

    for param in parameters[:40]:
        if not isinstance(param, Mapping):
            continue
        locations = param.get("locations") if isinstance(param.get("locations"), list) else []
        param_id = str(param.get("id") or "param")
        topic = "movement" if any(
            token in param_id.lower() for token in ("tick", "move", "speed", "interval")
        ) else "movement"
        for loc_index, loc in enumerate(locations[:4]):
            if not isinstance(loc, Mapping):
                continue
            line = loc.get("line")
            add(
                evidence_id="ev:param.{0}.{1}".format(_slug(param_id), loc_index),
                rel_path=str(loc.get("path") or ""),
                line_start=int(line) if isinstance(line, int) else None,
                line_end=int(line) if isinstance(line, int) else None,
                supports="/rule_ir/flow",
                topic=topic,
                detail=str(param.get("label") or param_id),
            )

    randomness = partial_schema.get("randomness") if isinstance(partial_schema.get("randomness"), Mapping) else {}
    events = randomness.get("events") if isinstance(randomness.get("events"), list) else []
    for event in events[:8]:
        if not isinstance(event, Mapping):
            continue
        calls = event.get("calls") if isinstance(event.get("calls"), list) else []
        for call_index, call in enumerate(calls[:4]):
            if not isinstance(call, Mapping):
                continue
            line = call.get("line")
            add(
                evidence_id="ev:random.{0}.{1}".format(
                    _slug(str(event.get("id") or "rng")), call_index,
                ),
                rel_path=str(call.get("path") or call.get("file") or ""),
                line_start=int(line) if isinstance(line, int) else None,
                line_end=int(line) if isinstance(line, int) else None,
                supports="/rule_ir/actions",
                topic="food_spawn",
                detail=str(event.get("id") or "randomness"),
            )

    for outcome in (partial_schema.get("outcomes") or [])[:12]:
        if not isinstance(outcome, Mapping):
            continue
        source_ref = outcome.get("source_ref")
        path, line = _parse_source_ref(source_ref)
        topic = "death" if any(
            token in str(outcome.get("id") or outcome.get("name") or "").lower()
            for token in ("collision", "loss", "fail", "death", "over")
        ) else "collision"
        add(
            evidence_id="ev:outcome.{0}".format(_slug(str(outcome.get("id") or "outcome"))),
            rel_path=path or str(partial_schema.get("source", {}).get("path") or ""),
            line_start=line,
            line_end=line,
            supports="/rule_ir/outcomes",
            topic=topic,
            detail=str(outcome.get("name") or outcome.get("id") or ""),
            confidence=0.55 if line in (None, 1) else 0.8,
        )

    for action in (partial_schema.get("actions") or [])[:16]:
        if not isinstance(action, Mapping):
            continue
        path, line = _parse_source_ref(action.get("source_ref"))
        action_id = str(action.get("id") or action.get("name") or "action")
        topic = "movement"
        lower = action_id.lower()
        if any(token in lower for token in ("direction", "control", "turn")):
            topic = "direction_input"
        add(
            evidence_id="ev:action.{0}".format(_slug(action_id)),
            rel_path=path or "",
            line_start=line,
            line_end=line,
            supports="/rule_ir/actions",
            topic=topic,
            detail=str(action.get("verb") or action_id),
            confidence=0.5 if line is None else 0.75,
        )

    for key, rows in (provenance or {}).items():
        if not isinstance(rows, list):
            continue
        topic = _topic_from_key(str(key))
        for index, row in enumerate(rows[:4]):
            if not isinstance(row, Mapping):
                continue
            path = str(row.get("source") or row.get("path") or "")
            line = row.get("line")
            add(
                evidence_id="ev:prov.{0}.{1}".format(_slug(str(key)), index),
                rel_path=path,
                line_start=int(line) if isinstance(line, int) else None,
                line_end=int(line) if isinstance(line, int) else None,
                supports=_supports_from_topic(topic),
                topic=topic,
                detail=str(row.get("detail") or key),
                confidence=float(row.get("confidence") or 0.7),
            )

    functions = static_summary.get("functions") if isinstance(static_summary.get("functions"), list) else []
    for fn in functions[:40]:
        if not isinstance(fn, Mapping):
            continue
        name = str(fn.get("name") or fn.get("qualname") or "")
        lower = name.lower()
        path = str(fn.get("path") or fn.get("file") or "")
        line = fn.get("lineno") if isinstance(fn.get("lineno"), int) else fn.get("line")
        end = fn.get("end_lineno") if isinstance(fn.get("end_lineno"), int) else line
        topic = None
        supports = "/rule_ir/actions"
        if any(token in lower for token in ("move", "advance", "update")):
            topic = "movement"
        elif any(token in lower for token in ("check_fail", "collide", "collision", "wall")):
            topic = "collision"
            supports = "/rule_ir/outcomes"
        elif any(token in lower for token in ("game_over", "die", "death")):
            topic = "death"
            supports = "/rule_ir/outcomes"
        elif any(token in lower for token in ("randomize", "spawn", "fruit", "food")):
            topic = "food_spawn"
        elif any(token in lower for token in ("load", "image", "blit", "draw")):
            topic = "asset_load"
            supports = "/asset_ir/assets"
        if topic is None:
            continue
        add(
            evidence_id="ev:fn.{0}".format(_slug(name)),
            rel_path=path,
            line_start=int(line) if isinstance(line, int) else None,
            line_end=int(end) if isinstance(end, int) else None,
            supports=supports,
            topic=topic,
            detail=name,
        )

    asset_files = ui_hints.get("asset_files") if isinstance(ui_hints.get("asset_files"), list) else []
    if not asset_files:
        asset_files = list(inventory.get("assets") or [])[:24]
    for index, asset in enumerate(asset_files[:24]):
        if isinstance(asset, Mapping):
            path = str(asset.get("path") or asset.get("file") or "")
        else:
            path = str(asset)
        if not path:
            continue
        add(
            evidence_id="ev:asset.{0}".format(_slug(path)),
            rel_path=path,
            line_start=None,
            line_end=None,
            supports="/asset_ir/assets",
            topic="asset_load",
            detail=path,
            confidence=0.7,
        )

    presentation = ui_hints.get("presentation_mapping") if isinstance(ui_hints.get("presentation_mapping"), Mapping) else {}
    mapped = presentation.get("mapped_roles") if isinstance(presentation.get("mapped_roles"), Mapping) else {}
    for role, path in list(mapped.items())[:16]:
        add(
            evidence_id="ev:role.{0}".format(_slug(str(role))),
            rel_path=str(path),
            line_start=None,
            line_end=None,
            supports="/asset_ir/assets",
            topic="asset_load",
            detail="role:{0}".format(role),
        )

    return items[:60]


def validate_evidence_citations(
    proposal: Mapping[str, Any],
    evidence_pack: Mapping[str, Any],
    *,
    source_root: Optional[Path] = None,
) -> List[str]:
    """Verify patch evidence IDs / paths / hashes / spans against the pack and disk.

    Fake or unverifiable citations become hard failures (callers may also promote
    them to required unresolved). Empty evidence arrays are rejected by the
    proposal contract; this layer rejects invented citations.
    """

    errors: List[str] = []
    catalog = evidence_pack.get("evidence_by_id")
    if not isinstance(catalog, Mapping):
        catalog = {
            str(item.get("evidence_id")): item
            for item in (evidence_pack.get("evidence") or [])
            if isinstance(item, Mapping) and item.get("evidence_id")
        }
    root = Path(source_root) if source_root else None
    patches = proposal.get("patches") if isinstance(proposal.get("patches"), Mapping) else {}
    for ir_key, entries in patches.items() if isinstance(patches, Mapping) else ():
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                continue
            evidence = entry.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(
                    "patches.{0}[{1}].evidence must cite verifiable evidence".format(ir_key, index)
                )
                continue
            for ev_index, item in enumerate(evidence):
                prefix = "patches.{0}[{1}].evidence[{2}]".format(ir_key, index, ev_index)
                errors.extend(
                    _validate_one_citation(item, catalog=catalog, source_root=root, prefix=prefix)
                )
    return errors


def evidence_topics(evidence_pack: Mapping[str, Any]) -> set:
    topics = set()
    for item in evidence_pack.get("evidence") or []:
        if isinstance(item, Mapping) and item.get("topic"):
            topics.add(str(item["topic"]))
    return topics


def snake_evidence_complete(evidence_pack: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    """Completion check for P0-1 Snake: direction / move / food / collision / death / assets."""

    required = {
        "direction_input", "movement", "food_spawn", "collision", "death", "asset_load",
    }
    present = evidence_topics(evidence_pack)
    # Collision and death may share the same outcome cite; accept either when both missing
    # only if at least one collision-like topic exists.
    missing = sorted(required - present)
    if "collision" in missing and "death" in present:
        missing = [item for item in missing if item != "collision"]
    if "death" in missing and "collision" in present:
        missing = [item for item in missing if item != "death"]
    return (not missing, missing)


def _validate_one_citation(
    item: Any,
    *,
    catalog: Mapping[str, Any],
    source_root: Optional[Path],
    prefix: str,
) -> List[str]:
    if isinstance(item, str) and item.strip():
        return ["{0} must be an evidence object, not a bare string".format(prefix)]
    if not isinstance(item, Mapping):
        return ["{0} must be an object".format(prefix)]

    evidence_id = str(item.get("evidence_id") or "").strip()
    path = str(item.get("path") or item.get("file") or "").strip()
    if evidence_id in {"ev:llm.inline", "ev:test"} and path in {"llm_proposal", "source", ""}:
        return ["{0} invents a non-source citation".format(prefix)]
    if path in {"llm_proposal"}:
        return ["{0}.path must reference a source file, not llm_proposal".format(prefix)]

    if evidence_id and evidence_id in catalog:
        known = catalog[evidence_id]
        if not isinstance(known, Mapping):
            return ["{0} catalog entry is invalid".format(prefix)]
        # ID match is authoritative when the pack minted it; optional field checks.
        known_path = str(known.get("path") or "").replace("\\", "/")
        cited_path = path.replace("\\", "/") if path else known_path
        if path and known_path and cited_path != known_path:
            return ["{0}.path does not match evidence pack id {1}".format(prefix, evidence_id)]
        cited_hash = item.get("file_sha256") or item.get("sha256")
        if cited_hash and known.get("file_sha256") and cited_hash != known.get("file_sha256"):
            return ["{0}.file_sha256 does not match evidence pack".format(prefix)]
        return []

    if not evidence_id:
        return ["{0}.evidence_id is required".format(prefix)]
    if not path:
        return ["{0}.path is required when evidence_id is not in the pack".format(prefix)]
    if source_root is None:
        return ["{0} cannot verify path without source_root".format(prefix)]

    resolved = _resolve_source_file(source_root, path)
    if resolved is None:
        return ["{0}.path does not exist under the source package".format(prefix)]
    actual_hash = file_sha256(resolved)
    cited_hash = item.get("file_sha256") or item.get("sha256")
    if not cited_hash:
        return ["{0}.file_sha256 is required".format(prefix)]
    if str(cited_hash) != actual_hash:
        return ["{0}.file_sha256 does not match on-disk file".format(prefix)]

    span = item.get("span") if isinstance(item.get("span"), Mapping) else None
    if span is None and isinstance(item.get("line"), int):
        span = {"line_start": int(item["line"]), "line_end": int(item["line"])}
    if span is None:
        return ["{0}.span (line or byte) is required".format(prefix)]
    span_error = _verify_span(resolved, span)
    if span_error:
        return ["{0}.span {1}".format(prefix, span_error)]
    supports = item.get("supports")
    if not isinstance(supports, str) or not supports.startswith("/"):
        return ["{0}.supports must be an IR JSON Pointer".format(prefix)]
    return []


def _verify_span(path: Path, span: Mapping[str, Any]) -> Optional[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if "line_start" in span or "line_end" in span:
        start = span.get("line_start", span.get("line_end"))
        end = span.get("line_end", span.get("line_start"))
        if not isinstance(start, int) or not isinstance(end, int):
            return "line_start/line_end must be integers"
        if start < 1 or end < start or end > max(1, len(lines)):
            return "line range is outside the file"
        return None
    if "byte_start" in span or "byte_end" in span:
        start = span.get("byte_start", 0)
        end = span.get("byte_end", start)
        data = path.read_bytes()
        if not isinstance(start, int) or not isinstance(end, int):
            return "byte_start/byte_end must be integers"
        if start < 0 or end < start or end > len(data):
            return "byte range is outside the file"
        return None
    return "must include line_start/line_end or byte_start/byte_end"


def _trim_partial_schema(schema: Mapping[str, Any]) -> Dict[str, Any]:
    if not schema:
        return {}
    keep_roots = (
        "schema_version", "game", "space", "participants", "entities", "state",
        "setup", "flow", "actions", "randomness", "goals", "outcomes", "modes",
        "ui_hints",
    )
    trimmed: Dict[str, Any] = {}
    for key in keep_roots:
        if key not in schema:
            continue
        value = schema[key]
        if key in ("actions", "outcomes", "entities", "participants", "goals", "modes") and isinstance(value, list):
            trimmed[key] = deepcopy(value[:20])
        elif key == "ui_hints" and isinstance(value, Mapping):
            hints = {}
            for hint_key in ("controls", "asset_files", "interaction_contract", "presentation_mapping"):
                if hint_key not in value:
                    continue
                hint_value = value[hint_key]
                if isinstance(hint_value, list):
                    hints[hint_key] = deepcopy(hint_value[:24])
                else:
                    hints[hint_key] = deepcopy(hint_value)
            trimmed[key] = hints
        elif key == "state" and isinstance(value, Mapping):
            state = deepcopy(dict(value))
            if isinstance(state.get("variables"), list):
                state["variables"] = state["variables"][:30]
            trimmed[key] = state
        else:
            trimmed[key] = deepcopy(value)
    return trimmed


def _prefer_topic_coverage(
    items: Sequence[Mapping[str, Any]], *, limit: int,
) -> List[Dict[str, Any]]:
    """Keep at least one cite per topic when truncating the evidence list."""

    if len(items) <= limit:
        return [dict(item) for item in items if isinstance(item, Mapping)]
    priority = (
        "direction_input", "movement", "food_spawn", "collision", "death", "asset_load",
    )
    selected: List[Dict[str, Any]] = []
    seen_ids: set = set()
    by_topic: Dict[str, List[Mapping[str, Any]]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        topic = str(item.get("topic") or "other")
        by_topic.setdefault(topic, []).append(item)

    def take(item: Mapping[str, Any]) -> None:
        eid = str(item.get("evidence_id") or "")
        if eid and eid in seen_ids:
            return
        if len(selected) >= limit:
            return
        selected.append(dict(item))
        if eid:
            seen_ids.add(eid)

    for topic in priority:
        rows = by_topic.get(topic) or []
        if rows:
            take(rows[0])
    for item in items:
        if len(selected) >= limit:
            break
        if isinstance(item, Mapping):
            take(item)
    return selected


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
    # Prefer nested partial_schema extensions when present.
    partial = static.get("partial_schema") if isinstance(static.get("partial_schema"), Mapping) else {}
    extensions = partial.get("extensions") if isinstance(partial.get("extensions"), Mapping) else {}
    python = extensions.get("python") if isinstance(extensions.get("python"), Mapping) else {}
    if "functions" not in summary and isinstance(python.get("functions"), list):
        summary["functions"] = python["functions"][:30]
    if "classes" not in summary and isinstance(python.get("classes"), list):
        summary["classes"] = python["classes"][:30]
    return summary


def _truncate_pack(pack: Dict[str, Any], *, max_chars: int) -> Dict[str, Any]:
    encoded = json.dumps(pack, ensure_ascii=False)
    if len(encoded) <= max_chars:
        pack["truncated"] = False
        return pack
    # Drop snippets before cutting evidence rows so topic coverage survives.
    for item in pack.get("evidence") or []:
        if isinstance(item, dict):
            item.pop("snippet", None)
    encoded = json.dumps(pack, ensure_ascii=False)
    if len(encoded) <= max_chars:
        pack["truncated"] = True
        return pack
    for key in ("project_diagnostics", "transformation_gaps", "source_parameters"):
        if key in pack and isinstance(pack[key], list):
            pack[key] = pack[key][:8]
        encoded = json.dumps(pack, ensure_ascii=False)
        if len(encoded) <= max_chars:
            pack["truncated"] = True
            return pack
    if isinstance(pack.get("evidence"), list) and len(pack["evidence"]) > 24:
        pack["evidence"] = _prefer_topic_coverage(pack["evidence"], limit=24)
        pack["evidence_by_id"] = {
            str(item["evidence_id"]): item for item in pack["evidence"]
            if isinstance(item, Mapping) and item.get("evidence_id")
        }
        encoded = json.dumps(pack, ensure_ascii=False)
        if len(encoded) <= max_chars:
            pack["truncated"] = True
            return pack
    if isinstance(pack.get("evidence"), list) and len(pack["evidence"]) > 12:
        pack["evidence"] = _prefer_topic_coverage(pack["evidence"], limit=12)
        pack["evidence_by_id"] = {
            str(item["evidence_id"]): item for item in pack["evidence"]
            if isinstance(item, Mapping) and item.get("evidence_id")
        }
    partial = pack.get("partial_schema")
    if isinstance(partial, dict):
        for key in ("actions", "outcomes", "entities", "participants"):
            if isinstance(partial.get(key), list):
                partial[key] = partial[key][:8]
        hints = partial.get("ui_hints")
        if isinstance(hints, dict) and isinstance(hints.get("controls"), list):
            hints["controls"] = hints["controls"][:8]
    static = pack.get("static_analysis_summary")
    if isinstance(static, dict):
        for key in ("diagnostics", "functions", "classes", "unresolved"):
            if isinstance(static.get(key), list):
                static[key] = static[key][:5]
    inventory = pack.get("inventory")
    if isinstance(inventory, dict):
        inventory["files"] = list(inventory.get("files") or [])[:12]
        inventory["assets"] = list(inventory.get("assets") or [])[:12]
    pack["truncated"] = True
    return pack


def _resolve_source_file(root: Path, rel_path: str) -> Optional[Path]:
    if not rel_path or not str(rel_path).strip():
        return None
    candidate = Path(rel_path)
    if candidate.is_file():
        try:
            candidate.relative_to(root.resolve())
            return candidate.resolve()
        except ValueError:
            # Absolute path outside package is rejected for citations.
            if root.resolve() in candidate.resolve().parents or candidate.resolve() == root.resolve():
                return candidate.resolve()
            return None
    joined = (root / rel_path).resolve()
    if joined.is_file():
        return joined
    # Basename search within package (bounded).
    name = Path(rel_path).name
    if not name:
        return None
    matches = list(root.rglob(name))[:8]
    files = [item for item in matches if item.is_file()]
    return files[0].resolve() if len(files) == 1 else (files[0].resolve() if files else None)


def _relpath(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return path.name


def _normalize_span(
    line_start: Optional[int], line_end: Optional[int],
) -> Optional[Dict[str, int]]:
    if isinstance(line_start, int) and line_start > 0:
        end = line_end if isinstance(line_end, int) and line_end >= line_start else line_start
        return {"line_start": int(line_start), "line_end": int(end)}
    return None


def _read_snippet(path: Path, span: Mapping[str, Any]) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if "line_start" in span:
        lines = text.splitlines()
        start = max(1, int(span["line_start"])) - 1
        end = min(len(lines), int(span.get("line_end", span["line_start"])))
        chunk = "\n".join(lines[start:end])
        return chunk[:_SNIPPET_MAX]
    if "byte_start" in span:
        data = path.read_bytes()
        start = int(span.get("byte_start") or 0)
        end = int(span.get("byte_end") or start)
        return data[start:end].decode("utf-8", errors="replace")[:_SNIPPET_MAX]
    return ""


def _parse_source_ref(value: Any) -> Tuple[Optional[str], Optional[int]]:
    if isinstance(value, Mapping):
        path = value.get("path") or value.get("file") or value.get("class")
        line = value.get("line")
        return (
            str(path) if path else None,
            int(line) if isinstance(line, int) else None,
        )
    if not isinstance(value, str) or not value.strip():
        return None, None
    match = re.match(r"^(?P<path>.+?):(?P<line>\d+)$", value.strip())
    if match:
        return match.group("path"), int(match.group("line"))
    if "/" in value or "\\" in value or value.endswith(".py"):
        return value, None
    return None, None


def _topic_from_key(key: str) -> str:
    lower = key.lower()
    for token, topic in _TOPIC_ALIASES.items():
        if token in lower:
            return topic
    if "flow" in lower or "tick" in lower:
        return "movement"
    if "space" in lower or "dimension" in lower:
        return "movement"
    return "movement"


def _supports_from_topic(topic: str) -> str:
    return {
        "direction_input": "/input_ir/bindings",
        "movement": "/rule_ir/flow",
        "food_spawn": "/rule_ir/actions",
        "collision": "/rule_ir/outcomes",
        "death": "/rule_ir/outcomes",
        "asset_load": "/asset_ir/assets",
    }.get(topic, "/rule_ir")


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return (text[:48] or "item")
