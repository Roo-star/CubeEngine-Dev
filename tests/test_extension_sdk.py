import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.extension_sdk import (
    EXTENSION_MANIFEST_SCHEMA_PATH,
    EXTENSION_MANIFEST_VERSION,
    EXTENSION_SDK_CAPABILITIES,
    EXTENSION_SDK_VERSION,
    ContractSchemaError,
    ContractValidationError,
    ExtensionHost,
    ExtensionHostError,
    ExtensionRegistry,
    ExtensionRegistryError,
    assess_extension_conformance,
    canonical_extension_manifest_hash,
    is_extension_compile_ready,
    new_extension_manifest,
    seal_extension_manifest,
    validate_contract_schema,
    validate_extension_manifest,
    validate_json_contract,
    verify_extension_replay,
    verify_extension_package,
)
from srtp.input_ir_v2 import PhysicalInputEvent, seal_input_ir
from srtp.ir_v2 import RuleRuntimeError, compile_rule_ir, seal_rule_ir
from srtp.project_manifest_v2 import compile_project_manifest, seal_project_manifest
from srtp.scene_ir_v2 import seal_scene_ir
from tests.test_project_manifest_v2 import four_ir_fixture, manifest_fixture


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "srtp" / "examples" / "extensions" / "parity"
CAPABILITY = "extension:cubeengine.parity/is_even/1"


def _capability(identifier, deterministic=True, side_effects="none", kind="rule_function"):
    return {
        "id": identifier,
        "kind": kind,
        "method": "run",
        "request_schema": {
            "type": "object", "required": ["arguments"],
            "properties": {
                "arguments": {
                    "type": "array", "minItems": 1, "maxItems": 1,
                    "items": {"type": "integer"},
                },
            },
            "additionalProperties": False,
        },
        "response_schema": {"type": "boolean"},
        "deterministic": deterministic,
        "replay_safe": deterministic,
        "side_effects": side_effects,
        "rule_signature": {
            "result_type": "core:bool", "argument_types": ["core:int"],
            "variadic": False,
        } if kind == "rule_function" else None,
    }


