import json
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.ir_v2 import (
    DeterministicRandomService,
    PCG32,
    RandomServiceError,
    RuleRuntimeError,
    compile_rule_ir,
    new_rule_ir,
    replay_rule_ir,
    validate_rule_ir,
)


ROOT = Path(__file__).parents[1]
VECTORS = ROOT / "srtp" / "ir_v2" / "random-conformance-vectors.json"
CAPABILITIES = ROOT / "srtp" / "ir_v2" / "rule-runtime-capabilities.json"


def _literal(value):
    return {"op": "literal", "value": value}


def _random_document(stream, distribution=None):
    document = new_rule_ir("rule:game.random_test", "Random Test")
    document["topologies"] = [{
        "id": "rule:topology.board", "name": "Board", "kind": "rect_grid", "anchor": "cell",
        "axes": [{"name": "x", "extent": 1, "boundary": "bounded"}],
        "neighborhoods": [],
    }]
    document["participants"] = [{
        "id": "rule:participant.player", "name": "Player", "kind": "human_or_agent",
    }]
    document["state"]["variables"] = [{
        "id": "rule:state.value", "name": "Value", "type": "core:int",
        "scope": "global", "initial": _literal(0),
    }]
    document["random_streams"] = [deepcopy(stream)]
    document["actions"] = [{
        "id": "rule:action.draw", "name": "Draw",
        "actor": _literal("rule:participant.player"), "parameters": [],
        "precondition": _literal(True),
        "effects": [
            {
                "op": "random.draw", "stream": stream["id"], "as": "result",
                "distribution": distribution or {
                    "kind": "uniform_int", "minimum": _literal(1), "maximum": _literal(6),
                },
            },
            {
                "op": "state.set", "target": _literal("rule:state.value"),
                "value": {"op": "var", "name": "result"},
            },
        ],
        "timing": {"phase": "rule:phase.input"},
        "encoding": {"kind": "finite_catalogue"},
    }]
    document["unresolved"] = []
    return document


def _fixed_stream(seed=42, sequence=54):
    return {
        "id": "rule:random.gameplay", "name": "Gameplay",
        "algorithm": "cubeengine.pcg32/1", "seed_policy": "fixed",
        "seed": seed, "sequence": sequence,
    }


class RandomConformanceVectorTests(unittest.TestCase):
    def test_runtime_manifest_advertises_the_verified_random_contract(self):
        capabilities = json.loads(CAPABILITIES.read_text(encoding="utf-8"))
        random_service = capabilities["random_service"]

        self.assertEqual(random_service["capability_id"], "cubeengine.random-service/1.0")
        self.assertEqual(
            random_service["algorithms"],
            ["cubeengine.pcg32/1", "cubeengine.recorded/1"],
        )
        self.assertEqual(
            set(random_service["distributions"]),
            {"choice", "uniform_int", "bernoulli", "weighted_choice", "shuffle", "sample"},
        )
        self.assertEqual(random_service["conformance_vectors"], VECTORS.name)

    def test_raw_pcg32_vector_is_frozen(self):
        vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
        generator = PCG32(vectors["seed"], vectors["sequence"])

        self.assertEqual(
            [generator.next_uint32() for _ in vectors["raw_uint32"]],
            vectors["raw_uint32"],
        )

    def test_every_declared_distribution_matches_frozen_vector(self):
        vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
        declaration = _fixed_stream()
        declaration["id"] = vectors["stream_id"]
        service = DeterministicRandomService([declaration])

        results = [
            service.draw(vectors["stream_id"], item["distribution"])[0]
            for item in vectors["distribution_sequence"]
        ]

        self.assertEqual(results, [item["result"] for item in vectors["distribution_sequence"]])
        self.assertEqual(service.state_hash(), vectors["final_service_state_hash"])


class SeedPolicyTests(unittest.TestCase):
    def test_fixed_session_and_external_policies_are_explicit(self):
        fixed = DeterministicRandomService([_fixed_stream()])
        self.assertIsInstance(fixed.draw("rule:random.gameplay", {"kind": "uniform_int", "minimum": 1, "maximum": 6})[0], int)

        for policy in ("session", "external"):
            declaration = _fixed_stream()
            declaration.pop("seed")
            declaration["seed_policy"] = policy
            with self.assertRaises(RandomServiceError):
                DeterministicRandomService([declaration])
            first = DeterministicRandomService([declaration], {declaration["id"]: 99})
            second = DeterministicRandomService([declaration], {declaration["id"]: 99})
            spec = {"kind": "choice", "values": [1, 2, 3]}
            self.assertEqual(first.draw(declaration["id"], spec)[0], second.draw(declaration["id"], spec)[0])

    def test_recorded_policy_validates_results_and_exhaustion(self):
        declaration = {
            "id": "rule:random.recorded", "name": "Recorded",
            "algorithm": "cubeengine.recorded/1", "seed_policy": "recorded",
        }
        service = DeterministicRandomService([declaration], {declaration["id"]: [3, True]})

        self.assertEqual(service.draw(declaration["id"], {"kind": "uniform_int", "minimum": 1, "maximum": 6})[0], 3)
        self.assertIs(service.draw(declaration["id"], {"kind": "bernoulli", "numerator": 1, "denominator": 2})[0], True)
        with self.assertRaisesRegex(RandomServiceError, "exhausted"):
            service.draw(declaration["id"], {"kind": "choice", "values": [1]})

        invalid = DeterministicRandomService([declaration], {declaration["id"]: [9]})
        before = invalid.state_hash()
        with self.assertRaisesRegex(RandomServiceError, "outside"):
            invalid.draw(declaration["id"], {"kind": "uniform_int", "minimum": 1, "maximum": 6})
        self.assertEqual(invalid.state_hash(), before)


