"""Exact package registry, dependency resolver and multi-extension session."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .host import ExtensionHost, ExtensionHostError, InvocationRecord
from .manifest import VerifiedExtensionPackage, verify_extension_package


class ExtensionRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class RegisteredCapability:
    id: str
    kind: str
    provider_id: str
    provider_version: str
    provider_hash: str
    declaration: Mapping[str, Any]


class ExtensionRegistry:
    def __init__(self) -> None:
        self._packages: Dict[str, VerifiedExtensionPackage] = {}
        self._capabilities: Dict[str, RegisteredCapability] = {}

    @property
    def packages(self) -> Tuple[VerifiedExtensionPackage, ...]:
        return tuple(self._packages[key] for key in sorted(self._packages))

    @property
    def capabilities(self) -> Tuple[RegisteredCapability, ...]:
        return tuple(self._capabilities[key] for key in sorted(self._capabilities))

    def register(self, root: Path, manifest_name: str = "extension.json") -> VerifiedExtensionPackage:
        return self.register_many(((root, manifest_name),))[0]

    def register_many(
        self, packages: Sequence[Tuple[Path, str]],
    ) -> Tuple[VerifiedExtensionPackage, ...]:
        verified = tuple(
            verify_extension_package(root, manifest_name)
            for root, manifest_name in packages
        )
        old_packages = dict(self._packages)
        old_capabilities = dict(self._capabilities)
        try:
            for package in verified:
                if package.extension_id in self._packages:
                    raise ExtensionRegistryError("extension ID is already registered: {0}".format(package.extension_id))
                self._packages[package.extension_id] = package
                for item in package.manifest["capabilities"]:
                    identifier = str(item["id"])
                    if identifier in self._capabilities:
                        raise ExtensionRegistryError("capability already has a provider: {0}".format(identifier))
                    self._capabilities[identifier] = RegisteredCapability(
                        id=identifier,
                        kind=str(item["kind"]),
                        provider_id=package.extension_id,
                        provider_version=package.version,
                        provider_hash=package.content_hash,
                        declaration=deepcopy(item),
                    )
            self._resolve_all_dependencies()
        except Exception:
            self._packages = old_packages
            self._capabilities = old_capabilities
            raise
        return verified

    def package(self, extension_id: str) -> VerifiedExtensionPackage:
        try:
            return self._packages[extension_id]
        except KeyError as exc:
            raise ExtensionRegistryError("extension is not registered: {0}".format(extension_id)) from exc

    def capability(self, capability_id: str) -> RegisteredCapability:
        try:
            return self._capabilities[capability_id]
        except KeyError as exc:
            raise ExtensionRegistryError("capability has no registered provider: {0}".format(capability_id)) from exc

    def verify_pins(self, pins: Sequence[Mapping[str, Any]]) -> Tuple[VerifiedExtensionPackage, ...]:
        if not isinstance(pins, Sequence) or isinstance(pins, (str, bytes)):
            raise ExtensionRegistryError("extension pins must be an array")
        seen = set()
        packages = []
        for pin in pins:
            if not isinstance(pin, Mapping) or set(pin) != {"extension_id", "version", "content_hash"}:
                raise ExtensionRegistryError("extension pin fields must be exact")
            identifier = str(pin["extension_id"])
            if identifier in seen:
                raise ExtensionRegistryError("extension pin is duplicated: {0}".format(identifier))
            seen.add(identifier)
            package = self.package(identifier)
            if pin["version"] != package.version or pin["content_hash"] != package.content_hash:
                raise ExtensionRegistryError("extension pin does not match registered package: {0}".format(identifier))
            packages.append(package)
        pinned = {item.extension_id for item in packages}
        for package in packages:
            for dependency in package.manifest["dependencies"]:
                if dependency["extension_id"] not in pinned:
                    raise ExtensionRegistryError(
                        "project does not pin transitive extension dependency: {0}".format(dependency["extension_id"])
                    )
        return tuple(packages)

    def verify_capabilities_pinned(
        self, capability_ids: Sequence[str], pins: Sequence[Mapping[str, Any]],
    ) -> Tuple[RegisteredCapability, ...]:
        packages = self.verify_pins(pins)
        pinned = {item.extension_id for item in packages}
        result = []
        for identifier in capability_ids:
            capability = self.capability(str(identifier))
            if capability.provider_id not in pinned:
                raise ExtensionRegistryError(
                    "capability provider is not pinned by the project: {0}".format(identifier)
                )
            result.append(capability)
        return tuple(result)

    def create_session(
        self, capability_ids: Sequence[str], *,
        approved_hashes: Sequence[str] = (),
        context: Optional[Mapping[str, Any]] = None,
    ) -> "ExtensionSession":
        ordered = self._resolve_capability_packages(capability_ids)
        return ExtensionSession(
            self, tuple(str(item) for item in capability_ids), ordered,
            approved_hashes=approved_hashes, context=context,
        )

    def _resolve_all_dependencies(self) -> Tuple[VerifiedExtensionPackage, ...]:
        return self._topological(tuple(self._packages))

    def _resolve_capability_packages(self, capability_ids: Sequence[str]) -> Tuple[VerifiedExtensionPackage, ...]:
        providers = {self.capability(str(identifier)).provider_id for identifier in capability_ids}
        return self._topological(tuple(sorted(providers)))

    def _topological(self, roots: Sequence[str]) -> Tuple[VerifiedExtensionPackage, ...]:
        visiting: Set[str] = set()
        visited: Set[str] = set()
        ordered: List[VerifiedExtensionPackage] = []

        def visit(identifier: str) -> None:
            if identifier in visited:
                return
            if identifier in visiting:
                raise ExtensionRegistryError("extension dependency cycle contains: {0}".format(identifier))
            visiting.add(identifier)
            package = self.package(identifier)
            for dependency in sorted(package.manifest["dependencies"], key=lambda item: item["extension_id"]):
                target = self.package(str(dependency["extension_id"]))
                if dependency["version"] != target.version or dependency["content_hash"] != target.content_hash:
                    raise ExtensionRegistryError(
                        "extension dependency pin mismatch: {0} -> {1}".format(identifier, target.extension_id)
                    )
                visit(target.extension_id)
            visiting.remove(identifier)
            visited.add(identifier)
            ordered.append(package)

        for identifier in roots:
            visit(identifier)
        return tuple(ordered)


class ExtensionSession:
    def __init__(
        self, registry: ExtensionRegistry, capability_ids: Sequence[str],
        packages: Sequence[VerifiedExtensionPackage], *,
        approved_hashes: Sequence[str], context: Optional[Mapping[str, Any]],
    ) -> None:
        self.registry = registry
        self.capability_ids = tuple(capability_ids)
        self.packages = tuple(packages)
        self.approved_hashes = tuple(approved_hashes)
        self.context = deepcopy(dict(context or {}))
        self.hosts: Dict[str, ExtensionHost] = {}
        self.state = "created"

    @property
    def invocation_records(self) -> Tuple[InvocationRecord, ...]:
        records = [item for host in self.hosts.values() for item in host.invocation_records]
        return tuple(sorted(records, key=lambda item: (item.extension_id, item.sequence)))

    def start(self) -> "ExtensionSession":
        if self.state != "created":
            raise ExtensionRegistryError("extension session can only start once")
        try:
            for package in self.packages:
                host = ExtensionHost(
                    package, approved_hashes=self.approved_hashes,
                ).start()
                self.hosts[package.extension_id] = host
                dependency_context = [
                    {
                        "extension_id": item["extension_id"],
                        "version": item["version"],
                        "content_hash": item["content_hash"],
                    }
                    for item in package.manifest["dependencies"]
                ]
                host.initialize({
                    "project": deepcopy(self.context),
                    "extension": {
                        "extension_id": package.extension_id,
                        "version": package.version,
                        "content_hash": package.content_hash,
                    },
                    "dependencies": dependency_context,
                })
        except Exception:
            self.close()
            raise
        self.state = "initialized"
        return self

    def invoke(self, capability_id: str, request: Any) -> Any:
        if self.state != "initialized":
            raise ExtensionRegistryError("extension session is not initialized")
        if capability_id not in self.capability_ids:
            raise ExtensionRegistryError("capability was not granted to this session: {0}".format(capability_id))
        capability = self.registry.capability(capability_id)
        return self.hosts[capability.provider_id].invoke(capability_id, request)

    def rule_function_specs(self):
        from srtp.ir_v2.expression import FunctionSpec

        result = {}
        for identifier in self.capability_ids:
            capability = self.registry.capability(identifier)
            if capability.kind != "rule_function":
                raise ExtensionRegistryError(
                    "Rule Runtime only accepts rule_function capabilities: {0}".format(identifier)
                )
            signature = capability.declaration["rule_signature"]

            def evaluator(arguments, context, capability_id=identifier, session=self):
                return session.invoke(capability_id, {"arguments": list(arguments)})

            result[identifier] = FunctionSpec(
                identifier,
                evaluator,
                str(signature["result_type"]),
                tuple(str(item) for item in signature["argument_types"]),
                bool(signature["variadic"]),
            )
        return result

    def close(self) -> None:
        for package in reversed(self.packages):
            host = self.hosts.pop(package.extension_id, None)
            if host is not None:
                host.close()
        self.state = "closed"

    def __enter__(self) -> "ExtensionSession":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
