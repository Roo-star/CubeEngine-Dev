"""Windows-safe launcher for playable SRTP-to-STAL 3D previews."""

from __future__ import annotations

import os
import subprocess
import sys
import ctypes
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .source_game import SourceGamePackage
from .source_runner import _wait_for_process_window


@dataclass
class TransformedGameProcess:
    process: Optional[subprocess.Popen]
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


class TransformedGameRunner:
    @staticmethod
    def adapter_arguments(package: SourceGamePackage):
        """Shared, validated launch contract for standalone and embedded demos."""
        plan = package.transformation
        dimensions = plan.target_dimensions
        if not plan.adapter_id:
            raise RuntimeError("No 3D transformation adapter is registered for this source game.")
        if plan.readiness != "ready":
            raise RuntimeError("The source mechanics are not fully compiled for 3D preview.")
        if not all(isinstance(dimensions.get(axis), int) and dimensions[axis] > 0 for axis in ("x", "y", "z")):
            raise RuntimeError("Set a valid target Z before entering 3D Play Mode.")
        args = ["-m", "srtp.transformed_viewer", "--adapter", plan.adapter_id,
                "--x", str(dimensions["x"]), "--y", str(dimensions["y"]), "--z", str(dimensions["z"]),
                "--source-root", str(package.root)]
        for flag, parameter, default in (("--source-mines", "source_mine_count", 10),
                                         ("--tick-ms", "source_tick_ms", 125),
                                         ("--connect-n", "source_connect_n", 4)):
            item = package.parameter(parameter)
            args.extend([flag, str(item.value if item else default)])
        source = plan.source_dimensions
        if isinstance(source.get("x"), int) and isinstance(source.get("y"), int):
            args.extend(["--source-x", str(source["x"]), "--source-y", str(source["y"])])
        return args

    def launch_project(self, manifest: Path, source_root: Path) -> TransformedGameProcess:
        from .llm_compiler_v1.compiler import load_compile_report_from_bundle
        report = load_compile_report_from_bundle(manifest.parent, manifest_path=manifest)
        if not report.compile_ready:
            raise RuntimeError("Approve the generated Target before 3D Play.")
        root = Path(__file__).resolve().parents[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "srtp.project_viewer", "--manifest", str(manifest.resolve()),
             "--source-root", str(source_root.resolve())], cwd=str(root),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        result = TransformedGameProcess(process)
        if sys.platform == "win32":
            threading.Thread(target=self._focus_later, args=(result,), daemon=True).start()
        return result

    def launch(self, package: SourceGamePackage) -> TransformedGameProcess:
        root = Path(__file__).resolve().parents[1]
        command = [sys.executable] + self.adapter_arguments(package)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        process = subprocess.Popen(
            command, cwd=str(root), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        result = TransformedGameProcess(process)
        if sys.platform == "win32":
            threading.Thread(target=self._focus_later, args=(result,), daemon=True).start()
        return result

    @staticmethod
    def _focus_later(result: TransformedGameProcess) -> None:
        handle = _wait_for_process_window(result.process.pid if result.process else 0, timeout=10.0)
        result.window_handle = handle
        if not handle:
            return
        user32 = ctypes.windll.user32
        user32.ShowWindow(handle, 9)
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
