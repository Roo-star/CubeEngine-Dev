"""Replay saved model replies through the current pipeline, without any model call.

Every agentic run keeps its parsed replies in ``agent_trace.json``. Replay
feeds them back, per role and in their original order, to today's compiler:
the gates, builders, repairs and engine templates are current, the model
output is the paid one. When the pipeline asks a role for more replies than
were saved, the job stops there as ``replay_exhausted`` (a new call would be
needed). This measures how far the current code carries the same model
output; it is not evidence that a new generation would succeed.

Usage:
    python -m srtp.llm_compiler_v1.replay RUN_DIR|STAGED_REPORT.json [...] --source GAME.py
        [--source-bundle DIR] [--json OUT]

A staged run directory with ``requests.jsonl`` is replayed with every recorded
reply (accepted ones included); with ``--source-bundle`` it is replayed as a Lift.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Deque, Dict, Iterator, List, Mapping, Optional
from unittest import mock

from .agentic import LLMBudgetExceeded
from .client import LLMChatResult


class ReplayExhausted(LLMBudgetExceeded):
    """The pipeline asked for a reply that was never paid for."""


def saved_replies(run_dir: Path) -> Dict[str, Deque[Mapping[str, Any]]]:
    trace = json.loads((Path(run_dir) / "agent_trace.json").read_text(encoding="utf-8"))
    queues: Dict[str, Deque[Mapping[str, Any]]] = defaultdict(deque)
    for step in trace.get("steps") or []:
        reply = step.get("reply")
        if step.get("kind") == "llm_reply" and isinstance(reply, Mapping) and not reply.get("truncated"):
            queues[str(step.get("role"))].append(reply)
    return queues


@contextmanager
def replaying(queues: Dict[str, Deque[Mapping[str, Any]]], log: List[Dict[str, Any]]) -> Iterator[None]:
    from .agentic import _Job

    def chat(self, role, messages, *, repair=False):
        queue = queues.get(role)
        if not queue:
            log.append({"role": role, "served": False})
            raise ReplayExhausted("replay exhausted: no saved {0} reply left".format(role))
        self.trace.llm_calls += 1
        reply = queue.popleft()
        log.append({"role": role, "served": True})
        self.trace.record(role, "llm_reply", call=self.trace.llm_calls, replayed=True, reply=reply)
        return LLMChatResult(content=json.dumps(reply), provider="replay", model="replay", parsed=reply)

    with mock.patch.object(_Job, "_chat", chat):
        yield


def replay_run(run_dir: Path, *, source: Path, source_bundle: Optional[Path] = None,
               out_dir: Optional[Path] = None) -> Dict[str, Any]:
    from .agentic import AgenticSourceToIRCompiler
    run_dir = Path(run_dir)
    queues = saved_replies(run_dir)
    total = sum(len(queue) for queue in queues.values())
    log: List[Dict[str, Any]] = []
    compiler = AgenticSourceToIRCompiler(chat_fn=lambda *a, **k: None)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(out_dir) if out_dir else Path(tmp) / "out"
        with replaying(queues, log):
            intent = run_dir / "design_intent.json"
            saved_intent = next(iter(queues.get("intent") or []), None)
            text = (json.loads(intent.read_text(encoding="utf-8")) if intent.is_file() else saved_intent or {}).get(
                "original_text")
            if text:
                bundle = Path(tmp) / "source_bundle"
                shutil.copytree(source_bundle, bundle)
                from .approval import approve_llm_manifest_file
                approve_llm_manifest_file(bundle / "project.manifest.json")
                report = compiler.compile_lift_path(source, source_bundle_dir=bundle, intent_text=text,
                                                    out_dir=out, resume=False)
            else:
                report = compiler.compile_path(source, out_dir=out, resume=False)
        oracle = getattr(compiler.last_job, "oracle", None)
        rejected = [dict(role=step.get("role"), origin=step.get("origin"), attempt=step.get("attempt"),
                         diagnostics=[str(d)[:300] for d in step.get("diagnostics") or []][:8])
                    for step in (compiler.last_job.trace.steps if compiler.last_job else [])
                    if step.get("kind") == "validate" and not step.get("ok")]
    return {"run": str(run_dir), "ok": report.ok, "stage": report.stage,
            "saved_replies": total, "used": sum(1 for row in log if row["served"]),
            "exhausted": next((row["role"] for row in log if not row["served"]), None),
            "calls_by_role": {role: sum(1 for row in log if row["role"] == role and row["served"])
                              for role in sorted({row["role"] for row in log})},
            "oracle": (oracle or {}).get("status"),
            "rejected": rejected,
            "diagnostics": [str(item)[:300] for item in report.diagnostics][:12]}


def saved_stage_replies(report_path: Path) -> Dict[str, Deque[Any]]:
    """Staged runs: the replies each stage attempt recorded (rejected definitions, raw invalid text)."""
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    queues: Dict[str, Deque[Any]] = defaultdict(deque)
    for step in (report.get("compilation_trace") or {}).get("stages") or []:
        if step.get("cached"):
            continue
        evidence = step.get("response_evidence") or {}
        if isinstance(step.get("rejected_definition"), Mapping) and "invalid_response" not in step["rejected_definition"]:
            queues[str(step.get("stage"))].append(step["rejected_definition"])
        elif isinstance(evidence.get("content"), str):
            queues[str(step.get("stage"))].append(evidence["content"])
    return queues


def replay_staged(report_path: Path, *, source: Path, max_repairs: int = 3) -> Dict[str, Any]:
    """Feed a staged run's recorded replies to today's staged compiler, stage by stage."""
    from srtp.source_importer import SourceGameImporter
    from .client import LLMTransportError
    from .compiler import SourceToIRCompiler
    queues = saved_stage_replies(report_path)
    total = sum(len(queue) for queue in queues.values())
    log: List[Dict[str, Any]] = []

    def chat(messages, **kwargs):
        stage = json.loads(messages[-1]["content"]).get("stage")
        queue = queues.get(stage)
        if not queue:
            log.append({"role": stage, "served": False})
            raise LLMTransportError("replay exhausted: no saved {0} reply left".format(stage))
        log.append({"role": stage, "served": True})
        reply = queue.popleft()
        return reply if isinstance(reply, str) else json.dumps(reply)

    package = SourceGameImporter().import_path(Path(source))
    compiler = SourceToIRCompiler(chat_fn=chat, max_repairs=max_repairs)
    with tempfile.TemporaryDirectory() as tmp:
        report = compiler.compile(package, out_dir=Path(tmp) / "out")
    stages = (report.compilation_trace or {}).get("stages") or []
    return {"run": str(report_path), "ok": report.ok, "stage": report.stage, "saved_replies": total,
            "used": sum(1 for row in log if row["served"]),
            "exhausted": next((row["role"] for row in log if not row["served"]), None),
            "rejected": [dict(role=step.get("stage"), origin="staged", attempt=step.get("attempt"),
                              diagnostics=[str(d)[:300] for d in step.get("diagnostics") or []][:8])
                         for step in stages if not step.get("passed")],
            "passed_stages": [step.get("stage") for step in stages if step.get("passed")],
            "diagnostics": [str(item)[:300] for item in report.diagnostics][:12]}


def recorded_replies(requests_jsonl: Path) -> List[Dict[str, Any]]:
    """Every reply a staged run received, in order: [{"stage": str|None, "response_text": str}].

    Unlike ``saved_stage_replies`` this includes accepted replies and the
    follow-up replies sent after ``source_results`` (recorded with no stage).
    """
    replies = []
    for line in Path(requests_jsonl).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        text = record.get("response_text")
        if record.get("status") == "ok" and isinstance(text, str) and text != "None":
            replies.append({"stage": record.get("stage"), "response_text": text})
    return replies


class RecordedChat:
    """Staged ``chat_fn`` answering from recorded replies, grouped by stage.

    A request for stage S gets the next recorded reply for S; a follow-up
    without a stage gets the follow-ups recorded after that reply. Replies a
    fixed engine no longer asks for are skipped rather than handed to another
    stage. A request with no recorded reply raises ``LLMTransportError`` and is
    logged in ``exhausted``: a live run would pay for it there.
    """

    def __init__(self, replies: List[Mapping[str, Any]]) -> None:
        self.groups: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)
        current: Optional[Dict[str, Any]] = None
        for reply in replies:
            if reply.get("stage"):
                current = {"text": reply["response_text"], "follow_ups": deque()}
                self.groups[str(reply["stage"])].append(current)
            elif current is not None:
                current["follow_ups"].append(reply["response_text"])
        self.total = len(replies)
        self.follow_ups: Deque[str] = deque()
        self.served: List[Optional[str]] = []
        self.exhausted: List[str] = []

    def __call__(self, messages, **kwargs):
        from .client import LLMTransportError
        try:
            content = json.loads(list(messages)[-1]["content"])
        except (LookupError, TypeError, ValueError):
            content = None
        stage = content.get("stage") if isinstance(content, dict) else None
        if stage is None:
            if not self.follow_ups:
                self.exhausted.append("follow-up")
                raise LLMTransportError("replay exhausted: no recorded follow-up reply")
            self.served.append(None)
            return self.follow_ups.popleft()
        if not self.groups.get(stage):
            self.exhausted.append(stage)
            raise LLMTransportError("replay exhausted: no recorded {0} reply left".format(stage))
        group = self.groups[stage].popleft()
        self.follow_ups = deque(group["follow_ups"])
        self.served.append(stage)
        return group["text"]


def replay_recorded(replies: List[Mapping[str, Any]], *, source: Path, source_bundle: Optional[Path] = None,
                    target_dimensions: Optional[Mapping[str, int]] = None, intent_text: str = "",
                    max_repairs: int = 3, checkpoint: Optional[Path] = None) -> Dict[str, Any]:
    """Run today's staged Source compile (or Lift, with ``source_bundle``) on recorded replies."""
    from srtp.source_importer import SourceGameImporter
    from .compiler import SourceToIRCompiler
    chat = RecordedChat(list(replies))
    package = SourceGameImporter().import_path(Path(source))
    compiler = SourceToIRCompiler(chat_fn=chat, max_repairs=max_repairs)
    with tempfile.TemporaryDirectory() as tmp:
        if source_bundle is not None:
            report = compiler.compile_spatial_lift(
                package, source_bundle_dir=Path(source_bundle), intent_text=intent_text,
                target_dimensions=dict(target_dimensions or package.transformation.target_dimensions),
                out_dir=Path(tmp) / "out", checkpoint_path=checkpoint)
        else:
            report = compiler.compile(package, out_dir=Path(tmp) / "out", checkpoint_path=checkpoint)
    trace = report.compilation_trace or {}
    stages = trace.get("stages") or []
    return {"ok": report.ok, "stage": report.stage, "saved_replies": chat.total,
            "used": len(chat.served), "served": list(chat.served),
            "exhausted": chat.exhausted[0] if chat.exhausted else None,
            "engine_fault": trace.get("engine_fault"),
            "passed_stages": [step.get("stage") for step in stages if step.get("passed")],
            "rejected": [dict(role=step.get("stage"), attempt=step.get("attempt"),
                              diagnostics=[str(d)[:2000] for d in step.get("diagnostics") or []][:8])
                         for step in stages if not step.get("passed")],
            "diagnostics": [str(item)[:2000] for item in report.diagnostics][:12]}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-bundle", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    rows = []
    for run in args.runs:
        try:
            if run.is_dir() and (run / "requests.jsonl").is_file() and not (run / "agent_trace.json").is_file():
                row = dict(replay_recorded(recorded_replies(run / "requests.jsonl"), source=args.source,
                                           source_bundle=args.source_bundle), run=str(run))
            elif run.is_file():
                row = replay_staged(run, source=args.source)
            else:
                row = replay_run(run, source=args.source, source_bundle=args.source_bundle)
        except Exception as error:  # noqa: BLE001 - report and continue with the next run
            row = {"run": str(run), "ok": False, "stage": "replay_error",
                   "diagnostics": ["{0}: {1}".format(type(error).__name__, error)]}
        rows.append(row)
        print("{0:<6} {1:<22} used {2}/{3} exhausted={4} {5}".format(
            "OK" if row["ok"] else "FAIL", row["stage"], row.get("used", "-"), row.get("saved_replies", "-"),
            row.get("exhausted"), row["run"]))
        for item in row["diagnostics"][:6]:
            print("        " + item)
        for step in row.get("rejected") or []:
            print("    rejected {0} ({1} #{2}):".format(step["role"], step["origin"], step["attempt"]))
            for item in step["diagnostics"][:5]:
                print("        " + item)
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