class RandomSnapshotTests(unittest.TestCase):
    def test_snapshot_restore_repeats_the_exact_draw_and_audit(self):
        service = DeterministicRandomService([_fixed_stream()])
        spec = {"kind": "sample", "values": [0, 1, 2, 3, 4], "count": 3}
        service.draw("rule:random.gameplay", spec)
        snapshot = service.snapshot()

        expected_result, expected_audit = service.draw("rule:random.gameplay", spec)
        service.restore(snapshot)
        actual_result, actual_audit = service.draw("rule:random.gameplay", spec)

        self.assertEqual(actual_result, expected_result)
        self.assertEqual(actual_audit.to_mapping(), expected_audit.to_mapping())

    def test_invalid_multi_stream_snapshot_is_atomic(self):
        second = _fixed_stream(seed=7, sequence=8)
        second["id"] = "rule:random.second"
        service = DeterministicRandomService([_fixed_stream(), second])
        before = service.state_hash()
        snapshot = service.snapshot()
        snapshot["streams"]["rule:random.second"]["generator"]["increment"] = 2

        with self.assertRaisesRegex(RandomServiceError, "odd"):
            service.restore(snapshot)

        self.assertEqual(service.state_hash(), before)


class RuleRuntimeRandomIntegrationTests(unittest.TestCase):
    def test_random_draw_updates_state_and_emits_auditable_chance_result(self):
        runtime = compile_rule_ir(_random_document(_fixed_stream()))

        report = runtime.apply_action(0)

        self.assertEqual(runtime.state.globals["rule:state.value"], 4)
        self.assertEqual(len(report.chance_results), 1)
        self.assertEqual(report.chance_results[0].distribution["kind"], "uniform_int")
        self.assertEqual(report.chance_results[0].result, 4)
        self.assertEqual(runtime.export_chance_audit(), report.chance_results)

    def test_failed_transaction_rolls_back_rng_and_audit(self):
        document = _random_document(_fixed_stream())
        document["actions"][0]["effects"].append({
            "op": "assert", "condition": _literal(False), "message": "rollback random",
        })
        runtime = compile_rule_ir(document)
        before = runtime.state.state_hash()

        with self.assertRaisesRegex(RuleRuntimeError, "rollback random"):
            runtime.apply_action(0)

        self.assertEqual(runtime.state.state_hash(), before)
        self.assertEqual(runtime.export_chance_audit(), ())

    def test_runtime_snapshot_restore_and_replay_are_deterministic(self):
        document = _random_document(_fixed_stream())
        runtime = compile_rule_ir(document)
        snapshot = runtime.random_snapshot()
        first = runtime.apply_action(0).chance_results[0]
        runtime.restore_random_snapshot(snapshot, expected_revision=1)
        second = runtime.apply_action(0).chance_results[0]

        self.assertEqual(first.result, second.result)
        self.assertEqual(len(runtime.export_chance_audit()), 1)
        replayed = replay_rule_ir(document, runtime.export_replay_trace())
        self.assertEqual(replayed.state.state_hash(), runtime.state.state_hash())
        self.assertEqual(
            [item.to_mapping() for item in replayed.export_chance_audit()],
            [item.to_mapping() for item in runtime.export_chance_audit()],
        )

    def test_replay_rejects_tampered_chance_result_even_when_state_hash_matches(self):
        document = _random_document(_fixed_stream())
        runtime = compile_rule_ir(document)
        runtime.apply_action(0)
        trace = [item.to_mapping() for item in runtime.export_replay_trace()]
        trace[0]["chance_results"][0]["result"] = 5

        with self.assertRaisesRegex(RuleRuntimeError, "chance results diverged"):
            replay_rule_ir(document, trace)

    def test_session_and_recorded_sources_flow_through_compile_boundary(self):
        session = _fixed_stream()
        session.pop("seed")
        session["seed_policy"] = "session"
        with self.assertRaisesRegex(RuleRuntimeError, "seed"):
            compile_rule_ir(_random_document(session))
        runtime = compile_rule_ir(_random_document(session), random_sources={session["id"]: 123})
        self.assertEqual(runtime.apply_action(0).chance_results[0].draw_index, 0)

        recorded = {
            "id": "rule:random.gameplay", "name": "Recorded",
            "algorithm": "cubeengine.recorded/1", "seed_policy": "recorded",
        }
        runtime = compile_rule_ir(_random_document(recorded), random_sources={recorded["id"]: [6]})
        self.assertEqual(runtime.apply_action(0).chance_results[0].result, 6)

    def test_validator_rejects_unversioned_algorithm_and_incomplete_distribution(self):
        document = _random_document(_fixed_stream())
        document["random_streams"][0]["algorithm"] = "python_random"
        document["actions"][0]["effects"][0]["distribution"].pop("maximum")

        codes = {item.code for item in validate_rule_ir(document)}

        self.assertIn("random.algorithm", codes)
        self.assertIn("random.distribution_field", codes)


if __name__ == "__main__":
    unittest.main()
