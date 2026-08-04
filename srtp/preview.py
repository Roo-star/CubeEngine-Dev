"""Open a supported source rule file directly in the unified Ursina viewer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser import parse_rule_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a rule source and open its proven subset in Ursina")
    parser.add_argument("--source-file", required=True, help="Generic/canonical JSON or Python rule source")
    args = parser.parse_args()
    report = parse_rule_file(Path(args.source_file))
    if not report.previewable:
        raise SystemExit("SRTP preview blocked:\n{0}".format(report.diagnostics_text()))
    sys.argv = ["stal.ursina_viewer", "--rule-json", json.dumps(report.schema, ensure_ascii=False)]
    from stal.ursina_viewer import main as ursina_main

    ursina_main()


if __name__ == "__main__":
    main()
