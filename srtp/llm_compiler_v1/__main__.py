"""CLI entry: python -m srtp.llm_compiler_v1 --source <path> --out <dir>."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.llm_compiler_v1.env import load_compiler_env


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="CubeEngine LLM Source-to-IR compiler (freeflow-llm backend)",
    )
    parser.add_argument(
        "--source", required=True,
        help="Source game file or project directory",
    )
    parser.add_argument(
        "--out", required=True,
        help="Output directory for proposal, IR documents and manifest",
    )
    parser.add_argument(
        "--lift-from",
        default="",
        help=(
            "Approved Source bundle directory. Runs Spatial Lift into --out. "
            "Requires --intent. Preferred two-phase path after designer approval."
        ),
    )
    parser.add_argument(
        "--intent", default="",
        help="Natural-language Design Intent for Spatial Lift",
    )
    parser.add_argument(
        "--language", default="en",
        help="Language tag for Design Intent (default: en)",
    )
    parser.add_argument(
        "--max-repairs", type=int, default=2,
        help="Maximum validator-guided repair attempts (default: 2)",
    )
    args = parser.parse_args(argv)
    load_compiler_env(override=True)

    compiler = SourceToIRCompiler(max_repairs=args.max_repairs)
    lift_from = str(args.lift_from or "").strip()
    intent = str(args.intent or "").strip()

    if lift_from:
        if not intent:
            parser.error("--lift-from requires --intent")
        report = compiler.compile_spatial_lift_path(
            Path(args.source),
            source_bundle_dir=Path(lift_from),
            intent_text=intent,
            out_dir=Path(args.out),
            language=args.language,
        )
    else:
        # Source four-IR only by default. Passing --intent without --lift-from
        # still attempts lift but will block until the source is compile_ready.
        report = compiler.compile_path(
            Path(args.source),
            out_dir=Path(args.out),
            intent_text=intent or None,
            language=args.language,
        )

    print(json.dumps(report.to_mapping(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
