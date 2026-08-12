"""Versioned Extension Manifest contract and package byte verification."""

from __future__ import annotations

import hashlib
import ast
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .contracts import ContractSchemaError, validate_contract_schema


EXTENSION_MANIFEST_VERSION = "cubeengine.extension-manifest/1.0"
EXTENSION_SDK_VERSION = "cubeengine.extension-sdk/1.0"
EXTENSION_RPC_VERSION = "cubeengine.extension-rpc/1.0"
EXTENSION_MANIFEST_SCHEMA_PATH = Path(__file__).with_name("extension-manifest-v1.schema.json")
EXTENSION_SDK_CAPABILITY_PATH = Path(__file__).with_name("extension-sdk-capabilities.json")
EXTENSION_SDK_CAPABILITIES = json.loads(
    EXTENSION_SDK_CAPABILITY_PATH.read_text(encoding="utf-8")
)
EXTENSION_HOST_CAPABILITY_ID = str(EXTENSION_SDK_CAPABILITIES["host_capability"])

_EXTENSION_ID = re.compile(r"^extension:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_CAPABILITY_ID = re.compile(
    r"^extension:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*(?:/[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*)+/[1-9][0-9]*(?:\.[0-9]+){0,2}$"
)
_SEMVER = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LOCAL = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_FACTORY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_WINDOWS_FORBIDDEN = set('<>:"|?*')
_KINDS = set(EXTENSION_SDK_CAPABILITIES["capability_kinds"])
_ROOT_FIELDS = {
    "manifest_version", "sdk_version", "extension_id", "version", "content_hash",
    "metadata", "entrypoint", "files", "capabilities", "permissions", "runtime",
    "dependencies", "provenance", "unresolved",
}


@dataclass(frozen=True)
class ExtensionDiagnostic:
    severity: str
    code: str
    path: str
    message: str

    def to_mapping(self) -> Dict[str, str]:
        return {
            "severity": self.severity, "code": self.code,
            "path": self.path, "message": self.message,
        }


@dataclass(frozen=True)
class VerifiedExtensionPackage:
    root: Path
    manifest_path: Path
    manifest: Mapping[str, Any]
    extension_id: str
    version: str
    content_hash: str
    capability_ids: Tuple[str, ...]


def new_extension_manifest(extension_id: str, version: str, name: str = "") -> Dict[str, Any]:
    return {
        "manifest_version": EXTENSION_MANIFEST_VERSION,
        "sdk_version": EXTENSION_SDK_VERSION,
        "extension_id": extension_id,
        "version": version,
        "content_hash": "",
        "metadata": {
            "name": name or extension_id,
            "description": "",
            "publisher": "unverified",
            "trust": "untrusted_generated",
        },
        "entrypoint": {"file": "adapter.py", "factory": "create_extension"},
        "files": [],
        "capabilities": [],
        "permissions": {
            "filesystem_read": ["package://"],
            "filesystem_write": [],
            "network": False,
            "subprocess": False,
            "native_code": False,
            "environment": [],
            "python_modules": [],
        },
        "runtime": {
            "protocol_version": EXTENSION_RPC_VERSION,
            "python_min": "3.9",
            "timeout_ms": 1000,
            "max_request_bytes": 65536,
            "max_response_bytes": 65536,
            "max_memory_mb": 128,
            "max_cpu_ms": 1000,
            "max_processes": 1,
            "isolation": "os_sandbox_required",
        },
        "dependencies": [],
        "provenance": {},
        "unresolved": [{
            "path": "/capabilities",
            "reason": "No reviewed extension implementation or capability has been supplied.",
            "required": True,
            "owner": "developer",
        }],
    }


def load_extension_manifest(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=lambda token: _raise_constant(token),
        )
    except OSError as exc:
        raise ValueError("could not read extension manifest: {0}".format(path)) from exc
    except json.JSONDecodeError as exc:
        raise ValueError("extension manifest is not valid JSON: {0}".format(exc.msg)) from exc
    if not isinstance(value, dict):
        raise ValueError("extension manifest root must be an object")
    return value


def canonical_extension_manifest_hash(manifest: Mapping[str, Any]) -> str:
    value = deepcopy(dict(manifest))
    value["content_hash"] = ""
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("extension manifest must contain finite JSON values") from exc
    return hashlib.sha256(payload).hexdigest()


