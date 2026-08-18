"""Module 1→4 pipeline: ingest → FreeFlow → runtime → optional Ursina play."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from srtp.source_importer import SourceGameImporter

from .freeflow import FreeFlowRequest, select_compiler
from .parser import parse_ludeme_text
from .runtime import LudemeRuntime
from .validate import validation_errors


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PACKAGE_DIR / "examples" / "generated"


@dataclass
class PipelineResult:
    source_path: Path
    ludeme_path: Path
    compiler_id: str
    runtime: LudemeRuntime


def run_pipeline(
    source_path: Path,
    target_z: int = 1,
    output_path: Optional[Path] = None,
    design_intent: str = "",
) -> PipelineResult:
    source_path = Path(source_path).resolve()
    package = SourceGameImporter().import_path(source_path)
    compiler = select_compiler(package)
    freeflow = compiler.compile(FreeFlowRequest(
        package=package,
        target_z=target_z,
        design_intent=design_intent,
    ))
    destination = output_path or _default_output_path(source_path, target_z)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(freeflow.ludeme_text, encoding="utf-8")

    game = parse_ludeme_text(freeflow.ludeme_text, source=str(destination))
    errors = validation_errors(game)
    if errors:
        raise ValueError("Generated Ludeme failed validation: {0}".format(errors[0].message))

    runtime = LudemeRuntime(game)
    return PipelineResult(
        source_path=source_path,
        ludeme_path=destination,
        compiler_id=freeflow.compiler_id,
        runtime=runtime,
    )


def _default_output_path(source_path: Path, target_z: int) -> Path:
    stem = source_path.stem.lower()
    if int(target_z) >= 3 and "tictactoe" in stem:
        return PACKAGE_DIR / "examples" / "tictactoe_3x3x3.cube.lud"
    suffix = "3d" if target_z > 1 else "2d"
    return DEFAULT_OUTPUT_DIR / "{0}_{1}.cube.lud".format(source_path.stem, suffix)


def launch_ursina(ludeme_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-m", "ludeme.ursina_viewer", "--ludeme-file", str(ludeme_path.resolve())]
    subprocess.run(command, cwd=str(root), check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="CubeEngine Ludeme pipeline (Module 1→4)")
    parser.add_argument("--source", required=True, help="Source game entry point or project path")
    parser.add_argument("--z", type=int, default=1, help="Target depth for spatial lift (default: 1)")
    parser.add_argument("--output", help="Output .cube.lud path")
    parser.add_argument("--intent", default="", help="Optional natural-language design intent for FreeFlow")
    parser.add_argument("--play", action="store_true", help="Open Ursina 3D viewer after compilation")
    args = parser.parse_args()

    output = Path(args.output) if args.output else None
    try:
        result = run_pipeline(
            source_path=Path(args.source),
            target_z=max(1, int(args.z)),
            output_path=output,
            design_intent=args.intent,
        )
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from error

    print("Module 1: imported {0}".format(result.source_path))
    print("Module 2: {0} -> {1}".format(result.compiler_id, result.ludeme_path))
    print("Module 3: runtime ready ({0} cells, {1} legal moves)".format(
        result.runtime.cell_count,
        len(result.runtime.legal_moves()),
    ))
    if args.play:
        print("Module 4: launching Ursina...")
        launch_ursina(result.ludeme_path)
    else:
        print("Module 4: skipped (pass --play to open Ursina)")


if __name__ == "__main__":
    main()
