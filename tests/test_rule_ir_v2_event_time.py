import unittest
from copy import deepcopy

from srtp.ir_v2 import (
    IllegalActionError,
    RuleRuntimeError,
    compile_rule_ir,
    new_rule_ir,
    replay_rule_ir,
)


def _literal(value):
    return {"op": "literal", "value": value}


def _increment(state_id, amount):
    return {
        "op": "state.increment",
        "target": _literal(state_id),
        "value": _literal(amount),
    }


def _system(identifier, trigger, effects, priority=0, phase="rule:phase.update", condition=True):
    return {
        "id": identifier,
        "name": identifier,
        "phase": phase,
        "priority": priority,
        "trigger": trigger,
        "condition": _literal(condition),
        "effects": effects,
    }


def _event_document(clock="event_queue", model="event_driven", tick_hz=None):
    document = new_rule_ir("rule:game.event_time", "Event Time")
    document["topologies"] = [{
        "id": "rule:topology.board", "name": "Board", "kind": "rect_grid", "anchor": "cell",
        "axes": [{"name": "x", "extent": 1, "boundary": "bounded"}],
        "neighborhoods": [],
    }]
    document["participants"] = [{
        "id": "rule:participant.operator", "name": "Operator", "kind": "human_or_agent",
    }]
    document["state"]["variables"] = [
        {
            "id": identifier, "name": identifier, "type": "core:int", "scope": "global",
            "initial": _literal(0),
        }
        for identifier in (
            "rule:state.counter", "rule:state.changes", "rule:state.phase_enters",
            "rule:state.phase_exits", "rule:state.delayed",
        )
    ]
    document["events"] = [
        {
            "id": "rule:event.pulse", "name": "Pulse",
            "payload": [{"name": "amount", "type": "core:int"}],
        },
        {"id": "rule:event.delayed", "name": "Delayed", "payload": []},
    ]
    document["actions"] = [{
        "id": "rule:action.change_phase", "name": "Change phase",
        "actor": _literal("rule:participant.operator"), "parameters": [],
        "precondition": _literal(True),
        "effects": [{"op": "phase.set", "phase": _literal("rule:phase.update")}],
        "timing": {"phase": "rule:phase.input"},
        "encoding": {"kind": "finite_catalogue"},
    }]
    document["systems"] = [
        _system(
            "rule:system.event_pulse", {"kind": "event", "event": "rule:event.pulse"},
            [_increment("rule:state.counter", 10)], priority=1,
        ),
        _system(
            "rule:system.state_change", {"kind": "state_changed", "state": "rule:state.counter"},
            [_increment("rule:state.changes", 1)], priority=2,
        ),
        _system(
            "rule:system.phase_enter", {"kind": "phase_enter", "phase": "rule:phase.update"},
            [_increment("rule:state.phase_enters", 1)], priority=3,
        ),
        _system(
            "rule:system.phase_exit", {"kind": "phase_exit", "phase": "rule:phase.input"},
            [_increment("rule:state.phase_exits", 1)], priority=4,
        ),
        _system(
            "rule:system.manual_boost", {"kind": "manual", "name": "boost"},
            [_increment("rule:state.counter", 5)], priority=5,
        ),
        _system(
            "rule:system.every_second_tick", {"kind": "tick", "every": 2, "offset": 0},
            [_increment("rule:state.counter", 1)], priority=6,
        ),
        _system(
            "rule:system.delayed", {"kind": "event", "event": "rule:event.delayed"},
            [_increment("rule:state.delayed", 1)], priority=7,
        ),
    ]
    document["flow"] = {
        "model": model,
        "phases": [
            {"id": "rule:phase.input", "order": 100},
            {"id": "rule:phase.update", "order": 200},
        ],
        "initial_phase": "rule:phase.input",
        "scheduler": {
            "clock": clock, "tick_hz": tick_hz,
            "ordering": "phase_priority_id", "max_catch_up_ticks": 8,
        },
    }
    document["unresolved"] = []
    return document


def _simultaneous_document():
    document = _event_document()
    document["participants"] = [
        {"id": "rule:participant.one", "name": "One", "kind": "human_or_agent"},
        {"id": "rule:participant.two", "name": "Two", "kind": "human_or_agent"},
    ]
    document["systems"] = []
    document["events"] = []
    document["state"]["variables"] = [{
        "id": "rule:state.counter", "name": "Counter", "type": "core:int",
        "scope": "global", "initial": _literal(0),
    }]
    document["actions"] = [
        {
            "id": "rule:action.one", "name": "One", "actor": _literal("rule:participant.one"),
            "parameters": [], "precondition": _literal(True),
            "effects": [_increment("rule:state.counter", 1)],
            "timing": {"phase": "rule:phase.input"}, "encoding": {"kind": "finite_catalogue"},
        },
        {
            "id": "rule:action.two", "name": "Two", "actor": _literal("rule:participant.two"),
            "parameters": [], "precondition": _literal(True),
            "effects": [_increment("rule:state.counter", 10)],
            "timing": {"phase": "rule:phase.input"}, "encoding": {"kind": "finite_catalogue"},
        },
    ]
    document["flow"]["model"] = "simultaneous"
    document["flow"]["turn_order"] = ["rule:participant.one", "rule:participant.two"]
    document["flow"]["scheduler"]["simultaneous_resolution"] = "action_code"
    return document


