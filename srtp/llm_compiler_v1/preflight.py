"""Before a paid compile: which code, prompts and cached stages would this run use?

Makes no model call. Prints (and returns) the running checkout and commit, the
prompt/contract versions, the model settings (never keys), and for the
checkpoint the run would use: whether it matches this source/model/intent, and
which stages would be reused for free, re-validated from a rejected reply, or
generated (paid). Warns when the code is imported from a different checkout
than the current directory, or the working tree has uncommitted changes.

Usage:
    python -m srtp.llm_compiler_v1.preflight --source GAME.py [--out OUT_DIR | --checkpoint FILE] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ORDER = ("rule_ir", "asset_ir", "scene_ir", "input_ir")


def source_preflight(source: Path, *, checkpoint: Optional[Path] = None, max_repairs: Optional[int] = None,
                     load_env: bool = True, client: Any = None) -> Dict[str, Any]:
    from srtp.source_importer import SourceGameImporter
    from .bootstrap import bootstrap_documents
    from .client import OpenRouterLLMClient
    from .compiler import MAX_REPAIR_ATTEMPTS, SourceToIRCompiler
    from .env import load_compiler_env
    from .evidence import build_evidence_pack
    from .provenance import code_fingerprint, contract_fingerprint, model_settings
    from .source_workspace import SourceWorkspace
    from .staged import stage_signatures
    if load_env:
        load_compiler_env(override=True)
    package = SourceGameImporter().import_path(Path(source))
    if client is None:  # the client a Workbench compile would build
        client = OpenRouterLLMClient()
        client.model = os.environ.get("CUBEENGINE_LLM_OPENROUTER_MODEL") or client.model
    compiler = SourceToIRCompiler(client=client)
    evidence = build_evidence_pack(package)
    bootstrap = bootstrap_documents(title=package.title, source_package_hash=str(evidence["source_package_hash"]))
    workspace = SourceWorkspace(Path(package.root), Path(package.entrypoint))
    signatures = stage_signatures(compiler, package, evidence, bootstrap.documents, None, None, workspace)
    code = code_fingerprint()
    warnings: List[str] = []
    cwd_repo = Path(os.getcwd()).resolve()
    if not str(cwd_repo).startswith(code["repo_root"]):
        warnings.append("the current directory {0} is not inside the checkout being imported ({1})".format(
            cwd_repo, code["repo_root"]))
    if code.get("git_dirty_files"):
        warnings.append("{0} uncommitted file(s) in the checkout; record them with the run".format(code["git_dirty_files"]))
    if not os.environ.get("OPENROUTER_API_KEY"):
        warnings.append("OPENROUTER_API_KEY is not set (repository-root .env)")
    cache: Dict[str, Any] = {"checkpoint": str(checkpoint) if checkpoint else None, "exists": False}
    accepted, rejected = {}, {}
    if checkpoint is not None and Path(checkpoint).is_file():
        stored = json.loads(Path(checkpoint).read_text(encoding="utf-8"))
        cache["exists"] = True
        if stored.get("signature") == signatures["signature"]:
            cache["matched_by"] = "signature"
        elif stored.get("input_signature") == signatures["input_signature"]:
            cache["matched_by"] = "input_signature (engine code changed; cached stages are re-validated)"
        else:
            cache["matched_by"] = None
            warnings.append("the checkpoint does not match this source/model/intent: nothing will be reused")
        if cache["matched_by"]:
            accepted, rejected = stored.get("stages") or {}, stored.get("rejected_stages") or {}
        cache["stored_model"] = stored.get("model")
    repairs = MAX_REPAIR_ATTEMPTS if max_repairs is None else int(max_repairs)
    stages = {}
    for slot in ORDER:
        if slot in accepted:
            stages[slot] = "reuse accepted reply (0 calls if it still passes every current gate)"
        elif slot in rejected:
            stages[slot] = "re-validate saved rejected reply locally, then repair (paid if it still fails)"
        else:
            stages[slot] = "generate (paid)"
    paid = [slot for slot in ORDER if slot not in accepted]
    return {"code": code, "contracts": contract_fingerprint(), "model": model_settings(client),
            "source": {"path": str(Path(source).resolve()), "title": package.title,
                       "source_package_hash": evidence["source_package_hash"]},
            "checkpoint": dict(cache, signature=signatures["signature"], input_signature=signatures["input_signature"]),
            "stages": stages,
            "estimate": {"paid_stages": paid, "first_attempt_calls": len(paid),
                         "max_calls_without_rounds": len(paid) * (1 + repairs),
                         "client_request_cap": client.max_requests if hasattr(client, "max_requests") else None,
                         "note": "Upper bounds, not a cost: cost = actual tokens x model price (OpenRouter usage.cost). "
                                 "A source-oracle or upstream round can add at most 2 Rule calls each."},
            "warnings": warnings}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="the output folder a compile would use (checkpoint OUT.stages.json)")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    checkpoint = args.checkpoint or (args.out.with_name(args.out.name + ".stages.json") if args.out else None)
    report = source_preflight(args.source, checkpoint=checkpoint)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    code = report["code"]
    print("code      {0} @ {1} ({2} dirty file(s)); imported from {3}".format(
        code["git_branch"], (code["git_commit"] or "?")[:12], code["git_dirty_files"], code["srtp_imported_from"]))
    contracts = report["contracts"]
    print("prompts   staged SYSTEM {0}..., backend profile {1}, template {2}".format(
        contracts["staged_system_prompt_sha256"][:12], contracts["backend_profile_version"],
        contracts["prompt_template_version"]))
    model = report["model"]
    print("model     {0} (reasoning {1}, max tokens {2}, request cap {3})".format(
        model["model"], model["reasoning_effort"], model["max_tokens"], model["max_requests"]))
    cache = report["checkpoint"]
    print("cache     {0}: {1}".format(cache["checkpoint"], ("matched by " + cache["matched_by"]) if cache.get("matched_by")
                                       else ("present but not matching" if cache["exists"] else "none")))
    for slot, plan in report["stages"].items():
        print("  {0:<9} {1}".format(slot, plan))
    estimate = report["estimate"]
    print("estimate  first attempts {0} call(s); at most {1} without oracle/upstream rounds".format(
        estimate["first_attempt_calls"], estimate["max_calls_without_rounds"]))
    for item in report["warnings"]:
        print("WARNING   " + item)
    return 0


if __name__ == "__main__":
    sys.exit(main())