def _write_package(
    root, source, *, extension_id="extension:test.adapter",
    capability_id="extension:test.adapter/run/1", trust="first_party",
    isolation="cooperative_process", timeout_ms=500,
    deterministic=True, side_effects="none", modules=(),
    kind="rule_function",
):
    root.mkdir(parents=True, exist_ok=True)
    payload = source.encode("utf-8")
    (root / "adapter.py").write_bytes(payload)
    manifest = new_extension_manifest(extension_id, "1.0.0", "Test Adapter")
    manifest["metadata"].update({
        "description": "test package", "publisher": "CubeEngine Tests", "trust": trust,
    })
    manifest["entrypoint"] = {"file": "adapter.py", "factory": "create_extension"}
    manifest["files"] = [{
        "path": "adapter.py", "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_size": len(payload),
    }]
    manifest["capabilities"] = [_capability(capability_id, deterministic, side_effects, kind)]
    manifest["permissions"]["python_modules"] = list(modules)
    manifest["runtime"].update({
        "timeout_ms": timeout_ms, "max_cpu_ms": 5000, "isolation": isolation,
    })
    manifest["unresolved"] = []
    manifest = seal_extension_manifest(manifest)
    (root / "extension.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return manifest


SIMPLE_SOURCE = '''
class Adapter:
    def initialize(self, context): return {"ready": True}
    def invoke(self, capability_id, method, payload): return payload["arguments"][0] > 0
    def snapshot(self): return {}
    def restore(self, snapshot): return {"restored": True}
    def shutdown(self): return {"closed": True}
def create_extension(): return Adapter()
'''.lstrip()


class ExtensionManifestContractTests(unittest.TestCase):
    def test_schema_capability_honest_draft_and_checked_package(self):
        schema = json.loads(EXTENSION_MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
        draft = new_extension_manifest("extension:test.empty", "0.1.0", "Empty")
        package = verify_extension_package(SAMPLE)

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["manifest_version"]["const"], EXTENSION_MANIFEST_VERSION)
        self.assertEqual(EXTENSION_SDK_CAPABILITIES["sdk_version"], EXTENSION_SDK_VERSION)
        self.assertFalse(is_extension_compile_ready(draft))
        self.assertEqual(package.content_hash, canonical_extension_manifest_hash(package.manifest))
        self.assertFalse(validate_extension_manifest(package.manifest))

    def test_contract_subset_rejects_unsupported_schema_and_bad_value(self):
        with self.assertRaises(ContractSchemaError):
            validate_contract_schema({"$ref": "https://untrusted.invalid/schema"})
        with self.assertRaises(ContractValidationError):
            validate_json_contract(True, {"type": "integer"})

    def test_package_byte_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "package"
            _write_package(root, SIMPLE_SOURCE)
            (root / "adapter.py").write_text(SIMPLE_SOURCE + "# changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "byte size changed|hash changed"):
                verify_extension_package(root)

    def test_static_gate_rejects_dangerous_import_even_if_requested(self):
        source = "import os\n" + SIMPLE_SOURCE
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "dangerous"
            _write_package(root, source, modules=["os"])
            with self.assertRaisesRegex(ValueError, "import is not allowlisted: os"):
                verify_extension_package(root)


class ExtensionHostTests(unittest.TestCase):
    def test_lifecycle_contract_purity_and_audit_record(self):
        package = verify_extension_package(SAMPLE)
        host = ExtensionHost(package).start()
        try:
            self.assertEqual(host.initialize({"project": "test"}), {"ready": True})
            self.assertTrue(host.invoke(CAPABILITY, {"arguments": [4]}))
            self.assertFalse(host.invoke(CAPABILITY, {"arguments": [5]}))
            self.assertEqual(len(host.invocation_records), 2)
            self.assertEqual(host.invocation_records[0].capability_id, CAPABILITY)
            with self.assertRaisesRegex(ExtensionHostError, "wrong JSON type"):
                host.invoke(CAPABILITY, {"arguments": [True]})
        finally:
            host.close()
        self.assertEqual(host.state, "closed")

    def test_review_approval_and_untrusted_os_sandbox_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            reviewed_root = Path(folder) / "reviewed"
            reviewed_manifest = _write_package(
                reviewed_root, SIMPLE_SOURCE,
                extension_id="extension:test.reviewed",
                capability_id="extension:test.reviewed/run/1",
                trust="designer_reviewed",
            )
            reviewed = verify_extension_package(reviewed_root)
            with self.assertRaisesRegex(ExtensionHostError, "explicitly approved"):
                ExtensionHost(reviewed).start()
            host = ExtensionHost(reviewed, approved_hashes=[reviewed_manifest["content_hash"]]).start()
            host.initialize({})
            host.close()

            untrusted_root = Path(folder) / "untrusted"
            _write_package(
                untrusted_root, SIMPLE_SOURCE,
                extension_id="extension:test.untrusted",
                capability_id="extension:test.untrusted/run/1",
                trust="untrusted_generated", isolation="os_sandbox_required",
            )
            with self.assertRaisesRegex(ExtensionHostError, "external OS sandbox"):
                ExtensionHost(verify_extension_package(untrusted_root)).start()

    def test_timeout_terminates_worker_and_adapter_error_stays_isolated(self):
        slow_source = '''
import time
class Adapter:
    def initialize(self, context): return {}
    def invoke(self, capability_id, method, payload): time.sleep(1); return True
    def snapshot(self): return {}
    def restore(self, snapshot): return {}
    def shutdown(self): return {}
def create_extension(): return Adapter()
'''.lstrip()
        crash_source = '''
class Adapter:
    def initialize(self, context): return {}
    def invoke(self, capability_id, method, payload): raise ValueError("fixture crash")
    def snapshot(self): return {}
    def restore(self, snapshot): return {}
    def shutdown(self): return {}
def create_extension(): return Adapter()
'''.lstrip()
        with tempfile.TemporaryDirectory() as folder:
            slow_root = Path(folder) / "slow"
            _write_package(
                slow_root, slow_source,
                extension_id="extension:test.slow", capability_id="extension:test.slow/run/1",
                timeout_ms=50, deterministic=False, side_effects="external",
                modules=["time"], kind="importer",
            )
            slow = ExtensionHost(verify_extension_package(slow_root)).start()
            slow.initialize({})
            with self.assertRaisesRegex(ExtensionHostError, "wall-clock timeout"):
                slow.invoke("extension:test.slow/run/1", {"arguments": [1]})
            slow.close()

            crash_root = Path(folder) / "crash"
            _write_package(
                crash_root, crash_source,
                extension_id="extension:test.crash", capability_id="extension:test.crash/run/1",
            )
            crash = ExtensionHost(verify_extension_package(crash_root)).start()
            crash.initialize({})
            with self.assertRaisesRegex(ExtensionHostError, "fixture crash"):
                crash.invoke("extension:test.crash/run/1", {"arguments": [1]})
            self.assertEqual(crash.state, "initialized")
            crash.close()

    def test_declared_pure_capability_cannot_change_extension_snapshot(self):
        impure_source = '''
class Adapter:
    def __init__(self): self.count = 0
    def initialize(self, context): return {}
    def invoke(self, capability_id, method, payload): self.count += 1; return True
    def snapshot(self): return {"count": self.count}
    def restore(self, snapshot): self.count = snapshot["count"]; return {}
    def shutdown(self): return {}
def create_extension(): return Adapter()
'''.lstrip()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "impure"
            _write_package(root, impure_source)
            host = ExtensionHost(verify_extension_package(root)).start()
            host.initialize({})
            with self.assertRaisesRegex(ExtensionHostError, "changed its adapter snapshot"):
                host.invoke("extension:test.adapter/run/1", {"arguments": [1]})
            host.close()


class ExtensionRegistryAndConformanceTests(unittest.TestCase):
    def test_registry_exact_pins_and_duplicate_capability(self):
        registry = ExtensionRegistry()
        package = registry.register(SAMPLE)
        pins = [{
            "extension_id": package.extension_id,
            "version": package.version,
            "content_hash": package.content_hash,
        }]

        self.assertEqual(registry.verify_pins(pins)[0].extension_id, package.extension_id)
        self.assertEqual(registry.verify_capabilities_pinned([CAPABILITY], pins)[0].kind, "rule_function")
        with self.assertRaisesRegex(ExtensionRegistryError, "already registered"):
            registry.register(SAMPLE)
        stale = deepcopy(pins)
        stale[0]["version"] = "1.0.1"
        with self.assertRaisesRegex(ExtensionRegistryError, "does not match"):
            registry.verify_pins(stale)

    def test_bulk_registry_resolves_exact_dependency_before_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            base_root = Path(folder) / "base"
            base = _write_package(
                base_root, SIMPLE_SOURCE,
                extension_id="extension:test.base",
                capability_id="extension:test.base/run/1",
            )
            dependent_root = Path(folder) / "dependent"
            dependent = _write_package(
                dependent_root, SIMPLE_SOURCE,
                extension_id="extension:test.dependent",
                capability_id="extension:test.dependent/run/1",
            )
            dependent["dependencies"] = [{
                "extension_id": base["extension_id"],
                "version": base["version"],
                "content_hash": base["content_hash"],
            }]
            dependent = seal_extension_manifest(dependent)
            (dependent_root / "extension.json").write_text(
                json.dumps(dependent, ensure_ascii=False, indent=2), encoding="utf-8",
            )

            registry = ExtensionRegistry()
            registry.register_many((
                (dependent_root, "extension.json"),
                (base_root, "extension.json"),
            ))
            session = registry.create_session(["extension:test.dependent/run/1"]).start()
            try:
                self.assertEqual(list(session.hosts), ["extension:test.base", "extension:test.dependent"])
                self.assertTrue(session.invoke(
                    "extension:test.dependent/run/1", {"arguments": [1]},
                ))
            finally:
                session.close()

    def test_fresh_process_conformance_is_deterministic(self):
        report = assess_extension_conformance(
            SAMPLE,
            cases={CAPABILITY: [{"arguments": [2]}, {"arguments": [3]}]},
        )

        self.assertTrue(report.compile_ready)
        self.assertTrue(report.package_verified)
        self.assertTrue(report.lifecycle_passed)
        self.assertTrue(report.determinism_passed)

    def test_replay_safe_capability_reproduces_request_response_hash_audit(self):
        package = verify_extension_package(SAMPLE)
        host = ExtensionHost(package).start()
        try:
            host.initialize({"record": True})
            host.invoke(CAPABILITY, {"arguments": [8]})
            expected = [item.to_mapping() for item in host.invocation_records]
        finally:
            host.close()

        actual = verify_extension_replay(
            package,
            invocations=[(CAPABILITY, {"arguments": [8]})],
            expected_records=expected,
        )
        self.assertEqual(list(actual), expected)
        tampered = deepcopy(expected)
        tampered[0]["response_hash"] = "0" * 64
        with self.assertRaisesRegex(ExtensionHostError, "audit diverged"):
            verify_extension_replay(
                package,
                invocations=[(CAPABILITY, {"arguments": [8]})],
                expected_records=tampered,
            )


class RuleAndProjectExtensionIntegrationTests(unittest.TestCase):
    def _extended_rule(self, rule):
        document = deepcopy(rule)
        document["dependencies"]["extensions"] = [CAPABILITY]
        original = document["actions"][0]["precondition"]
        document["actions"][0]["precondition"] = {
            "op": "and", "args": [
                original,
                {"op": "call", "function": CAPABILITY, "args": [{"op": "literal", "value": 2}]},
            ],
        }
        return seal_rule_ir(document)

    def test_rule_runtime_requires_and_uses_exact_extension_session(self):
        _, base_rule, _, _, _ = four_ir_fixture()
        rule = self._extended_rule(base_rule)
        registry = ExtensionRegistry()
        registry.register(SAMPLE)

        with self.assertRaisesRegex(RuleRuntimeError, "requires an initialized"):
            compile_rule_ir(rule)
        extension_session = registry.create_session([CAPABILITY]).start()
        runtime = compile_rule_ir(rule, extension_session=extension_session)
        try:
            self.assertTrue(runtime.is_legal(0))
            runtime.apply_action(0)
            self.assertGreater(len(extension_session.invocation_records), 0)
        finally:
            runtime.close()

    def test_project_manifest_pins_provider_and_runs_full_input_pipeline(self):
        _, base_rule, scene, asset, input_document = four_ir_fixture()
        rule = self._extended_rule(base_rule)
        scene = deepcopy(scene)
        scene["dependencies"]["rule_ir"] = {
            "document_id": rule["document_id"], "content_hash": rule["content_hash"],
        }
        scene = seal_scene_ir(scene)
        input_document = deepcopy(input_document)
        input_document["dependencies"]["rule_ir"] = {
            "document_id": rule["document_id"], "content_hash": rule["content_hash"],
        }
        input_document = seal_input_ir(input_document)
        manifest = manifest_fixture(rule, scene, asset, input_document)
        registry = ExtensionRegistry()
        package = registry.register(SAMPLE)
        manifest["extensions"] = [{
            "extension_id": package.extension_id,
            "version": package.version,
            "content_hash": package.content_hash,
        }]
        manifest = seal_project_manifest(manifest)

        with tempfile.TemporaryDirectory() as folder:
            bundle = compile_project_manifest(
                manifest, rule_document=rule, scene_document=scene,
                asset_document=asset, input_document=input_document,
                asset_project_root=Path(folder), extension_registry=registry,
            )
            session = bundle.create_session()
            try:
                result = session.handle_input(PhysicalInputEvent(
                    1, "mouse", "mouse.button.primary", "press",
                    position=(10, 10), data={"rule_coordinate": [0, 0, 0]},
                ))
                self.assertEqual(len(result.transitions), 1)
                self.assertFalse(result.rejections)
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