class EventTriggerRuntimeTests(unittest.TestCase):
    def test_event_state_change_manual_and_phase_triggers_are_transactional(self):
        runtime = compile_rule_ir(_event_document())

        event_report = runtime.dispatch_event("rule:event.pulse", {"amount": 3})
        self.assertEqual(runtime.state.globals["rule:state.counter"], 10)
        self.assertEqual(runtime.state.globals["rule:state.changes"], 1)
        self.assertEqual(event_report.revision, 1)
        self.assertEqual(
            [item.system_id for item in event_report.scheduler_trace if item.executed],
            ["rule:system.event_pulse", "rule:system.state_change"],
        )

        runtime.trigger_manual("boost")
        self.assertEqual(runtime.state.globals["rule:state.counter"], 15)
        self.assertEqual(runtime.state.globals["rule:state.changes"], 2)

        phase_report = runtime.apply_action(0)
        self.assertEqual(runtime.state.phase, "rule:phase.update")
        self.assertEqual(runtime.state.globals["rule:state.phase_exits"], 1)
        self.assertEqual(runtime.state.globals["rule:state.phase_enters"], 1)
        self.assertIn("rule:system.phase_exit", [item.system_id for item in phase_report.scheduler_trace])
        self.assertIn("rule:system.phase_enter", [item.system_id for item in phase_report.scheduler_trace])
        self.assertFalse(runtime.is_legal(0))

    def test_declared_payload_is_type_checked(self):
        runtime = compile_rule_ir(_event_document())
        before = runtime.state.state_hash()

        with self.assertRaises(RuleRuntimeError):
            runtime.dispatch_event("rule:event.pulse", {})
        with self.assertRaises(RuleRuntimeError):
            runtime.dispatch_event("rule:event.pulse", {"amount": "three"})
        with self.assertRaises(RuleRuntimeError):
            runtime.dispatch_event("rule:event.unknown", {})

        self.assertEqual(runtime.state.state_hash(), before)

    def test_failed_event_cascade_rolls_back_and_leaves_no_replay_entry(self):
        document = _event_document()
        document["systems"].append(_system(
            "rule:system.fail_event", {"kind": "event", "event": "rule:event.pulse"},
            [
                _increment("rule:state.counter", 99),
                {"op": "assert", "condition": _literal(False), "message": "event failure"},
            ],
            priority=99,
        ))
        runtime = compile_rule_ir(document)
        before = runtime.state.state_hash()

        with self.assertRaisesRegex(RuleRuntimeError, "event failure"):
            runtime.dispatch_event("rule:event.pulse", {"amount": 1})

        self.assertEqual(runtime.state.state_hash(), before)
        self.assertEqual(runtime.state.revision, 0)
        self.assertEqual(runtime.export_replay_trace(), ())

    def test_named_delayed_event_can_be_cancelled_before_due_tick(self):
        document = _event_document()
        document["actions"][0]["effects"] = [
            {
                "op": "event.schedule", "event": "rule:event.delayed",
                "schedule_id": _literal("alarm"), "delay_ticks": _literal(1),
                "payload": _literal({}),
            },
            {"op": "event.cancel", "schedule_id": _literal("alarm")},
        ]
        runtime = compile_rule_ir(document)

        runtime.apply_action(0)
        self.assertEqual(runtime.state.scheduled_events, [])
        runtime.advance_tick()
        self.assertEqual(runtime.state.globals["rule:state.delayed"], 0)

    def test_zero_delay_event_fires_in_the_same_transaction(self):
        document = _event_document()
        document["actions"][0]["effects"] = [{
            "op": "event.schedule", "event": "rule:event.delayed",
            "schedule_id": _literal("now"), "delay_ticks": _literal(0),
            "payload": _literal({}),
        }]
        runtime = compile_rule_ir(document)

        report = runtime.apply_action(0)

        self.assertEqual(runtime.state.globals["rule:state.delayed"], 1)
        self.assertIn("rule:event.delayed", [item["event"] for item in report.emitted_events])

    def test_scheduler_order_is_phase_then_priority_then_stable_id(self):
        document = _event_document()
        document["systems"] = [
            _system("rule:system.update_b", {"kind": "manual", "name": "ordered"}, [], priority=2),
            _system("rule:system.input_z", {"kind": "manual", "name": "ordered"}, [], priority=9, phase="rule:phase.input"),
            _system("rule:system.update_a", {"kind": "manual", "name": "ordered"}, [], priority=2),
            _system("rule:system.update_first", {"kind": "manual", "name": "ordered"}, [], priority=1),
        ]
        runtime = compile_rule_ir(document)

        report = runtime.trigger_manual("ordered")

        self.assertEqual(
            [item.system_id for item in report.scheduler_trace],
            [
                "rule:system.input_z", "rule:system.update_first",
                "rule:system.update_a", "rule:system.update_b",
            ],
        )