def seal_extension_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    result = deepcopy(dict(manifest))
    result["content_hash"] = canonical_extension_manifest_hash(result)
    return result


def is_extension_compile_ready(manifest: Mapping[str, Any]) -> bool:
    if any(item.severity == "error" for item in validate_extension_manifest(manifest)):
        return False
    return bool(manifest.get("files") and manifest.get("capabilities")) and not any(
        isinstance(item, Mapping) and item.get("required") is True
        for item in manifest.get("unresolved", [])
    )


def validate_extension_manifest(manifest: Mapping[str, Any]) -> List[ExtensionDiagnostic]:
    diagnostics: List[ExtensionDiagnostic] = []
    if not isinstance(manifest, Mapping):
        return [_error("root.type", "$", "Extension manifest root must be an object.")]
    for key in sorted(_ROOT_FIELDS - set(manifest)):
        diagnostics.append(_error("root.required", "/" + key, "Required field is missing."))
    if set(manifest) - _ROOT_FIELDS:
        diagnostics.append(_error("root.extra", "$", "Unsupported root field."))
    if manifest.get("manifest_version") != EXTENSION_MANIFEST_VERSION:
        diagnostics.append(_error("version.manifest", "/manifest_version", "Unsupported extension manifest version."))
    if manifest.get("sdk_version") != EXTENSION_SDK_VERSION:
        diagnostics.append(_error("version.sdk", "/sdk_version", "Unsupported extension SDK version."))
    if not isinstance(manifest.get("extension_id"), str) or not _EXTENSION_ID.fullmatch(str(manifest.get("extension_id", ""))):
        diagnostics.append(_error("extension_id.format", "/extension_id", "Extension ID must use the extension: namespace without a capability path."))
    if not isinstance(manifest.get("version"), str) or not _SEMVER.fullmatch(str(manifest.get("version", ""))):
        diagnostics.append(_error("version.semver", "/version", "Extension version must be exact major.minor.patch."))
    content_hash = manifest.get("content_hash")
    if not isinstance(content_hash, str) or (content_hash and not _SHA256.fullmatch(content_hash)):
        diagnostics.append(_error("hash.format", "/content_hash", "Content hash must be empty or lowercase SHA-256."))
    _validate_metadata(manifest.get("metadata"), diagnostics)
    entrypoint_path = _validate_entrypoint(manifest.get("entrypoint"), diagnostics)
    file_paths = _validate_files(manifest.get("files"), diagnostics)
    if entrypoint_path and entrypoint_path not in file_paths:
        diagnostics.append(_error("entrypoint.inventory", "/entrypoint/file", "Entrypoint must be present in the verified file inventory."))
    _validate_capabilities(manifest.get("capabilities"), diagnostics)
    _validate_permissions(manifest.get("permissions"), diagnostics)
    _validate_runtime(manifest.get("runtime"), manifest.get("metadata"), diagnostics)
    _validate_dependencies(manifest.get("dependencies"), manifest.get("extension_id"), diagnostics)
    _validate_unresolved(manifest.get("unresolved"), diagnostics)
    if not isinstance(manifest.get("provenance"), Mapping):
        diagnostics.append(_error("provenance.type", "/provenance", "Provenance must be an object."))
    _validate_determinism(manifest, diagnostics)
    _validate_json(manifest, "$", diagnostics)
    return diagnostics


