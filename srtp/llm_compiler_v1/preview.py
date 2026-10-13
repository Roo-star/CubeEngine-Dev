"""Offline preview of the next request a staged Compile would send; nothing goes to the network.

  python -m srtp.llm_compiler_v1.preview --source <game> [--checkpoint <source.stages.json>]
         [--expect swipe,background_click] [--save request.json]

The compiler runs with a capturing transport that records the first model
request and stops. With --checkpoint, a COPY of the Workbench stage checkpoint
is used, so already paid stages are revalidated locally and the preview shows
the first stage that would actually be paid for; the original file is never
modified. Use it to confirm which code and contract a paid run would use, that
the prompt carries a capability, and how large each part of the request is.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def preview(source: Path, *, checkpoint: Optional[Path] = None) -> Dict[str, Any]:
    from srtp.source_importer import SourceGameImporter

    from .client import LLMTransportError
    from .compiler import SourceToIRCompiler
    from .provenance import provenance

    package = SourceGameImporter().import_path(Path(source))
    captured: List[List[Dict[str, str]]] = []

    def capture(**kwargs: Any) -> Any:
        captured.append([dict(item) for item in kwargs["messages"]])
        raise LLMTransportError("offline preview: request captured, nothing was sent")

    with tempfile.TemporaryDirectory() as folder:
        copy = None
        if checkpoint is not None:
            copy = Path(folder) / "source.stages.json"
            shutil.copyfile(checkpoint, copy)
        SourceToIRCompiler(chat_fn=capture).compile(package, out_dir=Path(folder) / "out", checkpoint_path=copy)
    result: Dict[str, Any] = {"provenance": provenance()}
    if not captured:
        return dict(result, stage=None, reason="no model request: every stage was satisfied without one")
    messages = captured[0]
    try:
        payload = json.loads(messages[-1]["content"])
    except ValueError:
        payload = {}
    fields = sorted(((key, len(json.dumps(value, ensure_ascii=False))) for key, value in payload.items()),
                    key=lambda item: -item[1])
    characters = sum(len(item.get("content") or "") for item in messages)
    return dict(result, stage=payload.get("stage") or payload.get("task"), messages=messages,
                characters=characters, approx_input_tokens=characters // 4,
                system_characters=len(messages[0].get("content") or ""), largest_payload_fields=fields[:10])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", required=True, type=Path, help="Source game file or project directory")
    parser.add_argument("--checkpoint", type=Path, help="Workbench source.stages.json to start from (copied)")
    parser.add_argument("--expect", default="", help="Comma-separated words the request must contain")
    parser.add_argument("--save", type=Path, help="Write the captured messages to this JSON file")
    args = parser.parse_args(argv)
    report = preview(args.source, checkpoint=args.checkpoint)
    from .provenance import describe
    print(describe(report["provenance"]))
    if report["stage"] is None:
        print(report["reason"])
        return 0
    print("next paid stage: {0}; ~{1} input tokens ({2} characters, system prompt {3})".format(
        report["stage"], report["approx_input_tokens"], report["characters"], report["system_characters"]))
    print("largest payload fields: " + ", ".join("{0}={1}".format(k, v) for k, v in report["largest_payload_fields"]))
    text = "\n".join(item.get("content") or "" for item in report["messages"])
    missing = [word for word in (w.strip() for w in args.expect.split(",")) if word and word not in text]
    for word in (w.strip() for w in args.expect.split(",")):
        if word:
            print("{0}: {1}".format(word, "present" if word not in missing else "MISSING"))
    if args.save:
        args.save.write_text(json.dumps(report["messages"], ensure_ascii=False, indent=2), encoding="utf-8")
        print("saved " + str(args.save))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
