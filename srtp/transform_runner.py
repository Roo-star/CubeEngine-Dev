"""Windows-safe launcher for playable SRTP-to-STAL 3D previews."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .source_game import SourceGamePackage


@dataclass
class TransformedGameProcess:
    process: Optional[subprocess.Popen]

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        if self.running and self.process is not None:
            self.process.terminate()


class TransformedGameRunner:
    def launch(self, package: SourceGamePackage) -> TransformedGameProcess:
        plan = package.transformation
        dimensions = plan.target_dimensions
        if not plan.adapter_id:
            raise RuntimeError("No 3D transformation adapter is registered for this source game.")
        if plan.readiness != "ready":
            raise RuntimeError("The source mechanics are not fully compiled for 3D preview.")
        if not all(isinstance(dimensions.get(axis), int) and dimensions[axis] > 0 for axis in ("x", "y", "z")):
            raise RuntimeError("Set a valid target Z before entering 3D Play Mode.")
        root = Path(__file__).resolve().parents[1]
        command = [
            sys.executable, "-m", "srtp.transformed_viewer",
            "--adapter", plan.adapter_id,
            "--x", str(dimensions["x"]),
            "--y", str(dimensions["y"]),
            "--z", str(dimensions["z"]),
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        process = subprocess.Popen(command, cwd=str(root), env=env)
        return TransformedGameProcess(process)
