"""Drive an original pygame game headlessly and record its board after each input step.

Run only in a separate process (see llm_compiler_v1.source_oracle), like the
original preview. The source runs unchanged; the harness replaces event input
(scripted per frame) and frame pacing. When the game asks for the next
frame's events, the previous frame has handled its input, updated and drawn,
so board-shaped values reachable from the running source frames are read
then. No model output is ever executed here.

Usage: python -m srtp.source_oracle_worker PLAN.json RESULT.json
"""

from __future__ import annotations

import json
import os
import runpy
import sys
import time
from pathlib import Path

_SCALARS = (int, float, str, bool, type(None))


class _Stop(BaseException):
    pass


def main(plan_path: str, result_path: str) -> int:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    root = str(Path(plan["root"]).resolve())
    entry = str(Path(plan["entry"]).resolve())
    sys.path.insert(0, root)
    os.chdir(root)
    result = {"snapshots": [], "frames": 0, "error": None}
    try:
        import pygame
    except ImportError as error:
        result["error"] = "pygame unavailable: {0}".format(error)
        Path(result_path).write_text(json.dumps(result), encoding="utf-8")
        return 0
    shapes = {tuple(shape) for shape in plan["shapes"]}
    steps = plan["steps"]
    state = {"frame": 0, "step": -1, "mouse": (0, 0)}

    def event(spec):
        kind = getattr(pygame, spec["type"])
        data = {k: (tuple(v) if isinstance(v, list) else v) for k, v in spec.items() if k != "type"}
        if "key" in data and isinstance(data["key"], str):
            data["key"] = getattr(pygame, data["key"])
        if "pos" in data:
            state["mouse"] = data["pos"]
        return pygame.event.Event(kind, data)

    def get(*args, **kwargs):
        state["frame"] += 1
        if state["frame"] > plan.get("max_frames", 2000):
            raise _Stop()
        if state["frame"] == 1:
            return []  # let the game settle and draw its initial state first
        # The state after the previous step's frame (step -1: the start).
        result["snapshots"].append({"step": state["step"], "candidates": _candidates(root, shapes)})
        state["step"] += 1
        if state["step"] < len(steps):
            return [event(spec) for spec in steps[state["step"]]]
        if state["step"] == len(steps):
            return [pygame.event.Event(pygame.QUIT, {})]
        raise _Stop()

    class Clock:
        def tick(self, *args, **kwargs):
            return 16

        tick_busy_loop = tick

        def get_fps(self):
            return 60.0

        def get_time(self):
            return 16

    pygame.event.get = get
    pygame.event.poll = lambda: (get() or [pygame.event.Event(pygame.NOEVENT, {})])[0]
    pygame.event.wait = lambda *a, **k: (get() or [pygame.event.Event(pygame.NOEVENT, {})])[0]
    pygame.event.pump = lambda *a, **k: None
    pygame.mouse.get_pos = lambda: state["mouse"]
    pygame.time.Clock = Clock
    pygame.time.wait = lambda *a, **k: 0
    pygame.time.delay = lambda *a, **k: 0
    time.sleep = lambda *a, **k: None
    try:
        runpy.run_path(entry, run_name="__main__")
    except (_Stop, SystemExit):
        pass
    except Exception as error:  # noqa: BLE001 - the source's own failure is a finding
        result["error"] = "{0}: {1}".format(type(error).__name__, error)
    result["frames"] = state["frame"]
    Path(result_path).write_text(json.dumps(result), encoding="utf-8")
    return 0


def _candidates(root: str, shapes) -> dict:
    """Board-shaped values reachable from the running source frames (by access path)."""
    found = {}
    frame = sys._getframe(2)
    seen = set()
    while frame is not None:
        filename = str(Path(frame.f_code.co_filename).resolve()) if frame.f_code.co_filename else ""
        if filename.startswith(root):
            scope = frame.f_code.co_name
            for name, value in list(frame.f_locals.items()) + list(frame.f_globals.items()):
                if not name.startswith("__"):
                    _walk(value, "{0}.{1}".format(scope, name), found, shapes, seen, 0, root)
        frame = frame.f_back
    return found


def _walk(value, path, found, shapes, seen, depth, root):
    if depth > 3 or id(value) in seen or len(found) > 64 or isinstance(value, type) or callable(value):
        return
    seen.add(id(value))
    grid = _grid(value)
    if grid is not None:
        shape = _shape(grid)
        if shape and any(sorted(shape) == sorted(expected) for expected in shapes):
            found[path] = grid
        return
    if isinstance(value, dict):
        for key, item in list(value.items())[:64]:
            if isinstance(key, str):
                _walk(item, "{0}[{1}]".format(path, key), found, shapes, seen, depth + 1, root)
        return
    module = getattr(type(value), "__module__", "") or ""
    source = getattr(sys.modules.get(module), "__file__", "") or ""
    if hasattr(value, "__dict__") and source and str(Path(source).resolve()).startswith(root):
        for key, item in list(vars(value).items())[:64]:
            _walk(item, "{0}.{1}".format(path, key), found, shapes, seen, depth + 1, root)


def _grid(value):
    if isinstance(value, type):
        return None
    if hasattr(value, "tolist") and hasattr(value, "shape"):
        try:
            value = value.tolist()
        except Exception:  # noqa: BLE001 - not an array after all
            return None
    if isinstance(value, list) and value and all(isinstance(row, (list, tuple)) for row in value):
        rows = [list(row) for row in value]
        if len({len(row) for row in rows}) == 1:
            nested = [_grid(list(row)) if row and isinstance(row[0], (list, tuple)) else row for row in rows]
            if all(isinstance(item, list) for item in nested) and all(
                    all(isinstance(cell, _SCALARS) for cell in _flatten(item)) for item in nested):
                return nested
    return None


def _flatten(value):
    for item in value:
        if isinstance(item, list):
            yield from _flatten(item)
        else:
            yield item


def _shape(grid):
    shape = []
    node = grid
    while isinstance(node, list):
        shape.append(len(node))
        node = node[0] if node else None
    return shape


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