def verify_extension_package(root: Path, manifest_name: str = "extension.json") -> VerifiedExtensionPackage:
    root = Path(root).resolve()
    manifest_path = (root / manifest_name).resolve()
    if root not in manifest_path.parents:
        raise ValueError("extension manifest must be inside the package root")
    manifest = load_extension_manifest(manifest_path)
    errors = [item for item in validate_extension_manifest(manifest) if item.severity == "error"]
    if errors:
        raise ValueError("invalid extension manifest at {0}: {1}".format(errors[0].path, errors[0].message))
    if not is_extension_compile_ready(manifest):
        raise ValueError("extension manifest has required unresolved fields or no implementation")
    if manifest.get("content_hash") != canonical_extension_manifest_hash(manifest):
        raise ValueError("extension manifest is not sealed or its content hash is invalid")
    seen_case: Set[str] = set()
    for item in manifest["files"]:
        relative = _package_path(str(item["path"]))
        case_key = relative.as_posix().casefold()
        if case_key in seen_case:
            raise ValueError("extension package contains case-colliding file paths")
        seen_case.add(case_key)
        raw_target = root / Path(*relative.parts)
        target = raw_target.resolve()
        if root not in target.parents or not target.is_file() or _path_uses_symlink(root, relative):
            raise ValueError("extension inventory file is missing, outside root or a symlink: {0}".format(relative))
        payload = target.read_bytes()
        if len(payload) != item["byte_size"]:
            raise ValueError("extension inventory byte size changed: {0}".format(relative))
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError("extension inventory hash changed: {0}".format(relative))
        if target.suffix.casefold() == ".py":
            _validate_python_source(
                payload, relative.as_posix(),
                manifest["permissions"].get("python_modules", []),
                [record["path"] for record in manifest["files"]],
            )
    return VerifiedExtensionPackage(
        root=root,
        manifest_path=manifest_path,
        manifest=deepcopy(manifest),
        extension_id=str(manifest["extension_id"]),
        version=str(manifest["version"]),
        content_hash=str(manifest["content_hash"]),
        capability_ids=tuple(sorted(str(item["id"]) for item in manifest["capabilities"])),
    )


def _validate_metadata(value: Any, diagnostics: List[ExtensionDiagnostic]) -> None:
    path = "/metadata"
    keys = {"name", "description", "publisher", "trust"}
    if not isinstance(value, Mapping):
        diagnostics.append(_error("metadata.type", path, "Metadata must be an object."))
        return
    if set(value) != keys:
        diagnostics.append(_error("metadata.fields", path, "Metadata fields must be exact."))
    for key in ("name", "publisher"):
        if not isinstance(value.get(key), str) or not value.get(key):
            diagnostics.append(_error("metadata." + key, path + "/" + key, "Metadata value is required."))
    if not isinstance(value.get("description"), str):
        diagnostics.append(_error("metadata.description", path + "/description", "Description must be a string."))
    if value.get("trust") not in ("first_party", "designer_reviewed", "untrusted_generated"):
        diagnostics.append(_error("metadata.trust", path + "/trust", "Unsupported trust level."))


def _validate_entrypoint(value: Any, diagnostics: List[ExtensionDiagnostic]) -> Optional[str]:
    if not isinstance(value, Mapping) or set(value) != {"file", "factory"}:
        diagnostics.append(_error("entrypoint.type", "/entrypoint", "Entrypoint requires exact file and factory fields."))
        return None
    try:
        path = _package_path(value.get("file"))
    except ValueError as exc:
        diagnostics.append(_error("entrypoint.path", "/entrypoint/file", str(exc)))
        path = None
    if not isinstance(value.get("factory"), str) or not _FACTORY.fullmatch(str(value.get("factory", ""))):
        diagnostics.append(_error("entrypoint.factory", "/entrypoint/factory", "Factory must be a Python identifier."))
    return path.as_posix() if path else None


