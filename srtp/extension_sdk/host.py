"""Bounded out-of-process host for reviewed CubeEngine Extensions."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .contracts import ContractValidationError, validate_json_contract
from .manifest import EXTENSION_RPC_VERSION, VerifiedExtensionPackage
from .windows_job import WindowsJob, WindowsJobError


class ExtensionHostError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class InvocationRecord:
    sequence: int
    extension_id: str
    extension_version: str
    extension_hash: str
    capability_id: str
    request_hash: str
    response_hash: str

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "sequence": self.sequence,
            "extension_id": self.extension_id,
            "extension_version": self.extension_version,
            "extension_hash": self.extension_hash,
            "capability_id": self.capability_id,
            "request_hash": self.request_hash,
            "response_hash": self.response_hash,
        }


class ExtensionHost:
    """One reviewed package, one worker, one serialized RPC stream."""

    def __init__(
        self, package: VerifiedExtensionPackage, *,
        python_executable: Optional[Path] = None,
        approved_hashes: Sequence[str] = (),
    ) -> None:
        self.package = package
        self.python_executable = Path(python_executable or sys.executable).resolve()
        self.approved_hashes = frozenset(str(item) for item in approved_hashes)
        self.state = "created"
        self._process: Optional[subprocess.Popen] = None
        self._job: Optional[WindowsJob] = None
        self._temporary: Optional[tempfile.TemporaryDirectory] = None
        self._stdout_queue: "queue.Queue[Any]" = queue.Queue()
        self._stderr_chunks = []
        self._request_id = 0
        self._invocation_sequence = 0
        self._records = []
        self._capabilities = {
            str(item["id"]): deepcopy(item)
            for item in package.manifest["capabilities"]
        }

    @property
    def invocation_records(self) -> Tuple[InvocationRecord, ...]:
        return tuple(self._records)

    @property
    def stderr(self) -> str:
        return b"".join(self._stderr_chunks).decode("utf-8", errors="replace")[:8192]

    def start(self) -> "ExtensionHost":
        if self.state != "created":
            raise ExtensionHostError("lifecycle", "extension host can only start once")
        metadata = self.package.manifest["metadata"]
        runtime = self.package.manifest["runtime"]
        trust = metadata["trust"]
        if trust == "untrusted_generated" or runtime["isolation"] == "os_sandbox_required":
            raise ExtensionHostError(
                "os_sandbox_required",
                "untrusted/generated extensions require an external OS sandbox provider",
            )
        if trust == "designer_reviewed" and self.package.content_hash not in self.approved_hashes:
            raise ExtensionHostError(
                "approval_required",
                "designer-reviewed extension hash has not been explicitly approved",
            )
        permissions = self.package.manifest["permissions"]
        if (
            permissions["network"] or permissions["subprocess"] or permissions["native_code"]
            or permissions["filesystem_write"] or permissions["environment"]
        ):
            raise ExtensionHostError(
                "permission_unsupported",
                "local cooperative host refuses network, subprocess, native, write and environment permissions",
            )
        if os.name != "nt":
            raise ExtensionHostError(
                "resource_provider_unavailable",
                "this SDK build currently requires Windows Job Objects for local execution",
            )
        self._temporary = tempfile.TemporaryDirectory(prefix="cubeengine-extension-")
        worker = Path(__file__).with_name("worker.py").resolve()
        command = [
            str(self.python_executable), "-I", "-S", "-X", "utf8", "-u", str(worker),
            "--root", str(self.package.root),
            "--manifest", str(self.package.manifest_path),
            "--hash", self.package.content_hash,
        ]
        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ
        }
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._temporary.name,
                env=environment,
                shell=False,
                creationflags=creationflags,
            )
            try:
                self._job = WindowsJob(
                    self._process,
                    memory_mb=int(runtime["max_memory_mb"]),
                    cpu_ms=int(runtime["max_cpu_ms"]),
                    max_processes=int(runtime["max_processes"]),
                )
            except WindowsJobError as exc:
                self._terminate()
                raise ExtensionHostError("resource_limit_failed", str(exc)) from exc
            threading.Thread(target=self._stdout_reader, daemon=True).start()
            threading.Thread(target=self._stderr_reader, daemon=True).start()
            hello = self._read_response(max(1000, int(runtime["timeout_ms"])), expected_id=0)
            result = hello["result"]
            expected = {
                "event": "hello",
                "extension_id": self.package.extension_id,
                "version": self.package.version,
                "content_hash": self.package.content_hash,
                "capabilities": sorted(self._capabilities),
            }
            if result != expected:
                raise ExtensionHostError("handshake_mismatch", "extension worker handshake did not match its verified manifest")
        except Exception:
            self._terminate()
            raise
        self.state = "started"
        return self

    def initialize(self, context: Optional[Mapping[str, Any]] = None) -> Any:
        if self.state != "started":
            raise ExtensionHostError("lifecycle", "extension must be started before initialization")
        result = self._rpc("initialize", {"context": deepcopy(dict(context or {}))})
        self.state = "initialized"
        return result

    def invoke(self, capability_id: str, request: Any) -> Any:
        if self.state != "initialized":
            raise ExtensionHostError("lifecycle", "extension must be initialized before invocation")
        capability = self._capabilities.get(capability_id)
        if capability is None:
            raise ExtensionHostError("capability_unknown", "extension does not provide capability: {0}".format(capability_id))
        try:
            validate_json_contract(request, capability["request_schema"], "$request")
        except ContractValidationError as exc:
            raise ExtensionHostError("request_contract", str(exc)) from exc
        before = self.snapshot() if capability["side_effects"] == "none" else None
        result = self._rpc("invoke", {"capability_id": capability_id, "request": deepcopy(request)})
        try:
            validate_json_contract(result, capability["response_schema"], "$response")
        except ContractValidationError as exc:
            self._terminate()
            raise ExtensionHostError("response_contract", str(exc)) from exc
        if capability["side_effects"] == "none":
            after = self.snapshot()
            if _json_hash(before) != _json_hash(after):
                self._terminate()
                raise ExtensionHostError(
                    "purity_violation",
                    "side-effect-free extension capability changed its adapter snapshot",
                )
        self._invocation_sequence += 1
        self._records.append(InvocationRecord(
            sequence=self._invocation_sequence,
            extension_id=self.package.extension_id,
            extension_version=self.package.version,
            extension_hash=self.package.content_hash,
            capability_id=capability_id,
            request_hash=_json_hash(request),
            response_hash=_json_hash(result),
        ))
        return result

    def snapshot(self) -> Any:
        if self.state != "initialized":
            raise ExtensionHostError("lifecycle", "snapshot requires initialized extension")
        return self._rpc("snapshot", {})

    def restore(self, snapshot: Any) -> Any:
        if self.state != "initialized":
            raise ExtensionHostError("lifecycle", "restore requires initialized extension")
        return self._rpc("restore", {"snapshot": deepcopy(snapshot)})

    def close(self) -> None:
        if self.state == "initialized":
            try:
                self._rpc("shutdown", {})
            except ExtensionHostError:
                pass
        self._terminate()
        self.state = "closed"

    def __enter__(self) -> "ExtensionHost":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _rpc(self, operation: str, payload: Mapping[str, Any]) -> Any:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise ExtensionHostError("worker_unavailable", "extension worker is not running")
        self._request_id += 1
        request = {
            "protocol": EXTENSION_RPC_VERSION,
            "id": self._request_id,
            "op": operation,
            "payload": deepcopy(dict(payload)),
        }
        try:
            encoded = json.dumps(
                request, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8") + b"\n"
        except (TypeError, ValueError) as exc:
            raise ExtensionHostError("request_json", "extension request is not finite JSON") from exc
        maximum = int(self.package.manifest["runtime"]["max_request_bytes"])
        if len(encoded) > maximum:
            raise ExtensionHostError("request_too_large", "extension request exceeds declared byte limit")
        try:
            process.stdin.write(encoded)
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._terminate()
            raise ExtensionHostError("worker_unavailable", "extension worker pipe closed") from exc
        response = self._read_response(
            int(self.package.manifest["runtime"]["timeout_ms"]),
            expected_id=self._request_id,
        )
        return deepcopy(response["result"])

    def _read_response(self, timeout_ms: int, expected_id: int) -> Mapping[str, Any]:
        try:
            item = self._stdout_queue.get(timeout=timeout_ms / 1000.0)
        except queue.Empty as exc:
            self._terminate()
            raise ExtensionHostError("timeout", "extension request exceeded wall-clock timeout") from exc
        if isinstance(item, Exception):
            self._terminate()
            raise ExtensionHostError("protocol_io", str(item))
        if item is None:
            code = self._process.poll() if self._process is not None else None
            self._terminate()
            raise ExtensionHostError(
                "worker_exited",
                "extension worker exited before responding (exit={0}, stderr={1})".format(code, self.stderr),
            )
        try:
            response = json.loads(
                item.decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._terminate()
            raise ExtensionHostError("protocol_json", "extension response is not valid finite JSON") from exc
        if not isinstance(response, Mapping) or response.get("protocol") != EXTENSION_RPC_VERSION or response.get("id") != expected_id:
            self._terminate()
            raise ExtensionHostError("protocol_response", "extension response protocol or request ID mismatch")
        if response.get("ok") is not True:
            if set(response) != {"protocol", "id", "ok", "error", "diagnostics"}:
                self._terminate()
                raise ExtensionHostError("protocol_response", "extension error response fields are invalid")
            error = response.get("error", {})
            if not isinstance(error, Mapping) or set(error) != {"code", "message"}:
                self._terminate()
                raise ExtensionHostError("protocol_response", "extension error payload fields are invalid")
            code = str(error.get("code", "adapter_error"))
            message = str(error.get("message", "extension request failed"))
            raise ExtensionHostError(code, message)
        if set(response) != {"protocol", "id", "ok", "result", "diagnostics"}:
            self._terminate()
            raise ExtensionHostError("protocol_response", "extension success response fields are invalid")
        _json_hash(response["result"])
        return response

    def _stdout_reader(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._stdout_queue.put(None)
            return
        maximum = int(self.package.manifest["runtime"]["max_response_bytes"])
        try:
            while True:
                line = process.stdout.readline(maximum + 1)
                if not line:
                    self._stdout_queue.put(None)
                    return
                if len(line) > maximum or not line.endswith(b"\n"):
                    self._stdout_queue.put(ExtensionHostError("response_too_large", "extension response exceeded declared byte limit"))
                    return
                self._stdout_queue.put(line)
        except Exception as exc:
            self._stdout_queue.put(exc)

    def _stderr_reader(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        remaining = 8192
        while remaining > 0:
            chunk = process.stderr.read(min(1024, remaining))
            if not chunk:
                return
            self._stderr_chunks.append(chunk)
            remaining -= len(chunk)

    def _terminate(self) -> None:
        process, self._process = self._process, None
        if self._job is not None:
            try:
                self._job.terminate(1)
            finally:
                self._job.close()
                self._job = None
        if process is not None:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=1)
            except (subprocess.TimeoutExpired, OSError):
                pass
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None


def _json_hash(value: Any) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ExtensionHostError("json_contract", "extension value is not finite JSON") from exc
    return hashlib.sha256(encoded).hexdigest()
