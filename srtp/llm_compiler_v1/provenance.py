"""Run provenance: what code, prompts, caches and model calls produced a compile.

``provenance()``/``describe()`` give the checkout, branch, commit and
model-facing contract in short form; they are recorded in every staged compile
report and shown in the Workbench before a paid conversion, so a run from a
stale checkout or an unexpected branch is visible before money is spent.

Every model call made through OpenRouterLLMClient.chat_json is recorded with the
exact messages sent, the raw reply text, the parsed/failed status and the token
and cost change it caused. Each written run (successful or ``.failed``) gets
``provenance.json`` (code location, git commit, contract/prompt versions,
model settings, cache fingerprint and which stages came from cache) and
``requests.jsonl`` (one line per call). Nothing secret is recorded: API keys
never appear in messages, and only non-secret CUBEENGINE_LLM_* settings are kept.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

PROVENANCE_VERSION = "cubeengine.srtp/run-provenance/1"
REPO = Path(__file__).resolve().parents[2]
_SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _usage(client: Any) -> Dict[str, Any]:
    summary = dict(getattr(client, "usage_summary", None) or {})
    budget = getattr(client, "budget", None)
    return {"http_requests": int(getattr(client, "http_requests", 0) or 0),
            "input_tokens": summary.get("input_tokens") or 0, "output_tokens": summary.get("output_tokens") or 0,
            "reported_cost_usd": summary.get("reported_cost_usd"),
            "budget_spent_usd": _number(getattr(budget, "spent", None))}


def _number(value: Any) -> Optional[float]:
    value = value() if callable(value) else value
    return float(value) if isinstance(value, (int, float)) else None


def _delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> Dict[str, Any]:
    result = {}
    for key in ("http_requests", "input_tokens", "output_tokens", "reported_cost_usd", "budget_spent_usd"):
        a, b = before.get(key), after.get(key)
        if isinstance(a, (int, float)) or isinstance(b, (int, float)):
            result[key] = round((b or 0) - (a or 0), 8)
    return result


def _stage_of(messages: Any) -> Optional[str]:
    try:
        payload = json.loads(messages[-1]["content"])
        return payload.get("stage") or payload.get("task") or payload.get("role")
    except (TypeError, ValueError, KeyError, IndexError, AttributeError):
        return None


def record_request(client: Any, messages: Any, options: Mapping[str, Any], call: Callable[[], Any]) -> Any:
    """Run one chat call and append its record to ``client.request_log`` (also on failure)."""
    log = getattr(client, "request_log", None)
    if log is None:
        return call()
    before = _usage(client)
    entry: Dict[str, Any] = {"n": len(log) + 1, "started_at": _now(), "stage": _stage_of(messages),
                             "schema_name": options.get("schema_name"), "model": getattr(client, "model", None),
                             "messages": [dict(m) for m in messages] if isinstance(messages, list) else messages}
    try:
        result = call()
    except BaseException as error:  # noqa: BLE001 - recorded and re-raised unchanged
        entry.update(status="error", error_type=type(error).__name__, error=str(error)[:4000],
                     response_text=getattr(error, "content", None) or getattr(error, "partial_content", None))
        raise
    else:
        entry.update(status="ok", response_text=getattr(result, "content", None),
                     provider=getattr(result, "provider", None))
        return result
    finally:
        entry["finished_at"] = _now()
        entry["usage_delta"] = _delta(before, _usage(client))
        log.append(entry)


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        output = subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, text=True, timeout=10,
                                check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return output.stdout.strip() if output.returncode == 0 else None


def provenance() -> Dict[str, Any]:
    from .backend_contract import PROFILE_VERSION
    from .staged import SYSTEM
    status = _git(REPO, "status", "--porcelain", "--untracked-files=no")
    return {
        "checkout": str(REPO),
        "branch": _git(REPO, "branch", "--show-current"),
        "commit": _git(REPO, "rev-parse", "--short=12", "HEAD"),
        "local_changes": bool(status) if status is not None else None,
        "backend_profile": PROFILE_VERSION,
        "stage_prompt_sha256": hashlib.sha256(SYSTEM.encode("utf-8")).hexdigest()[:12],
        "python": sys.executable,
    }


def describe(info: Dict[str, Any]) -> str:
    commit = info.get("commit") or "unknown commit"
    if info.get("local_changes"):
        commit += " + uncommitted changes"
    return "Engine {0} on {1} @ {2}; contract {3}, stage prompt {4}".format(
        info.get("checkout"), info.get("branch") or "unknown branch", commit,
        info.get("backend_profile"), info.get("stage_prompt_sha256"))


def code_fingerprint() -> Dict[str, Any]:
    """Which checkout and commit this process is running (catches stale working copies)."""
    import srtp
    package = Path(srtp.__file__).resolve().parent
    root = package.parent
    status = _git(root, "status", "--porcelain")
    return {"srtp_imported_from": str(package), "repo_root": str(root), "cwd": os.getcwd(),
            "git_commit": _git(root, "rev-parse", "HEAD"), "git_branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
            "git_dirty_files": None if status is None else len([line for line in status.splitlines() if line.strip()]),
            "python": sys.version.split()[0], "python_executable": sys.executable}


def contract_fingerprint() -> Dict[str, Any]:
    """Prompt and capability versions the model was given."""
    from .backend_contract import PROFILE_VERSION, profile
    from .evidence import PROMPT_TEMPLATE_VERSION
    from .staged import SYSTEM
    result = {"prompt_template_version": PROMPT_TEMPLATE_VERSION,
              "staged_system_prompt_sha256": hashlib.sha256(SYSTEM.encode("utf-8")).hexdigest(),
              "backend_profile_version": PROFILE_VERSION,
              "backend_profile_sha256": hashlib.sha256(json.dumps(profile(), sort_keys=True, default=str)
                                                       .encode("utf-8")).hexdigest()}
    try:
        from .agent_prompts import AGENT_PROMPT_VERSION
        result["agent_prompt_version"] = AGENT_PROMPT_VERSION
    except ImportError:
        pass
    return result


def model_settings(client: Any) -> Dict[str, Any]:
    env = {key: value for key, value in os.environ.items()
           if key.startswith("CUBEENGINE_") and not any(word in key.upper() for word in _SECRET_WORDS)}
    return {"provider": getattr(client, "provider", None), "model": getattr(client, "model", None),
            "reasoning_effort": getattr(client, "reasoning_effort", None), "max_tokens": getattr(client, "max_tokens", None),
            "output_format": getattr(client, "output_format", None), "max_requests": getattr(client, "max_requests", None),
            "settings": dict(sorted(env.items()))}


def build_provenance(report: Any, client: Any, requests: List[Mapping[str, Any]], *,
                     extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    trace = getattr(report, "compilation_trace", None) or {}
    stages = trace.get("stages") or []
    return {
        "provenance_version": PROVENANCE_VERSION, "written_at": _now(),
        "job_id": getattr(report, "job_id", None), "stage": getattr(report, "stage", None),
        "ok": getattr(report, "ok", None), "source_package_hash": getattr(report, "source_package_hash", None),
        "code": code_fingerprint(), "contracts": contract_fingerprint(), "model": model_settings(client),
        "cache": dict(trace.get("cache") or {}, stages_from_cache=sorted({s.get("stage") for s in stages
                                                                          if s.get("cached") and s.get("passed")})),
        "requests": {"count": len(requests), "file": "requests.jsonl",
                     "summary": [{k: r.get(k) for k in ("n", "stage", "status", "error_type", "usage_delta")}
                                 for r in requests]},
        "usage": trace.get("api_usage"),
        "repair_summary": repair_summary(stages, trace),
        **(dict(extra) if extra else {}),
    }


def repair_summary(stages: List[Mapping[str, Any]], trace: Mapping[str, Any]) -> Dict[str, Any]:
    """Per stage: attempts, which were paid, why each failed (classified), and cross-stage rounds."""
    from .failure_report import classify
    summary: Dict[str, Any] = {}
    for step in stages:
        stage = str(step.get("stage"))
        row = summary.setdefault(stage, {"attempts": 0, "cached_attempts": 0, "engine_attempts": 0,
                                         "passed": False, "failures": []})
        row["attempts"] += 1
        row["cached_attempts"] += int(bool(step.get("cached")))
        row["engine_attempts"] += int(bool(step.get("engine")))
        row["passed"] = row["passed"] or bool(step.get("passed"))
        for item in step.get("diagnostics") or []:
            row["failures"].append({"attempt": step.get("attempt"), "category": classify(str(item)),
                                    "diagnostic": str(item)[:240]})
        if step.get("upstream_request"):
            row["upstream_request"] = [r.get("ir") for r in step["upstream_request"]]
    return {"stages": summary,
            "upstream_rounds": [{k: v for k, v in r.items() if k in ("requested_by", "reopened", "kept", "failed_stage")}
                                for r in trace.get("upstream_rounds") or []],
            "source_oracle": {k: (trace.get("source_oracle") or {}).get(k) for k in ("status", "checked_steps")},
            "scene_draft": (trace.get("scene_draft") or {}).get("used")}
