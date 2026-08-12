"""Supervised original-game runtime used as the SRTP fidelity reference."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from .source_game import SourceGamePackage


@dataclass
class OriginalGameProcess:
    package: SourceGamePackage
    process: Optional[subprocess.Popen]
    embedded: bool = False
    window_handle: int = 0
    reported: bool = False

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        if self.running and self.process is not None:
            self.process.terminate()

    def collect_output(self) -> str:
        if self.process is None or self.process.poll() is None or self.process.stdout is None:
            return ""
        try:
            return self.process.stdout.read() or ""
        except (OSError, ValueError):
            return ""


class SourceGameRunner:
    """Launch source only after an explicit designer action.

    Static import never executes the project.  Launching the baseline is a
    separate, visible operation because arbitrary user game code is trusted
    code, not a safe data file.
    """

    def launch(
        self,
        package: SourceGamePackage,
        embed_parent_title: str = "",
        embed_bounds: Tuple[int, int, int, int] = (650, 72, 920, 790),
        on_embedded=None,
    ) -> OriginalGameProcess:
        if not package.runtime.runnable:
            missing = ", ".join(package.runtime.missing_dependencies) or "unsupported runtime"
            raise RuntimeError("Original game is not runnable: {0}".format(missing))
        if package.runtime.kind == "html":
            webbrowser.open(Path(package.runtime.command[0]).as_uri())
            return OriginalGameProcess(package, None)
        env: Dict[str, str] = dict(os.environ)
        root = str(package.root)
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        process = subprocess.Popen(
            package.runtime.command,
            cwd=package.runtime.cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        result = OriginalGameProcess(package, process)
        if embed_parent_title and sys.platform == "win32":
            thread = threading.Thread(
                target=self._embed_later,
                args=(result, embed_parent_title, embed_bounds, on_embedded),
                daemon=True,
            )
            thread.start()
        elif sys.platform == "win32":
            threading.Thread(target=self._focus_later, args=(result,), daemon=True).start()
        return result

    @staticmethod
    def _focus_later(result: OriginalGameProcess) -> None:
        handle = _wait_for_process_window(result.process.pid if result.process else 0, timeout=8.0)
        result.window_handle = handle
        if not handle:
            return
        user32 = ctypes.windll.user32
        user32.ShowWindow(handle, 9)  # SW_RESTORE
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)

    @staticmethod
    def _embed_later(result, parent_title, bounds, callback) -> None:
        child = _wait_for_process_window(result.process.pid if result.process else 0, timeout=8.0)
        parent = ctypes.windll.user32.FindWindowW(None, parent_title)
        if child and parent:
            _reparent_window(child, parent, bounds)
            result.embedded = True
            result.window_handle = child
        if callback:
            callback(result.embedded)


def _wait_for_process_window(process_id: int, timeout: float) -> int:
    if sys.platform != "win32" or not process_id:
        return 0
    user32 = ctypes.windll.user32
    found = {"handle": 0}
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd, _lparam):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process_id and user32.IsWindowVisible(hwnd):
            found["handle"] = int(hwnd)
            return False
        return True

    callback = callback_type(visit)
    deadline = time.time() + timeout
    while time.time() < deadline:
        user32.EnumWindows(callback, 0)
        if found["handle"]:
            return found["handle"]
        time.sleep(0.1)
    return 0


def _reparent_window(child: int, parent: int, bounds: Tuple[int, int, int, int]) -> None:
    user32 = ctypes.windll.user32
    style_index = -16
    ws_child = 0x40000000
    ws_popup = 0x80000000
    ws_caption = 0x00C00000
    ws_thickframe = 0x00040000
    swp_showwindow = 0x0040
    swp_framechanged = 0x0020
    style = user32.GetWindowLongW(child, style_index)
    style = (style & ~ws_popup & ~ws_caption & ~ws_thickframe) | ws_child
    user32.SetWindowLongW(child, style_index, style)
    user32.SetParent(child, parent)
    x, y, width, height = bounds
    user32.SetWindowPos(child, 0, x, y, width, height, swp_showwindow | swp_framechanged)
