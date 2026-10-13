"""Call functions of an original source game in an isolated, headless process.

Used by ``llm_compiler_v1.source_equivalence``. Each call names a module file
inside the source root, a module-level function, JSON arguments and an optional
seed for Python's ``random``. The worker returns the function's result and the
value of its grid argument after the call, so both returning and in-place
functions can be compared. Nothing is rewritten; the source runs as shipped.

Usage: python -m srtp.source_function_worker PLAN.json RESULT.json
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
from copy import deepcopy
from pathlib import Path


def _jsonable(value):
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    tolist = getattr(value, "tolist", None)  # numpy arrays and scalars
    if callable(tolist):
        return _jsonable(tolist())
    raise TypeError("unsupported return type {0}".format(type(value).__name__))


def _load(root: Path, relative: str, cache: dict):
    if relative in cache:
        return cache[relative]
    path = (root / relative).resolve()
    if root not in path.parents or path.suffix != ".py" or not path.is_file():
        raise ValueError("{0} is not a Python file inside the source root".format(relative))
    folder = str(path.parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    # Register under the file's own module name so sibling imports
    # (``from logic import *``) resolve to this same module object.
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    cache[relative] = module
    return module


def main(plan_path: str, result_path: str) -> int:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    root = Path(plan["root"]).resolve()
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    os.chdir(root)  # sources open their own data files relative to the game folder
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    modules: dict = {}
    results = []
    for call in plan["calls"]:
        try:
            module = _load(root, call["file"], modules)
            function = getattr(module, call["name"], None)
            if not callable(function):
                raise ValueError("{0} has no function {1}".format(call["file"], call["name"]))
            args = deepcopy(call.get("args") or [])
            kwargs = deepcopy(call.get("kwargs") or {})
            if call.get("seed") is not None:
                random.seed(call["seed"])
            value = function(*args, **kwargs)
            grid_index = call.get("grid_arg")
            entry = {"ok": True, "value": _jsonable(value)}
            if grid_index is not None:
                entry["grid"] = _jsonable(args[grid_index])
            results.append(entry)
        except SystemExit as error:
            results.append({"ok": False, "error": "the function exited the program ({0})".format(error)})
        except Exception as error:  # noqa: BLE001 - report the source's own failure to the caller
            results.append({"ok": False, "error": "{0}: {1}".format(type(error).__name__, error)})
    Path(result_path).write_text(json.dumps({"results": results}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
