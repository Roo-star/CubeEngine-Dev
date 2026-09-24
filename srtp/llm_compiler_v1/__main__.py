"""CLI entry: python -m srtp.llm_compiler_v1 --source <path> --out <dir>."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from srtp.llm_compiler_v1.agentic import (
    DEFAULT_MAX_LLM_CALLS,
    DEFAULT_REPAIRS_PER_IR,
    AgenticSourceToIRCompiler,
)
from srtp.llm_compiler_v1.client import LLMTransportError
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.llm_compiler_v1.env import load_compiler_env


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="CubeEngine LLM Source-to-IR compiler (OpenRouter Responses backend)",
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
        "--max-repairs", type=int, default=None,
        help="Maximum validator-guided repair attempts (default: 2; agentic: 3 per IR)",
    )
    parser.add_argument(
        "--agentic", action="store_true",
        help=(
            "Agentic workflow: investigate -> per-IR draft -> validate -> runtime probe -> "
            "review, with targeted repairs. With --lift-from/--intent: Design Intent -> lift "
            "plan -> Target IR with Z=1 equivalence and planner behaviour tests"
        ),
    )
    parser.add_argument(
        "--max-llm-calls", type=int, default=DEFAULT_MAX_LLM_CALLS,
        help="Agentic mode: total LLM call budget (default: {0})".format(DEFAULT_MAX_LLM_CALLS),
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Agentic mode: continue from <out>/agent_state.json (accepted stages are kept)",
    )
    parser.add_argument(
        "--title", default="",
        help="Agentic mode: project title override (default: importer-derived)",
    )
    args = parser.parse_args(argv)
    load_compiler_env(override=True)

    lift_from = str(args.lift_from or "").strip()
    intent = str(args.intent or "").strip()

    if args.agentic:
        if intent and not lift_from:
            parser.error("--agentic with --intent needs --lift-from <approved Source bundle>")
        agentic = AgenticSourceToIRCompiler(
            max_llm_calls=args.max_llm_calls,
            max_repairs_per_ir=(
                DEFAULT_REPAIRS_PER_IR if args.max_repairs is None else args.max_repairs
            ),
        )
        if lift_from and not intent:
            parser.error("--lift-from requires --intent")
        try:
            if lift_from:
                report = agentic.compile_lift_path(
                    Path(args.source), source_bundle_dir=Path(lift_from), intent_text=intent,
                    out_dir=Path(args.out), title=args.title or None, language=args.language,
                    resume=args.resume,
                )
            else:
                report = agentic.compile_path(
                    Path(args.source), out_dir=Path(args.out), title=args.title or None,
                    resume=args.resume,
                )
        except LLMTransportError as error:
            # Configuration failures (missing key, bad model ID) before any request.
            print("LLM transport error: {0}".format(error), file=sys.stderr)
            return 2
        print(json.dumps(report.to_mapping(), ensure_ascii=False, indent=2))
        return 0 if report.ok else 1

    compiler = SourceToIRCompiler(max_repairs=2 if args.max_repairs is None else args.max_repairs)

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
