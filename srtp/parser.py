"""Public SRTP Function 1 file parsing pipeline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .extractors import extract_json_source, extract_python_source
from .report import Diagnostic, ParseReport, SourceEvidence, deduplicate_diagnostics
from .schema import classify_schema, new_rule_schema, normalize_rule_schema, set_path, validate_rule_schema


MAX_SOURCE_BYTES = 2 * 1024 * 1024


class RuleFileParser:
    """Parse supported source files into one canonical Rule Schema."""

    def __init__(self, max_source_bytes: int = MAX_SOURCE_BYTES) -> None:
        self.max_source_bytes = int(max_source_bytes)

    def parse(self, path: Path, overrides: Optional[Mapping[str, Any]] = None) -> ParseReport:
        source_path = Path(path)
        try:
            data = source_path.read_bytes()
        except OSError as error:
            report = ParseReport(new_rule_schema(source_path.stem), source_path=str(source_path), source_format="unknown")
            report.add_diagnostic(Diagnostic("error", "source.read", "$", "Could not read the source file.", evidence=str(error)))
            return self._finalize(report, b"", overrides)
        if len(data) > self.max_source_bytes:
            report = ParseReport(new_rule_schema(source_path.stem), source_path=str(source_path), source_format="unknown")
            report.add_diagnostic(Diagnostic(
                "error", "source.size_limit", "$", "Source file exceeds the Function 1 safety limit.",
                evidence="{0} bytes > {1}".format(len(data), self.max_source_bytes),
                suggestion="Provide an isolated rule file or use Function 2.", requires_llm=True,
            ))
            return self._finalize(report, data, overrides)
        if b"\x00" in data:
            report = ParseReport(new_rule_schema(source_path.stem), source_path=str(source_path), source_format="unknown")
            report.add_diagnostic(Diagnostic("error", "source.binary", "$", "Binary/NUL-containing files are not accepted as rule scripts.", requires_llm=True))
            return self._finalize(report, data, overrides)
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            report = ParseReport(new_rule_schema(source_path.stem), source_path=str(source_path), source_format="unknown")
            report.add_diagnostic(Diagnostic("error", "source.encoding", "$", "Rule files must use UTF-8 text encoding.", evidence=str(error)))
            return self._finalize(report, data, overrides)

        suffix = source_path.suffix.lower()
        if suffix == ".json":
            try:
                value = json.loads(text)
            except json.JSONDecodeError as error:
                report = ParseReport(new_rule_schema(source_path.stem), source_path=str(source_path), source_format="json")
                report.add_diagnostic(Diagnostic(
                    "error", "json.syntax", "$", "JSON syntax is invalid.",
                    evidence="line {0}, column {1}: {2}".format(error.lineno, error.colno, error.msg),
                ))
            else:
                report = extract_json_source(value, str(source_path)) if isinstance(value, Mapping) else ParseReport(
                    new_rule_schema(source_path.stem),
                    diagnostics=[Diagnostic("error", "json.root_type", "$", "JSON root must be an object.")],
                    source_path=str(source_path), source_format="json",
                )
        elif suffix in (".py", ".pyw"):
            report = extract_python_source(text, str(source_path))
        else:
            report = ParseReport(new_rule_schema(source_path.stem), source_path=str(source_path), source_format=suffix.lstrip(".") or "unknown")
            report.add_diagnostic(Diagnostic(
                "error", "source.unsupported_format", "$",
                "Function 1 currently accepts canonical/generic JSON and statically analysed Python scripts.",
                evidence="extension={0}".format(suffix or "none"),
                suggestion="Add a deterministic extractor or pass the content to Function 2.", requires_llm=True,
            ))
        return self._finalize(report, data, overrides)

    def apply_overrides(self, report: ParseReport, overrides: Mapping[str, Any]) -> ParseReport:
        if all(
            overrides.get("space.dimensions.{0}".format(axis), report.schema.get("space", {}).get("dimensions", {}).get(axis))
            is not None
            for axis in ("x", "y", "z")
        ):
            report.diagnostics = [item for item in report.diagnostics if item.code != "python.grid_unresolved"]
        return self._finalize(report, b"", overrides, preserve_hash=True)

    def _finalize(
        self,
        report: ParseReport,
        source_bytes: bytes,
        overrides: Optional[Mapping[str, Any]],
        preserve_hash: bool = False,
    ) -> ParseReport:
        report.schema = normalize_rule_schema(report.schema)
        if overrides:
            for path, value in overrides.items():
                set_path(report.schema, str(path), value)
                report.add_evidence(
                    str(path),
                    SourceEvidence("designer_override", "SRTP Workbench", 1.0, "Explicit designer correction"),
                )
        classify_schema(report.schema)
        source = report.schema.get("source")
        if not isinstance(source, dict):
            source = {}
            report.schema["source"] = source
        source["path"] = report.source_path
        source["format"] = report.source_format
        if source_bytes:
            source["sha256"] = hashlib.sha256(source_bytes).hexdigest()
        elif not preserve_hash:
            source.setdefault("sha256", "")
        source["provenance"] = {
            path: [item.to_mapping() for item in evidence]
            for path, evidence in report.provenance.items()
        }

        extraction_diagnostics = [
            item for item in report.diagnostics
            if not item.code.startswith((
                "schema.", "field.", "space.dimension_", "space.anchor_", "flow.unresolved",
                "actions.unresolved", "action.semantic_", "outcome.semantic_", "randomness.distribution_",
            ))
        ]
        report.diagnostics = deduplicate_diagnostics(extraction_diagnostics + validate_rule_schema(report.schema))
        if any(item.severity == "error" for item in report.diagnostics):
            report.readiness = "blocked"
        elif any(item.requires_llm or item.severity == "warning" for item in report.diagnostics):
            report.readiness = "partial"
        else:
            report.readiness = "complete"
        extensions = report.schema.get("extensions")
        if not isinstance(extensions, dict):
            extensions = {}
            report.schema["extensions"] = extensions
        extensions["function1_status"] = {
            "readiness": report.readiness,
            "previewable": report.previewable,
            "needs_llm": report.needs_llm,
        }
        return report


def parse_rule_file(path: Path, overrides: Optional[Mapping[str, Any]] = None) -> ParseReport:
    return RuleFileParser().parse(path, overrides=overrides)