def _validate_files(value: Any, diagnostics: List[ExtensionDiagnostic]) -> Set[str]:
    result: Set[str] = set()
    if not isinstance(value, list):
        diagnostics.append(_error("files.type", "/files", "Files must be an array."))
        return result
    for index, item in enumerate(value):
        path = "/files/{0}".format(index)
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256", "byte_size"}:
            diagnostics.append(_error("file.type", path, "File record fields must be exact."))
            continue
        try:
            relative = _package_path(item.get("path")).as_posix()
        except ValueError as exc:
            diagnostics.append(_error("file.path", path + "/path", str(exc)))
            continue
        if relative in result:
            diagnostics.append(_error("file.duplicate", path + "/path", "File path is duplicated."))
        result.add(relative)
        if not isinstance(item.get("sha256"), str) or not _SHA256.fullmatch(str(item.get("sha256", ""))):
            diagnostics.append(_error("file.hash", path + "/sha256", "File requires lowercase SHA-256."))
        size = item.get("byte_size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            diagnostics.append(_error("file.size", path + "/byte_size", "File size must be a non-negative integer."))
    return result


def _validate_capabilities(value: Any, diagnostics: List[ExtensionDiagnostic]) -> None:
    if not isinstance(value, list):
        diagnostics.append(_error("capabilities.type", "/capabilities", "Capabilities must be an array."))
        return
    seen: Set[str] = set()
    exact = {
        "id", "kind", "method", "request_schema", "response_schema",
        "deterministic", "replay_safe", "side_effects", "rule_signature",
    }
    for index, item in enumerate(value):
        path = "/capabilities/{0}".format(index)
        if not isinstance(item, Mapping) or set(item) != exact:
            diagnostics.append(_error("capability.fields", path, "Capability fields must be exact."))
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not _CAPABILITY_ID.fullmatch(identifier):
            diagnostics.append(_error("capability.id", path + "/id", "Capability requires a versioned extension path."))
        elif identifier in seen:
            diagnostics.append(_error("capability.duplicate", path + "/id", "Capability ID is duplicated."))
        else:
            seen.add(identifier)
        if item.get("kind") not in _KINDS:
            diagnostics.append(_error("capability.kind", path + "/kind", "Unsupported capability kind."))
        if not isinstance(item.get("method"), str) or not _LOCAL.fullmatch(str(item.get("method", ""))):
            diagnostics.append(_error("capability.method", path + "/method", "Method must be a local identifier."))
        for key in ("request_schema", "response_schema"):
            if not isinstance(item.get(key), Mapping):
                diagnostics.append(_error("capability.schema", path + "/" + key, "Capability schema must be an object."))
            else:
                try:
                    validate_contract_schema(item[key], path + "/" + key)
                except ContractSchemaError as exc:
                    diagnostics.append(_error("capability.schema", path + "/" + key, str(exc)))
        for key in ("deterministic", "replay_safe"):
            if not isinstance(item.get(key), bool):
                diagnostics.append(_error("capability." + key, path + "/" + key, "Capability flag must be boolean."))
        if item.get("side_effects") not in ("none", "stateful", "external"):
            diagnostics.append(_error("capability.side_effects", path + "/side_effects", "Unsupported side-effect declaration."))
        signature = item.get("rule_signature")
        if item.get("kind") == "rule_function":
            _validate_rule_signature(signature, path + "/rule_signature", diagnostics)
            if item.get("side_effects") != "none" or item.get("deterministic") is not True:
                diagnostics.append(_error("capability.rule_purity", path, "Rule functions must be deterministic and side-effect free."))
        elif signature is not None:
            diagnostics.append(_error("capability.rule_signature", path + "/rule_signature", "Only rule_function may declare a Rule signature."))
        if item.get("replay_safe") is True and item.get("deterministic") is not True:
            diagnostics.append(_error("capability.replay", path + "/replay_safe", "Replay-safe capabilities must be deterministic."))


def _validate_rule_signature(value: Any, path: str, diagnostics: List[ExtensionDiagnostic]) -> None:
    if not isinstance(value, Mapping) or set(value) != {"result_type", "argument_types", "variadic"}:
        diagnostics.append(_error("signature.fields", path, "Rule signature fields must be exact."))
        return
    if not isinstance(value.get("result_type"), str) or not value.get("result_type"):
        diagnostics.append(_error("signature.result", path + "/result_type", "Result type is required."))
    args = value.get("argument_types")
    if not isinstance(args, list) or any(not isinstance(item, str) or not item for item in args):
        diagnostics.append(_error("signature.arguments", path + "/argument_types", "Argument types must be strings."))
    if not isinstance(value.get("variadic"), bool):
        diagnostics.append(_error("signature.variadic", path + "/variadic", "Variadic must be boolean."))


def _validate_permissions(value: Any, diagnostics: List[ExtensionDiagnostic]) -> None:
    keys = {"filesystem_read", "filesystem_write", "network", "subprocess", "native_code", "environment", "python_modules"}
    if not isinstance(value, Mapping) or set(value) != keys:
        diagnostics.append(_error("permissions.fields", "/permissions", "Permission fields must be exact."))
        return
    for key in ("filesystem_read", "filesystem_write", "environment", "python_modules"):
        items = value.get(key)
        if not isinstance(items, list) or len(items) != len(set(items)) or any(not isinstance(item, str) for item in items):
            diagnostics.append(_error("permissions." + key, "/permissions/" + key, "Permission list must contain unique strings."))
    for key in ("network", "subprocess", "native_code"):
        if not isinstance(value.get(key), bool):
            diagnostics.append(_error("permissions." + key, "/permissions/" + key, "Permission must be boolean."))
    reads = value.get("filesystem_read", []) if isinstance(value.get("filesystem_read"), list) else []
    writes = value.get("filesystem_write", []) if isinstance(value.get("filesystem_write"), list) else []
    if any(item != "package://" for item in reads):
        diagnostics.append(_error("permissions.read_scope", "/permissions/filesystem_read", "Local host only supports package:// read access."))
    if writes:
        diagnostics.append(_error("permissions.write_scope", "/permissions/filesystem_write", "Local host does not grant filesystem writes."))
    modules = value.get("python_modules", []) if isinstance(value.get("python_modules"), list) else []
    if any(not _MODULE.fullmatch(item) for item in modules):
        diagnostics.append(_error("permissions.module", "/permissions/python_modules", "Python module allowlist contains an invalid module name."))


def _validate_runtime(value: Any, metadata: Any, diagnostics: List[ExtensionDiagnostic]) -> None:
    keys = {
        "protocol_version", "python_min", "timeout_ms", "max_request_bytes",
        "max_response_bytes", "max_memory_mb", "max_cpu_ms", "max_processes", "isolation",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        diagnostics.append(_error("runtime.fields", "/runtime", "Runtime fields must be exact."))
        return
    if value.get("protocol_version") != EXTENSION_RPC_VERSION:
        diagnostics.append(_error("runtime.protocol", "/runtime/protocol_version", "Unsupported RPC protocol."))
    if not isinstance(value.get("python_min"), str) or not re.fullmatch(r"[1-9][0-9]*\.[0-9]+", str(value.get("python_min", ""))):
        diagnostics.append(_error("runtime.python", "/runtime/python_min", "python_min must be major.minor."))
    limits = {
        "timeout_ms": (10, 30000), "max_request_bytes": (256, 1048576),
        "max_response_bytes": (256, 1048576), "max_memory_mb": (16, 2048),
        "max_cpu_ms": (10, 60000),
    }
    for key, (minimum, maximum) in limits.items():
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, int) or not minimum <= item <= maximum:
            diagnostics.append(_error("runtime." + key, "/runtime/" + key, "Runtime limit is outside the supported range."))
    if value.get("max_processes") != 1:
        diagnostics.append(_error("runtime.processes", "/runtime/max_processes", "Local host permits exactly one process."))
    if value.get("isolation") not in ("cooperative_process", "os_sandbox_required"):
        diagnostics.append(_error("runtime.isolation", "/runtime/isolation", "Unsupported isolation level."))
    trust = metadata.get("trust") if isinstance(metadata, Mapping) else None
    if trust == "untrusted_generated" and value.get("isolation") != "os_sandbox_required":
        diagnostics.append(_error("runtime.untrusted", "/runtime/isolation", "Untrusted generated code must require an OS sandbox."))


def _validate_dependencies(value: Any, own_id: Any, diagnostics: List[ExtensionDiagnostic]) -> None:
    if not isinstance(value, list):
        diagnostics.append(_error("dependencies.type", "/dependencies", "Dependencies must be an array."))
        return
    seen: Set[str] = set()
    for index, item in enumerate(value):
        path = "/dependencies/{0}".format(index)
        if not isinstance(item, Mapping) or set(item) != {"extension_id", "version", "content_hash"}:
            diagnostics.append(_error("dependency.fields", path, "Dependency pin fields must be exact."))
            continue
        identifier = item.get("extension_id")
        if not isinstance(identifier, str) or not _EXTENSION_ID.fullmatch(identifier):
            diagnostics.append(_error("dependency.id", path + "/extension_id", "Dependency extension ID is invalid."))
        elif identifier == own_id:
            diagnostics.append(_error("dependency.self", path + "/extension_id", "Extension cannot depend on itself."))
        elif identifier in seen:
            diagnostics.append(_error("dependency.duplicate", path + "/extension_id", "Dependency is duplicated."))
        else:
            seen.add(identifier)
        if not isinstance(item.get("version"), str) or not _SEMVER.fullmatch(str(item.get("version", ""))):
            diagnostics.append(_error("dependency.version", path + "/version", "Dependency version must be exact semver."))
        if not isinstance(item.get("content_hash"), str) or not _SHA256.fullmatch(str(item.get("content_hash", ""))):
            diagnostics.append(_error("dependency.hash", path + "/content_hash", "Dependency requires a manifest hash."))


def _validate_unresolved(value: Any, diagnostics: List[ExtensionDiagnostic]) -> None:
    if not isinstance(value, list):
        diagnostics.append(_error("unresolved.type", "/unresolved", "Unresolved must be an array."))
        return
    keys = {"path", "reason", "required", "owner"}
    for index, item in enumerate(value):
        path = "/unresolved/{0}".format(index)
        if not isinstance(item, Mapping) or set(item) != keys:
            diagnostics.append(_error("unresolved.fields", path, "Unresolved fields must be exact."))
            continue
        if not isinstance(item.get("path"), str) or not item.get("path"):
            diagnostics.append(_error("unresolved.path", path + "/path", "Unresolved path is required."))
        if not isinstance(item.get("reason"), str) or not item.get("reason"):
            diagnostics.append(_error("unresolved.reason", path + "/reason", "Unresolved reason is required."))
        if not isinstance(item.get("required"), bool):
            diagnostics.append(_error("unresolved.required", path + "/required", "Required must be boolean."))
        if item.get("owner") not in ("developer", "designer", "llm", "security_review"):
            diagnostics.append(_error("unresolved.owner", path + "/owner", "Unsupported unresolved owner."))


def _validate_determinism(manifest: Mapping[str, Any], diagnostics: List[ExtensionDiagnostic]) -> None:
    capabilities = manifest.get("capabilities", [])
    deterministic = bool(isinstance(capabilities, list) and capabilities and all(
        isinstance(item, Mapping) and item.get("deterministic") is True for item in capabilities
    ))
    if not deterministic:
        return
    permissions = manifest.get("permissions", {})
    if not isinstance(permissions, Mapping):
        return
    external = bool(
        permissions.get("network") or permissions.get("subprocess") or permissions.get("native_code")
        or permissions.get("filesystem_write") or permissions.get("environment")
    )
    if external:
        diagnostics.append(_error(
            "determinism.permissions", "/permissions",
            "A fully deterministic extension cannot request external or mutable permissions.",
        ))


def _package_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise ValueError("package path must be a forward-slash relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("package path cannot traverse or be absolute")
    for part in path.parts:
        if any(character in _WINDOWS_FORBIDDEN for character in part) or part.endswith((" ", ".")):
            raise ValueError("package path is not portable to Windows")
    return path


def _validate_python_source(
    payload: bytes, path: str, allowed_modules: Sequence[str], inventory_paths: Sequence[str],
) -> None:
    try:
        source = payload.decode("utf-8")
        tree = ast.parse(source, filename=path)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ValueError("extension Python source is not valid UTF-8 syntax: {0}".format(path)) from exc
    allowed = {str(item).split(".", 1)[0] for item in allowed_modules}
    for item in inventory_paths:
        candidate = PurePosixPath(item)
        if str(item).endswith(".py"):
            allowed.add(candidate.stem)
            allowed.add(candidate.parts[0])
    forbidden_modules = {
        "builtins", "ctypes", "importlib", "inspect", "marshal", "multiprocessing",
        "os", "pathlib", "pickle", "shutil", "socket", "subprocess", "sys", "tempfile",
    }
    forbidden_calls = {"__import__", "compile", "eval", "exec"}
    forbidden_attributes = {"__builtins__", "__class__", "__dict__", "__globals__", "__subclasses__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name.split(".", 1)[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [] if node.level else [str(node.module or "").split(".", 1)[0]]
        else:
            modules = []
        for module in modules:
            if module in forbidden_modules or module not in allowed:
                raise ValueError("extension import is not allowlisted: {0} ({1})".format(module, path))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
            raise ValueError("extension uses forbidden dynamic execution: {0} ({1})".format(node.func.id, path))
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attributes:
            raise ValueError("extension uses forbidden reflective attribute: {0} ({1})".format(node.attr, path))


def _path_uses_symlink(root: Path, relative: PurePosixPath) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _validate_json(value: Any, path: str, diagnostics: List[ExtensionDiagnostic]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            diagnostics.append(_error("number.finite", path, "Non-finite values are forbidden."))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json(item, path + "/" + str(index), diagnostics)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                diagnostics.append(_error("object.key", path, "Object keys must be strings."))
            else:
                _validate_json(item, path + "/" + key, diagnostics)
        return
    diagnostics.append(_error("json.type", path, "Manifest contains a non-JSON value."))


def _raise_constant(token: str) -> None:
    raise ValueError("non-finite JSON constant: {0}".format(token))


def _error(code: str, path: str, message: str) -> ExtensionDiagnostic:
    return ExtensionDiagnostic("error", code, path, message)
