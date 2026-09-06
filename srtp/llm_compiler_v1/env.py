"""Load repo-root ``.env`` so FreeFlow can read GEMINI_API_KEY / GROQ_API_KEY."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any, List, Optional

_PROVIDER_ENV = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}


def repo_root() -> Path:
    """CubeEngine-Dev root (``srtp/llm_compiler_v1/env.py`` → parents[2])."""

    return Path(__file__).resolve().parents[2]


def default_dotenv_path() -> Path:
    return repo_root() / ".env"


def _clean_api_key(value: Any) -> str:
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def parse_api_keys(value: Optional[str]) -> List[str]:
    """Parse a single key, comma-separated keys, JSON array, or Python list literal.

    ``.env`` must use JSON double quotes for arrays. Python-style
    ``['k1', 'k2']`` is accepted as a compatibility fallback so FreeFlow does
    not send mangled keys (leading ``['``) to the provider.
    """

    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        parsed: Any = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                parsed = None
        if isinstance(parsed, list):
            return [
                key for key in (_clean_api_key(item) for item in parsed)
                if key
            ]
    if "," in text:
        return [
            key for key in (_clean_api_key(part) for part in text.split(","))
            if key
        ]
    key = _clean_api_key(text)
    return [key] if key else []


def provider_api_keys(provider: str) -> List[str]:
    env_name = _PROVIDER_ENV.get(provider.lower())
    if not env_name:
        return []
    return parse_api_keys(os.environ.get(env_name))


def load_compiler_env(
    *,
    dotenv_path: Optional[Path] = None,
    override: bool = True,
) -> Optional[Path]:
    """Load repo-root ``.env`` into ``os.environ``.

    Provider keys default to ``override=True`` so a stale single-key process
    env (shell / IDE) cannot hide a multi-key list in ``.env``.
    """

    path = Path(dotenv_path) if dotenv_path is not None else default_dotenv_path()
    if not path.is_file():
        return None
    try:
        from dotenv import load_dotenv
    except ImportError:
        _load_env_file_fallback(path, override=override)
        return path
    load_dotenv(path, override=override, encoding="utf-8")
    return path


def _load_env_file_fallback(path: Path, *, override: bool) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not override and os.environ.get(name):
            continue
        os.environ[name] = value
