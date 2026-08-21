"""Local model runtime protocol. No hosted APIs."""

from __future__ import annotations

import os
from typing import Any, Mapping, Protocol, runtime_checkable

from .contracts import PROMPT_TEMPLATE_VERSION

BACKEND_ENV = "CUBEENGINE_LLM_BACKEND"
MODEL_PATH_ENV = "CUBEENGINE_LLM_MODEL_PATH"


class ModelRuntimeError(RuntimeError):
    pass


@runtime_checkable
class LocalModelRuntime(Protocol):
    backend_id: str
    prompt_template_version: str

    def identity(self) -> Mapping[str, Any]:
        ...

    def complete(self, task: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


def runtime_identity(backend_id: str, **extra: Any) -> Mapping[str, Any]:
    record = {
        "backend_id": backend_id,
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        "weight_hash": extra.get("weight_hash") or "",
        "tokenizer_hash": extra.get("tokenizer_hash") or "",
        "model_path": extra.get("model_path") or "",
    }
    record.update({key: value for key, value in extra.items() if key not in record})
    return record


def create_runtime(backend: str = None) -> LocalModelRuntime:
    selected = (backend or os.environ.get(BACKEND_ENV) or "fixture").strip().lower()
    if selected in ("fixture", "fixtures", "test"):
        from .fixture_runtime import FixtureModelRuntime
        return FixtureModelRuntime()
    if selected in ("huggingface", "hf", "transformers"):
        from .hf_runtime import HuggingFaceModelRuntime
        return HuggingFaceModelRuntime()
    raise ModelRuntimeError("unknown FreeFlow-LLM backend: {0}".format(selected))
