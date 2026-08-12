"""Create reversible source-derived variants for genuinely safe parameters."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .source_game import SourceGamePackage, SourceParameter


class SourceVariantBuilder:
    def create(self, package: SourceGamePackage, parameter: SourceParameter, raw_value: Any) -> Path:
        if not parameter.safely_editable:
            raise ValueError("This parameter is not independently safe to edit.")
        if parameter.edit_mode not in ("data_file", "literal_patch"):
            raise ValueError("No variant writer is registered for edit mode {0}.".format(parameter.edit_mode))
        value = _coerce(raw_value, parameter.value_type)
        _validate(value, parameter.constraints)
        if not parameter.locations:
            raise ValueError("The source location for this parameter is unresolved.")
        location = parameter.locations[0]
        source_file = package.root / location.path

        variants_root = (
            package.root.parent
            if package.root.parent.name == ".cubeengine_variants"
            else package.root.parent / ".cubeengine_variants"
        )
        variants_root.mkdir(parents=True, exist_ok=True)
        token = _slug("{0}-{1}-{2}".format(package.entrypoint.stem, parameter.id, value))
        destination = variants_root / token
        suffix = 2
        while destination.exists():
            destination = variants_root / "{0}-{1}".format(token, suffix)
            suffix += 1
        shutil.copytree(str(package.root), str(destination))

        target_file = destination / location.path
        if parameter.edit_mode == "data_file":
            if source_file.suffix.lower() != ".json":
                raise ValueError("The safe data-file writer currently supports JSON values only.")
            data = json.loads(target_file.read_text(encoding="utf-8-sig"))
            key = parameter.id.replace("source_visual_", "", 1)
            if key not in data:
                raise ValueError("The proven JSON key no longer exists in the source copy.")
            data[key] = value
            target_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            _patch_proven_literal(target_file, parameter, value)
        manifest = {
            "variant_version": "cubeengine.srtp/source-variant-v1",
            "source_root": str(package.root),
            "source_entrypoint": str(package.entrypoint),
            "change": {
                "parameter": parameter.id,
                "old_value": parameter.value,
                "new_value": value,
                "source_file": location.path,
            },
        }
        (destination / "cubeengine-variant.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return destination / package.entrypoint.relative_to(package.root)


def _coerce(value: Any, value_type: str) -> Any:
    if value_type in ("int", "integer"):
        if isinstance(value, bool):
            raise ValueError("Boolean is not an integer setting.")
        return int(value)
    if value_type == "float":
        return float(value)
    if value_type == "bool":
        if isinstance(value, bool):
            return value
        token = str(value).strip().lower()
        if token in ("true", "1", "yes", "on"):
            return True
        if token in ("false", "0", "no", "off"):
            return False
        raise ValueError("Expected a boolean value.")
    return str(value)


def _validate(value: Any, constraints: Any) -> None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in constraints and value < constraints["minimum"]:
            raise ValueError("Value is below the proven safe minimum.")
        if "maximum" in constraints and value > constraints["maximum"]:
            raise ValueError("Value is above the proven safe maximum.")


def _patch_proven_literal(path: Path, parameter: SourceParameter, value: Any) -> None:
    """Patch one importer-proven literal, refusing ambiguous source changes."""

    location = parameter.locations[0]
    if not location.line:
        raise ValueError("The proven literal has no source line.")
    lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    index = location.line - 1
    if not 0 <= index < len(lines):
        raise ValueError("The proven source line no longer exists.")
    old = re.escape(str(parameter.value))
    if parameter.id == "source_tick_ms":
        patterns = [re.compile(r"((?:\bontimer|\b(?:pygame\.)?time\.set_timer)\s*\([^,]+,\s*){0}(\s*\))".format(old))]
    elif parameter.id == "source_mine_count":
        patterns = [
            re.compile(r"((?:\brange)\s*\(\s*){0}(\s*\))".format(old)),
            re.compile(r"((?:\bUserInterface|\bBoard)\s*\([^,]+,[^,]+,\s*){0}(\s*[,\)])".format(old)),
            re.compile(r"^(\s*){0}(\s*,\s*(?:#.*)?(?:\r?\n)?)$".format(old)),
        ]
    else:
        raise ValueError("No literal patch contract exists for {0}.".format(parameter.id))
    replaced, count = lines[index], 0
    for pattern in patterns:
        replaced, count = pattern.subn(r"\g<1>{0}\g<2>".format(value), lines[index])
        if count == 1:
            break
    if count != 1:
        raise ValueError("The proven literal changed or became ambiguous; no source file was modified.")
    lines[index] = replaced
    path.write_text("".join(lines), encoding="utf-8")


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-")[:90] or "variant"
