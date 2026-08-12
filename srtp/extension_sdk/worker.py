"""Private JSON-lines worker for one verified Extension package.

This module is launched with Python isolated mode. It deliberately uses only
the standard library and never imports the CubeEngine application package.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path


PROTOCOL = "cubeengine.extension-rpc/1.0"


class WorkerError(RuntimeError):
    pass


def _load_json(path):
    with open(str(path), "r", encoding="utf-8") as handle:
        return json.load(handle, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _inside(path, roots):
    try:
        resolved = Path(path).resolve()
    except (OSError, TypeError, ValueError):
        return False
    return any(resolved == root or root in resolved.parents for root in roots)


def _install_cooperative_guards(root, manifest):
    """Add defence-in-depth guards; these are not hostile-code containment."""

    permissions = manifest["permissions"]
    allowed_modules = set(item.split(".", 1)[0] for item in permissions["python_modules"])
    allowed_modules.update(
        Path(item["path"]).stem
        for item in manifest["files"] if str(item["path"]).endswith(".py")
    )
    allowed_modules.add("_cubeengine_extension")
    read_roots = tuple({
        root.resolve(), Path(sys.base_prefix).resolve(), Path(__file__).resolve().parent,
    })
    write_roots = ()
    denied_events = {
        "os.system", "os.exec", "os.posix_spawn", "subprocess.Popen",
        "socket.__new__", "socket.connect", "socket.bind", "socket.getaddrinfo",
        "ctypes.dlopen", "ctypes.dlsym", "ctypes.call_function",
        "winreg.OpenKey", "winreg.CreateKey", "winreg.SetValue",
    }

    def audit(event, args):
        if event in denied_events or event.startswith("subprocess.") or event.startswith("socket."):
            raise PermissionError("extension permission denied: " + event)
        if event == "import" and args:
            name = str(args[0]).split(".", 1)[0]
            if name not in allowed_modules:
                raise PermissionError("extension import is not allowlisted: " + name)
        if event == "open" and args and not isinstance(args[0], int):
            target = args[0]
            mode = args[1] if len(args) > 1 else "r"
            flags = args[2] if len(args) > 2 else 0
            writing = (
                isinstance(mode, str) and any(character in mode for character in "wax+")
            ) or (
                isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            )
            roots = write_roots if writing else read_roots
            if not _inside(target, roots):
                raise PermissionError("extension filesystem access denied")

    sys.addaudithook(audit)


def _load_adapter(root, manifest):
    entrypoint = (root / Path(*manifest["entrypoint"]["file"].split("/"))).resolve()
    if root not in entrypoint.parents:
        raise WorkerError("entrypoint escaped the verified package root")
    sys.dont_write_bytecode = True
    sys.path[:] = [str(root)]
    spec = importlib.util.spec_from_file_location("_cubeengine_extension", str(entrypoint))
    if spec is None or spec.loader is None:
        raise WorkerError("entrypoint could not be loaded")
    module = importlib.util.module_from_spec(spec)
    captured_out, captured_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
        spec.loader.exec_module(module)
        factory = getattr(module, manifest["entrypoint"]["factory"], None)
        if not callable(factory):
            raise WorkerError("entrypoint factory is missing or not callable")
        adapter = factory()
    for name in ("initialize", "invoke", "snapshot", "restore", "shutdown"):
        if not callable(getattr(adapter, name, None)):
            raise WorkerError("adapter lifecycle method is missing: " + name)
    return adapter, _diagnostics(captured_out, captured_err)


def _diagnostics(out, err):
    text = (out.getvalue() + err.getvalue())[:4096]
    return text if text else ""


def _call(adapter, name, *args):
    captured_out, captured_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
        result = getattr(adapter, name)(*args)
    return result, _diagnostics(captured_out, captured_err)


def _write(payload, maximum):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8") + b"\n"
    if len(encoded) > maximum:
        fallback = {
            "protocol": PROTOCOL,
            "id": payload.get("id"),
            "ok": False,
            "error": {"code": "response_too_large", "message": "Extension response exceeded its declared byte limit."},
            "diagnostics": "",
        }
        encoded = json.dumps(fallback, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def _error(identifier, code, message, maximum):
    _write({
        "protocol": PROTOCOL,
        "id": identifier,
        "ok": False,
        "error": {"code": code, "message": str(message)[:1024]},
        "diagnostics": "",
    }, maximum)


def run(root, manifest_path, expected_hash):
    root = Path(root).resolve()
    manifest_path = Path(manifest_path).resolve()
    manifest = _load_json(manifest_path)
    content_hash = manifest.get("content_hash", "")
    if content_hash != expected_hash:
        raise WorkerError("host/worker manifest hash mismatch")
    maximum_in = int(manifest["runtime"]["max_request_bytes"])
    maximum_out = int(manifest["runtime"]["max_response_bytes"])
    _install_cooperative_guards(root, manifest)
    adapter, import_diagnostics = _load_adapter(root, manifest)
    capabilities = {item["id"]: item for item in manifest["capabilities"]}
    _write({
        "protocol": PROTOCOL,
        "id": 0,
        "ok": True,
        "result": {
            "event": "hello",
            "extension_id": manifest["extension_id"],
            "version": manifest["version"],
            "content_hash": content_hash,
            "capabilities": sorted(capabilities),
        },
        "diagnostics": import_diagnostics,
    }, maximum_out)
    initialized = False
    while True:
        line = sys.stdin.buffer.readline(maximum_in + 1)
        if not line:
            break
        if len(line) > maximum_in or not line.endswith(b"\n"):
            _error(None, "request_too_large", "RPC request exceeded its declared byte limit.", maximum_out)
            break
        try:
            request = json.loads(line.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if not isinstance(request, dict) or set(request) != {"protocol", "id", "op", "payload"}:
                raise WorkerError("RPC request fields are invalid")
            if request["protocol"] != PROTOCOL or isinstance(request["id"], bool) or not isinstance(request["id"], int):
                raise WorkerError("RPC protocol or request ID is invalid")
            if not isinstance(request["payload"], dict):
                raise WorkerError("RPC payload must be an object")
            identifier, operation, payload = request["id"], request["op"], request["payload"]
            if operation == "initialize":
                if initialized:
                    raise WorkerError("extension is already initialized")
                result, diagnostics = _call(adapter, "initialize", payload.get("context", {}))
                initialized = True
            elif operation == "invoke":
                if not initialized:
                    raise WorkerError("extension is not initialized")
                capability_id = payload.get("capability_id")
                if capability_id not in capabilities:
                    raise WorkerError("unknown capability")
                result, diagnostics = _call(
                    adapter, "invoke", capability_id,
                    capabilities[capability_id]["method"], payload.get("request"),
                )
            elif operation == "snapshot":
                if not initialized:
                    raise WorkerError("extension is not initialized")
                result, diagnostics = _call(adapter, "snapshot")
            elif operation == "restore":
                if not initialized:
                    raise WorkerError("extension is not initialized")
                result, diagnostics = _call(adapter, "restore", payload.get("snapshot"))
            elif operation == "shutdown":
                result, diagnostics = _call(adapter, "shutdown")
                _write({"protocol": PROTOCOL, "id": identifier, "ok": True, "result": result, "diagnostics": diagnostics}, maximum_out)
                return 0
            else:
                raise WorkerError("unsupported lifecycle operation")
            _write({"protocol": PROTOCOL, "id": identifier, "ok": True, "result": result, "diagnostics": diagnostics}, maximum_out)
        except WorkerError as exc:
            _error(request.get("id") if isinstance(request, dict) else None, "protocol_error", exc, maximum_out)
        except Exception as exc:
            _error(request.get("id") if isinstance(request, dict) else None, "adapter_error", "{0}: {1}".format(type(exc).__name__, exc), maximum_out)
    try:
        adapter.shutdown()
    except Exception:
        pass
    return 0


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--hash", required=True)
    args = parser.parse_args()
    try:
        return run(args.root, args.manifest, args.hash)
    except Exception as exc:
        payload = {
            "protocol": PROTOCOL, "id": 0, "ok": False,
            "error": {"code": "worker_start_failed", "message": "{0}: {1}".format(type(exc).__name__, exc)[:1024]},
            "diagnostics": "",
        }
        sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
