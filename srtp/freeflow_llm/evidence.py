"""Bounded evidence index built from Function 1 handoff. Source text is data."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from srtp.source_game import SourceGamePackage

from .contracts import EVIDENCE_KIND_PREFIX


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(payload)


def source_package_hash(package: SourceGamePackage) -> str:
    files = []
    for relative in package.files:
        path = package.root / relative
        digest = sha256_bytes(path.read_bytes()) if path.is_file() else ""
        files.append({"path": relative.replace("\\", "/"), "sha256": digest})
    return sha256_json({
        "entrypoint": _relative(package.entrypoint, package.root),
        "files": files,
        "title": package.title,
    })


@dataclass
class EvidenceIndex:
    source_package_hash: str
    items: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ids(self) -> List[str]:
        return [str(item["evidence_id"]) for item in self.items]

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "source_package_hash": self.source_package_hash,
            "items": list(self.items),
        }

    def citation(self, evidence_id: str) -> Dict[str, Any]:
        for item in self.items:
            if item.get("evidence_id") == evidence_id:
                return {
                    "evidence_id": item["evidence_id"],
                    "source_file_hash": item.get("source_file_hash"),
                    "path": item.get("path"),
                    "span": item.get("span"),
                    "kind": item.get("kind"),
                }
        raise KeyError(evidence_id)


def build_evidence_index(package: SourceGamePackage) -> EvidenceIndex:
    package_hash = source_package_hash(package)
    index = EvidenceIndex(source_package_hash=package_hash)
    handoff = package.llm_handoff()
    entry_rel = _relative(package.entrypoint, package.root)
    entry_bytes = package.entrypoint.read_bytes() if package.entrypoint.is_file() else b""
    entry_hash = sha256_bytes(entry_bytes) if entry_bytes else sha256_bytes(b"")
    index.items.append(_item(
        package_hash, "entrypoint", entry_rel, entry_hash, "source_file",
        supports="/source_project/entrypoint",
        span=_span(1, max(entry_bytes.count(b"\n") + 1, 1)),
        detail="Immutable source entry point. Quoted as data, not instructions.",
    ))
    for relative in package.files:
        path = package.root / relative
        if not path.is_file():
            continue
        data = path.read_bytes()
        index.items.append(_item(
            package_hash, "file:{0}".format(relative.replace("\\", "/")),
            relative.replace("\\", "/"), sha256_bytes(data), "source_file",
            supports="/source_project/inventory/files",
        ))
    provenance = handoff.get("static_analysis", {}).get("provenance", {})
    if isinstance(provenance, Mapping):
        for pointer, records in provenance.items():
            if not isinstance(records, list):
                continue
            for offset, record in enumerate(records):
                if not isinstance(record, Mapping):
                    continue
                line = record.get("line")
                index.items.append(_item(
                    package_hash, "provenance:{0}:{1}".format(pointer, offset),
                    str(record.get("source") or entry_rel), entry_hash,
                    str(record.get("method") or "static"),
                    supports=str(pointer),
                    span=_span(line, line) if isinstance(line, int) else None,
                    detail=str(record.get("detail") or ""),
                    confidence=record.get("confidence"),
                ))
    for diagnostic in list(handoff.get("static_analysis", {}).get("unresolved", [])) + list(
        handoff.get("project_diagnostics", [])
    ):
        if not isinstance(diagnostic, Mapping):
            continue
        index.items.append(_item(
            package_hash, "diagnostic:{0}".format(diagnostic.get("code") or diagnostic.get("path")),
            entry_rel, entry_hash, "diagnostic",
            supports=str(diagnostic.get("path") or "/unresolved"),
            detail=str(diagnostic.get("message") or diagnostic.get("reason") or ""),
        ))
    schema = package.rule_report.schema if package.rule_report is not None else {}
    index.items.append(_item(
        package_hash, "partial_schema", entry_rel, entry_hash, "partial_rule_schema",
        supports="/static_analysis/partial_schema",
        detail="Function 1 partial Rule Schema v1. Unknown fields remain unresolved.",
    ))
    dimensions = schema.get("space", {}).get("dimensions") if isinstance(schema.get("space"), Mapping) else None
    if isinstance(dimensions, Mapping):
        index.items.append(_item(
            package_hash, "dimensions", entry_rel, entry_hash, "constant",
            supports="/topologies/0/axes",
            detail=json.dumps(dimensions, sort_keys=True),
        ))
    seen = set()
    unique = []
    for item in index.items:
        if item["evidence_id"] in seen:
            continue
        seen.add(item["evidence_id"])
        unique.append(item)
    index.items = unique
    return index


def _item(
    package_hash: str, slug: str, path: str, file_hash: str, kind: str, *,
    supports: str, span: Optional[Mapping[str, int]] = None,
    detail: str = "", confidence: Any = None,
) -> Dict[str, Any]:
    digest = sha256_json({"package": package_hash, "slug": slug, "path": path, "hash": file_hash})
    item: Dict[str, Any] = {
        "evidence_id": "{0}sha256:{1}".format(EVIDENCE_KIND_PREFIX, digest),
        "source_file_hash": file_hash,
        "path": path,
        "kind": kind,
        "supports": supports,
        "detail": detail,
        "untrusted_source_text": True,
    }
    if span is not None:
        item["span"] = dict(span)
    if confidence is not None:
        item["confidence"] = confidence
    return item


def _span(start: Any, end: Any) -> Dict[str, int]:
    start_line = int(start) if isinstance(start, int) and start > 0 else 1
    end_line = int(end) if isinstance(end, int) and end > 0 else start_line
    return {"start_line": start_line, "start_column": 1, "end_line": end_line, "end_column": 1}


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name
