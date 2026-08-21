"""CLI: python -m srtp.freeflow_llm compile|lift ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .compiler import FreeFlowLlmCompiler, FreeFlowLlmError
from .runtime import create_runtime


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m srtp.freeflow_llm")
    sub = parser.add_subparsers(dest="command", required=True)

    compile_cmd = sub.add_parser("compile", help="Source evidence to sealed four-IR source project")
    compile_cmd.add_argument("--source", required=True, help="Source game path or entry point")
    compile_cmd.add_argument("--out", required=True, help="Output bundle directory")
    compile_cmd.add_argument("--backend", default=None, help="fixture or huggingface")

    lift_cmd = sub.add_parser("lift", help="Source bundle plus Design Intent to target 3D project")
    lift_cmd.add_argument("--bundle", required=True, help="Sealed source bundle directory")
    lift_cmd.add_argument("--intent", required=True, help="Natural-language transformation instruction")
    lift_cmd.add_argument("--out", required=True, help="Output target bundle directory")
    lift_cmd.add_argument("--backend", default=None, help="fixture or huggingface")
    lift_cmd.add_argument("--accept-intent", action="store_true", help="Programmatically accept Design Intent")

    args = parser.parse_args(argv)
    compiler = FreeFlowLlmCompiler(runtime=create_runtime(args.backend))
    try:
        if args.command == "compile":
            result = compiler.compile_source(Path(args.source), output_dir=Path(args.out))
        else:
            result = compiler.lift_bundle(
                Path(args.bundle), args.intent, output_dir=Path(args.out),
                accept_intent=bool(args.accept_intent),
            )
    except FreeFlowLlmError as error:
        if error.record:
            _write_failed_job(Path(args.out), error.record)
        unresolved = list((error.record or {}).get("unresolved") or [])
        sys.stderr.write("{0}\n".format(error))
        for item in unresolved[:5]:
            if isinstance(item, dict):
                sys.stderr.write("  - {0}: {1}\n".format(item.get("path"), item.get("reason")))
        return 1
    sys.stdout.write(json.dumps({
        "status": result.record.get("status"),
        "out": str(result.output_dir),
        "project_id": result.manifest.get("project_id"),
        "variant": result.manifest.get("variant"),
    }, indent=2) + "\n")
    sys.stdout.write("Wrote {0}\n".format(result.output_dir))
    return 0


def _write_failed_job(output_dir: Path, record: dict) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "job.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
    except OSError:
        return


if __name__ == "__main__":
    sys.exit(main())