class TimeControllerTests(unittest.TestCase):
    def test_integer_wall_clock_pause_resume_and_step(self):
        runtime = compile_rule_ir(_event_document(clock="real_time", model="real_time", tick_hz=20))

        first = runtime.advance_time_ns(125_000_000)
        self.assertEqual(first.consumed_ticks, 2)
        self.assertEqual(first.remainder_units, 500_000_000)
        self.assertEqual(runtime.state.tick, 2)
        self.assertEqual(runtime.state.globals["rule:state.counter"], 1)

        runtime.pause()
        paused_hash = runtime.state.state_hash()
        paused = runtime.advance_time_ns(1_000_000_000)
        self.assertEqual(paused.consumed_ticks, 0)
        self.assertEqual(runtime.state.state_hash(), paused_hash)

        runtime.step()
        self.assertEqual(runtime.state.tick, 3)
        runtime.resume()
        runtime.advance_time_ns(25_000_000)
        self.assertEqual(runtime.state.tick, 4)
        self.assertEqual(runtime.state.globals["rule:state.counter"], 2)

    def test_catch_up_limit_keeps_backlog_without_dropping_time(self):
        document = _event_document(clock="real_time", model="real_time", tick_hz=10)
        document["flow"]["scheduler"]["max_catch_up_ticks"] = 2
        runtime = compile_rule_ir(document)

        first = runtime.advance_time_ns(1_000_000_000)
        self.assertEqual(first.consumed_ticks, 2)
        self.assertEqual(first.remainder_units, 8_000_000_000)
        second = runtime.advance_time_ns(0)
        self.assertEqual(second.consumed_ticks, 2)
        self.assertEqual(second.remainder_units, 6_000_000_000)

    def test_wall_clock_controller_trace_restores_pause_and_fractional_time(self):
        document = _event_document(clock="real_time", model="real_time", tick_hz=20)
        original = compile_rule_ir(document)
        original.advance_time_ns(125_000_000)
        original.pause()
        original.advance_time_ns(1_000_000_000)
        original.resume()
        original.advance_time_ns(25_000_000)

        replayed = replay_rule_ir(document, original.export_replay_trace())

        self.assertEqual(replayed.state.state_hash(), original.state.state_hash())
        self.assertEqual(replayed.time_remainder_units, original.time_remainder_units)
        self.assertEqual(replayed.paused, original.paused)

    def test_failed_wall_clock_batch_restores_state_and_fractional_time(self):
        document = _event_document(clock="real_time", model="real_time", tick_hz=20)
        document["systems"].append(_system(
            "rule:system.fail_tick", {"kind": "tick"},
            [{"op": "assert", "condition": _literal(False), "message": "stop"}],
            priority=99,
        ))
        runtime = compile_rule_ir(document)
        before_hash = runtime.state.state_hash()

        with self.assertRaisesRegex(RuleRuntimeError, "stop"):
            runtime.advance_time_ns(50_000_000)

        self.assertEqual(runtime.state.state_hash(), before_hash)
        self.assertEqual(runtime.time_remainder_units, 0)
        self.assertEqual(runtime.export_replay_trace(), ())


class SimultaneousAndReplayTests(unittest.TestCase):
    def test_joint_action_is_atomic_complete_and_arrival_order_independent(self):
        document = _simultaneous_document()
        first = compile_rule_ir(document)
        second = compile_rule_ir(document)

        first_report = first.apply_joint_actions([1, 0])
        second_report = second.apply_joint_actions([0, 1])

        self.assertEqual(first.state.globals["rule:state.counter"], 11)
        self.assertEqual(first_report.state_hash, second_report.state_hash)
        self.assertEqual(first.state.revision, 1)
        self.assertEqual(first_report.emitted_events[-1]["event"], "rule:event.joint_actions_applied")
        with self.assertRaises(IllegalActionError):
            first.apply_action(0)
        with self.assertRaises(IllegalActionError):
            compile_rule_ir(document).apply_joint_actions([0])

    def test_replay_reconstructs_state_and_rejects_tampering(self):
        document = _simultaneous_document()
        original = compile_rule_ir(document)
        original.apply_joint_actions([1, 0])
        original.advance_tick()
        trace = original.export_replay_trace()

        replayed = replay_rule_ir(document, trace)
        self.assertEqual(replayed.state.state_hash(), original.state.state_hash())
        self.assertEqual(len(replayed.export_replay_trace()), len(trace))

        tampered = [item.to_mapping() for item in trace]
        tampered[-1]["state_hash"] = "0" * 64
        with self.assertRaisesRegex(RuleRuntimeError, "diverged after"):
            replay_rule_ir(document, tampered)

        trace_tampered = [item.to_mapping() for item in trace]
        trace_tampered[-1]["scheduler_trace"] = [{"invented": True}]
        with self.assertRaisesRegex(RuleRuntimeError, "scheduler trace diverged"):
            replay_rule_ir(document, trace_tampered)


if __name__ == "__main__":
    unittest.main()
