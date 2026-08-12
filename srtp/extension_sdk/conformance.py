"""Executable Extension package conformance and determinism probes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

from .host import ExtensionHost, ExtensionHostError
from .manifest import (
    EXTENSION_HOST_CAPABILITY_ID,
    ExtensionDiagnostic,
    canonical_extension_manifest_hash,
    load_extension_manifest,
    validate_extension_manifest,
    verify_extension_package,
)


@dataclass(frozen=True)
class ExtensionConformanceReport:
    extension_id: str
    version: str
    content_hash: str
    host_capability: str
    structurally_valid: bool
    sealed: bool
    package_verified: bool
    lifecycle_passed: bool
    determinism_passed: bool
    compile_ready: bool
    diagnostics: Tuple[ExtensionDiagnostic, ...]

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "extension_id": self.extension_id,
            "version": self.version,
            "content_hash": self.content_hash,
            "host_capability": self.host_capability,
            "structurally_valid": self.structurally_valid,
            "sealed": self.sealed,
            "package_verified": self.package_verified,
            "lifecycle_passed": self.lifecycle_passed,
            "determinism_passed": self.determinism_passed,
            "compile_ready": self.compile_ready,
            "diagnostics": [item.to_mapping() for item in self.diagnostics],
        }


def assess_extension_conformance(
    root: Path, *,
    cases: Mapping[str, Sequence[Any]],
    approved_hashes: Sequence[str] = (),
) -> ExtensionConformanceReport:
    root = Path(root)
    try:
        manifest = load_extension_manifest(root / "extension.json")
    except ValueError as exc:
        diagnostic = ExtensionDiagnostic("error", "manifest.load", "/", str(exc))
        return ExtensionConformanceReport(
            "", "", "", EXTENSION_HOST_CAPABILITY_ID,
            False, False, False, False, False, False, (diagnostic,),
        )
    diagnostics = list(validate_extension_manifest(manifest))
    structural = not any(item.severity == "error" for item in diagnostics)
    sealed = False
    try:
        sealed = bool(
            manifest.get("content_hash")
            and manifest.get("content_hash") == canonical_extension_manifest_hash(manifest)
        )
    except (TypeError, ValueError):
        pass
    if manifest.get("content_hash") and not sealed:
        diagnostics.append(ExtensionDiagnostic(
            "error", "hash.mismatch", "/content_hash",
            "Extension manifest content hash is invalid.",
        ))
    package = None
    if structural and sealed:
        try:
            package = verify_extension_package(root)
        except ValueError as exc:
            diagnostics.append(ExtensionDiagnostic("error", "package.invalid", "/files", str(exc)))
    lifecycle = False
    determinism = False
    if package is not None:
        declared = {item["id"]: item for item in package.manifest["capabilities"]}
        unknown = set(cases) - set(declared)
        if unknown:
            diagnostics.append(ExtensionDiagnostic(
                "error", "case.capability", "/capabilities",
                "Conformance case targets unknown capability: {0}".format(sorted(unknown)[0]),
            ))
        missing = {
            identifier for identifier, item in declared.items()
            if item["deterministic"] is True and not cases.get(identifier)
        }
        if missing:
            diagnostics.append(ExtensionDiagnostic(
                "error", "case.required", "/capabilities",
                "Every deterministic capability requires at least one conformance case.",
            ))
        if not unknown and not missing:
            try:
                first = _run_cases(package, cases, approved_hashes)
                second = _run_cases(package, cases, approved_hashes)
                lifecycle = True
                deterministic_ids = {
                    identifier for identifier, item in declared.items()
                    if item["deterministic"] is True
                }
                determinism = all(
                    first[identifier] == second[identifier]
                    for identifier in deterministic_ids
                )
                if not determinism:
                    diagnostics.append(ExtensionDiagnostic(
                        "error", "determinism.diverged", "/capabilities",
                        "Fresh-process deterministic probe produced different results.",
                    ))
            except ExtensionHostError as exc:
                diagnostics.append(ExtensionDiagnostic(
                    "error", "host." + exc.code, "/runtime", str(exc),
                ))
    ready = bool(
        package is not None and lifecycle and determinism
        and not any(item.severity == "error" for item in diagnostics)
    )
    return ExtensionConformanceReport(
        extension_id=str(manifest.get("extension_id", "")),
        version=str(manifest.get("version", "")),
        content_hash=str(manifest.get("content_hash", "")),
        host_capability=EXTENSION_HOST_CAPABILITY_ID,
        structurally_valid=structural,
        sealed=sealed,
        package_verified=package is not None,
        lifecycle_passed=lifecycle,
        determinism_passed=determinism,
        compile_ready=ready,
        diagnostics=tuple(diagnostics),
    )


def verify_extension_replay(
    package, *,
    invocations: Sequence[Tuple[str, Any]],
    expected_records: Sequence[Mapping[str, Any]],
    approved_hashes: Sequence[str] = (),
) -> Tuple[Mapping[str, Any], ...]:
    if len(invocations) != len(expected_records):
        raise ExtensionHostError("replay_length", "extension replay input and audit lengths differ")
    host = ExtensionHost(package, approved_hashes=approved_hashes).start()
    try:
        host.initialize({"replay": True})
        for capability_id, request in invocations:
            declaration = host._capabilities.get(capability_id)
            if declaration is None or declaration["replay_safe"] is not True:
                raise ExtensionHostError(
                    "replay_unsupported",
                    "capability does not declare replay safety: {0}".format(capability_id),
                )
            host.invoke(capability_id, request)
        actual = tuple(item.to_mapping() for item in host.invocation_records)
    finally:
        host.close()
    normalized = tuple(dict(item) for item in expected_records)
    if actual != normalized:
        raise ExtensionHostError("replay_divergence", "extension replay audit diverged")
    return actual


def _run_cases(package, cases, approved_hashes):
    results = {identifier: [] for identifier in cases}
    host = ExtensionHost(package, approved_hashes=approved_hashes).start()
    try:
        host.initialize({"conformance": True})
        for identifier in sorted(cases):
            for request in cases[identifier]:
                results[identifier].append(host.invoke(identifier, request))
        snapshot = host.snapshot()
        host.restore(snapshot)
    finally:
        host.close()
    return results
