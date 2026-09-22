"""Bootstrap sealed empty four-IR bases for LLM proposals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping

from srtp.asset_ir_v2 import ASSET_IR_VERSION, new_asset_ir, seal_asset_ir
from srtp.input_ir_v2 import INPUT_IR_VERSION, new_input_ir, seal_input_ir
from srtp.ir_v2 import RULE_IR_VERSION, new_rule_ir, seal_rule_ir
from srtp.scene_ir_v2 import SCENE_IR_VERSION, new_scene_ir, seal_scene_ir


_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class BootstrapDocuments:
    project_id: str
    source_package_hash: str
    documents: Dict[str, Dict[str, Any]]

    def base_pins(self) -> Dict[str, Dict[str, Any]]:
        return {
            key: {
                "document_id": doc["document_id"],
                "revision": int(doc["revision"]),
                "content_hash": str(doc["content_hash"]),
                "ir_version": str(doc["ir_version"]),
            }
            for key, doc in self.documents.items()
        }


def slugify(value: str, *, fallback: str = "game") -> str:
    text = _SLUG.sub(".", (value or "").strip().lower()).strip(".")
    if not text or not text[0].isalpha():
        text = "{0}.{1}".format(fallback, text or "untitled")
    parts = [part for part in text.split(".") if part]
    cleaned = []
    for part in parts:
        token = "".join(ch for ch in part if ch.isalnum())
        if token:
            cleaned.append(token)
    if not cleaned:
        return fallback
    return ".".join(cleaned)[:80]


def bootstrap_documents(
    *,
    title: str,
    source_package_hash: str,
) -> BootstrapDocuments:
    slug = slugify(title)
    project_id = "project:{0}.llm.source".format(slug)
    rule = new_rule_ir("rule:game.{0}.source".format(slug), title=title)
    scene = new_scene_ir("scene:game.{0}.source".format(slug), title=title)
    asset = new_asset_ir("asset:game.{0}.source".format(slug), title=title)
    input_ir = new_input_ir("input:game.{0}.source".format(slug), title=title)

    for document in (rule, scene, asset, input_ir):
        metadata = document.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["source_project_hash"] = source_package_hash
            metadata["description"] = (
                "LLM compiler bootstrap shell. Semantics must be filled by evidence-backed patches."
            )

    sealed = {
        "rule_ir": seal_rule_ir(rule, revision=0),
        "scene_ir": seal_scene_ir(scene, revision=0),
        "asset_ir": seal_asset_ir(asset, revision=0),
        "input_ir": seal_input_ir(input_ir, revision=0),
    }
    # Attach versions for pin helpers (already present on documents).
    assert sealed["rule_ir"]["ir_version"] == RULE_IR_VERSION
    assert sealed["scene_ir"]["ir_version"] == SCENE_IR_VERSION
    assert sealed["asset_ir"]["ir_version"] == ASSET_IR_VERSION
    assert sealed["input_ir"]["ir_version"] == INPUT_IR_VERSION
    return BootstrapDocuments(
        project_id=project_id,
        source_package_hash=source_package_hash,
        documents=sealed,
    )


def document_pin(document: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "document_id": str(document["document_id"]),
        "ir_version": str(document["ir_version"]),
        "content_hash": str(document["content_hash"]),
    }
