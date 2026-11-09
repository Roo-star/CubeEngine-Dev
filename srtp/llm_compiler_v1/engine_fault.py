"""Tell engine defects apart from diagnostics a model can repair.

Every gate reports a problem in model output with a domain error (a
``ValueError`` subclass such as ``DefinitionValidationError`` or
``RuleIRPatchError``, a ``RuleRuntimeError``, or an ``LLMClientError``). No
gate raises a bare ``KeyError``, ``IndexError``, ``TypeError`` or
``AttributeError`` on purpose, so one escaping a gate means engine code read
or combined data it had not validated. Sending such a message (``'key'``) to the
model as repair feedback pays for a repair the model cannot make. The stage
stops instead, keeps the paid reply for local revalidation after the fix, and
reports where the engine failed.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, Dict

ENGINE_FAULT_TYPES = (KeyError, IndexError, TypeError, AttributeError)


def is_engine_fault(error: BaseException) -> bool:
    """True for a defect in engine code or the local environment, not in model output."""
    from .client import LLMClientError
    if isinstance(error, LLMClientError):
        return False
    return isinstance(error, ENGINE_FAULT_TYPES) or isinstance(error, OSError)


def engine_fault_record(error: BaseException, slot: str) -> Dict[str, Any]:
    """Where the engine failed: innermost engine frames, never model-facing text."""
    frames = traceback.extract_tb(error.__traceback__)
    location = [
        "{0}:{1} in {2}".format(Path(frame.filename).name, frame.lineno, frame.name)
        for frame in frames[-4:]
    ]
    kind = "environment" if isinstance(error, OSError) else "engine"
    return {
        "kind": kind,
        "stage": slot,
        "type": type(error).__name__,
        "message": str(error)[:500],
        "location": location,
    }


def engine_fault_diagnostic(record: Dict[str, Any]) -> str:
    where = record["location"][-1] if record.get("location") else "unknown location"
    return ("{kind} error, not sent to the model: {type}({message}) at {where}. The paid reply is kept; "
            "after the engine is fixed, Continue revalidates it without a new request.").format(
                kind=record["kind"], type=record["type"], message=record["message"], where=where)
