"""Nonblocking process supervision used by Workbench and acceptance tools."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .artifacts import atomic_json, binding, check_checkpoint_binding, utc_now, read_json

ROOT = Path(__file__).resolve().parents[2]


def default_python():
    local = ROOT / ".venv-training" / "Scripts" / "python.exe"
    return str(local if local.is_file() else sys.executable)


class TrainingSession:
    def __init__(self):
        self.process = None
        self.root = None
        self.offset = 0
        self.tail = b""
        self.reported = False
        self.log = None
        self.cancelled_at = None
        self.last_heartbeat = 0

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self, snapshot, config, output, python=None, resume=None):
        if self.running:
            raise ValueError("A training run is already active.")
        config.validate()
        # Validate a resume before making a new run. Worker revalidates before loading.
        if resume:
            check_checkpoint_binding(resume, binding(snapshot, config.channels))
        output = Path(output).expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        self.root = output / ("run-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        self.root.mkdir(exist_ok=False)
        request = {"target_path": snapshot["target_path"], "project_hash": snapshot["project"]["content_hash"],
                   "rule_hash": snapshot["rule"]["content_hash"], "config": config.to_mapping(),
                   "resume": str(Path(resume).resolve()) if resume else None, "supervised": True}
        atomic_json(self.root / "request.json", request)
        (self.root / "heartbeat").touch()
        self.offset, self.tail, self.reported, self.cancelled_at = 0, b"", False, None
        self.log = (self.root / "worker.log").open("wb")
        try:
            self.process = subprocess.Popen([str(python or default_python()), "-u", "-m", "srtp.training.worker",
                                            "--request", str(self.root / "request.json")], cwd=str(ROOT),
                                            stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            self.log.close()
            self.log = None
            raise
        return self.root

    def cancel(self):
        if self.running:
            (self.root / "cancel.request").touch()
            self.cancelled_at = self.cancelled_at or time.monotonic()
            return True
        return False

    def poll(self):
        if self.root is None or self.reported:
            return []
        if self.running and time.monotonic() - self.last_heartbeat >= 1:
            (self.root / "heartbeat").touch()
            self.last_heartbeat = time.monotonic()
        # Hung native code gets a bounded shutdown; published checkpoints are
        # immutable and a partial iteration can never be selected as latest.
        if self.running and self.cancelled_at and time.monotonic() - self.cancelled_at > 15:
            self.process.terminate()
        events = []
        path = self.root / "events.jsonl"
        if path.exists():
            with path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read(262144)
                self.offset = stream.tell()
            pieces = (self.tail + chunk).split(b"\n")
            self.tail = pieces.pop()
            for line in pieces:
                if line:
                    events.append(json.loads(line.decode("utf-8")))
        if not self.running:
            # Drain any remainder on subsequent frames before emitting exit.
            if path.exists() and self.offset < path.stat().st_size:
                return events
            self.reported = True
            if self.log:
                self.log.close()
                self.log = None
            report_path = self.root / "report.json"
            if not report_path.exists():
                report = {"status": "interrupted" if self.cancelled_at else "failed", "error":
                          "Worker ended before final report (exit {0}). Last complete checkpoint remains safe; see worker.log.".format(self.process.returncode),
                          "finished_at": utc_now()}
                if (self.root / "run.json").exists():
                    report["binding"] = read_json(self.root / "run.json").get("binding")
                if (self.root / "latest.json").exists():
                    report["checkpoint"] = read_json(self.root / "latest.json")["checkpoint"]
                atomic_json(report_path, report)
                events.append({"phase": report["status"], **report})
            events.append({"phase": "process_exit", "returncode": self.process.returncode, "report": str(report_path)})
        return events

    def close(self):
        self.cancel()
        # Do not block the render thread during app shutdown. A daemon monitor
        # waits for cooperative cleanup then stops a stuck child.
        if self.running:
            from threading import Thread
            process, log = self.process, self.log
            def reap():
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait()
                finally:
                    if log:
                        log.close()
            Thread(target=reap, name="training shutdown", daemon=True).start()
        elif self.log:
            self.log.close()
            self.log = None
