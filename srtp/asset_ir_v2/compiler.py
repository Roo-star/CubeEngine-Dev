"""Secure, deterministic Asset IR importer and derivation compiler."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .asset_ir import (
    ASSET_COMPILER_CAPABILITIES,
    ASSET_COMPILER_CAPABILITY_ID,
    canonical_asset_ir_hash,
    is_asset_ir_compile_ready,
    project_uri_relative_path,
    validate_asset_ir,
)


class AssetCompileError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class LicenseRecord:
    spdx_id: str
    attribution: str
    source_uri: Optional[str]
    redistribution: str

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "spdx_id": self.spdx_id,
            "attribution": self.attribution,
            "source_uri": self.source_uri,
            "redistribution": self.redistribution,
        }


@dataclass(frozen=True)
class CompiledResource:
    id: str
    name: str
    kind: str
    media_type: str
    content_hash: str
    byte_size: int
    cache_uri: str
    source_uri: Optional[str]
    derived_from: Tuple[str, ...]
    derivation_strategy: Optional[str]
    licenses: Tuple[LicenseRecord, ...]
    metadata: Mapping[str, Any]
    _payload: bytes = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "derived_from", tuple(self.derived_from))
        object.__setattr__(self, "licenses", tuple(self.licenses))
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "media_type": self.media_type,
            "content_hash": self.content_hash,
            "byte_size": self.byte_size,
            "cache_uri": self.cache_uri,
            "source_uri": self.source_uri,
            "derived_from": list(self.derived_from),
            "derivation_strategy": self.derivation_strategy,
            "licenses": [item.to_mapping() for item in self.licenses],
            "metadata": _thaw(self.metadata),
        }


@dataclass(frozen=True)
class CompiledRole:
    id: str
    semantic: str
    resource_id: str
    usage: str
    required: bool

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "semantic": self.semantic,
            "resource": self.resource_id,
            "usage": self.usage,
            "required": self.required,
        }


@dataclass(frozen=True)
class CompiledPresentationMapping:
    id: str
    source_role: str
    target_resource: str
    strategy: str
    fidelity: str
    settings: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", _freeze(self.settings))

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_role": self.source_role,
            "target_resource": self.target_resource,
            "strategy": self.strategy,
            "fidelity": self.fidelity,
            "settings": _thaw(self.settings),
        }


@dataclass(frozen=True)
class CompiledAssetCatalog:
    document_id: str
    document_hash: str
    compiler_capability: str
    bundle_hash: str
    resources_by_id: Mapping[str, CompiledResource]
    roles_by_id: Mapping[str, CompiledRole]
    roles_by_semantic: Mapping[str, CompiledRole]
    mappings_by_id: Mapping[str, CompiledPresentationMapping]
    distribution_ready: bool
    warnings: Tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "resources_by_id", MappingProxyType(dict(self.resources_by_id)))
        object.__setattr__(self, "roles_by_id", MappingProxyType(dict(self.roles_by_id)))
        object.__setattr__(self, "roles_by_semantic", MappingProxyType(dict(self.roles_by_semantic)))
        object.__setattr__(self, "mappings_by_id", MappingProxyType(dict(self.mappings_by_id)))
        object.__setattr__(self, "warnings", tuple(self.warnings))

    def resource(self, identifier: str) -> CompiledResource:
        try:
            return self.resources_by_id[identifier]
        except KeyError as exc:
            raise AssetCompileError("unknown compiled asset resource: {0}".format(identifier)) from exc

    def role(self, identifier_or_semantic: str) -> CompiledRole:
        value = self.roles_by_id.get(identifier_or_semantic) or self.roles_by_semantic.get(identifier_or_semantic)
        if value is None:
            raise AssetCompileError("unknown semantic asset role: {0}".format(identifier_or_semantic))
        return value

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "document_hash": self.document_hash,
            "compiler_capability": self.compiler_capability,
            "bundle_hash": self.bundle_hash,
            "distribution_ready": self.distribution_ready,
            "warnings": list(self.warnings),
            "resources": [self.resources_by_id[key].to_mapping() for key in sorted(self.resources_by_id)],
            "roles": [self.roles_by_id[key].to_mapping() for key in sorted(self.roles_by_id)],
            "presentation_mappings": [self.mappings_by_id[key].to_mapping() for key in sorted(self.mappings_by_id)],
        }

    def materialize(self, cache_root: Path) -> Dict[str, Path]:
        """Write immutable content-addressed artifacts and return their paths.

        Existing files are reused only when their SHA-256 matches.  An existing
        mismatch is never overwritten, which prevents cache-path corruption.
        """

        root = Path(cache_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        result: Dict[str, Path] = {}
        for identifier in sorted(self.resources_by_id):
            resource = self.resources_by_id[identifier]
            extension = _media_extension(resource.media_type)
            directory = root / "sha256" / resource.content_hash[:2]
            directory.mkdir(parents=True, exist_ok=True)
            resolved_directory = directory.resolve()
            if not _is_relative_to(resolved_directory, root):
                raise AssetCompileError("cache directory escaped the requested cache root")
            target = resolved_directory / (resource.content_hash + extension)
            if target.exists():
                if target.is_symlink() or not target.is_file() or _sha256_path(target) != resource.content_hash:
                    raise AssetCompileError("content-addressed cache collision at {0}".format(target))
            else:
                handle = tempfile.NamedTemporaryFile(
                    mode="wb", prefix=resource.content_hash + ".", suffix=".tmp",
                    dir=str(resolved_directory), delete=False,
                )
                temporary = Path(handle.name)
                try:
                    with handle:
                        handle.write(resource._payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(str(temporary), str(target))
                finally:
                    if temporary.exists():
                        temporary.unlink()
            result[identifier] = target
        return result


def compile_asset_ir(
    document: Mapping[str, Any], project_root: Path, *,
    max_asset_bytes: Optional[int] = None,
    max_total_bytes: Optional[int] = None,
    max_image_pixels: Optional[int] = None,
) -> CompiledAssetCatalog:
    """Verify source bytes, compile deterministic derivatives and bind roles."""

    diagnostics = validate_asset_ir(document)
    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        first = errors[0]
        raise AssetCompileError("Asset IR is invalid at {0}: {1}".format(first.path, first.message))
    if not is_asset_ir_compile_ready(document):
        raise AssetCompileError("Asset IR has required unresolved semantics or no resources")
    expected_document_hash = canonical_asset_ir_hash(document)
    if document.get("content_hash") != expected_document_hash:
        raise AssetCompileError("Asset IR must be sealed with its canonical content hash")

    limits = ASSET_COMPILER_CAPABILITIES["default_limits"]
    asset_limit = _positive_limit(max_asset_bytes, int(limits["max_asset_bytes"]), "max_asset_bytes")
    total_limit = _positive_limit(max_total_bytes, int(limits["max_total_bytes"]), "max_total_bytes")
    pixel_limit = _positive_limit(max_image_pixels, int(limits["max_image_pixels"]), "max_image_pixels")

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise AssetCompileError("project root is not an existing directory")

    resources: Dict[str, CompiledResource] = {}
    total = 0
    for asset in document.get("assets", []):
        resource = _import_source_asset(asset, root, asset_limit, pixel_limit)
        total += resource.byte_size
        if total > total_limit:
            raise AssetCompileError("source assets exceed the total byte limit")
        resources[resource.id] = resource

    pending = list(document.get("derivations", []))
    while pending:
        progressed = False
        next_pending = []
        for derivation in pending:
            if all(input_id in resources for input_id in derivation.get("inputs", [])):
                resource = _compile_derivation(derivation, resources, pixel_limit)
                total += resource.byte_size
                if resource.byte_size > asset_limit or total > total_limit:
                    raise AssetCompileError("compiled assets exceed declared byte limits")
                resources[resource.id] = resource
                progressed = True
            else:
                next_pending.append(derivation)
        if not progressed:
            missing = sorted({
                input_id for item in next_pending for input_id in item.get("inputs", [])
                if input_id not in resources
            })
            raise AssetCompileError("derived asset dependency graph cannot resolve: {0}".format(", ".join(missing)))
        pending = next_pending

    roles_by_id: Dict[str, CompiledRole] = {}
    roles_by_semantic: Dict[str, CompiledRole] = {}
    for item in document.get("roles", []):
        role = CompiledRole(
            id=str(item["id"]), semantic=str(item["semantic"]),
            resource_id=str(item["resource"]), usage=str(item["usage"]),
            required=bool(item["required"]),
        )
        if role.resource_id not in resources:
            raise AssetCompileError("role references missing compiled resource: {0}".format(role.resource_id))
        roles_by_id[role.id] = role
        roles_by_semantic[role.semantic] = role

    mappings: Dict[str, CompiledPresentationMapping] = {}
    for item in document.get("presentation_mappings", []):
        mapping = CompiledPresentationMapping(
            id=str(item["id"]), source_role=str(item["source_role"]),
            target_resource=str(item["target_resource"]), strategy=str(item["strategy"]),
            fidelity=str(item["fidelity"]), settings=dict(item["settings"]),
        )
        if mapping.source_role not in roles_by_id or mapping.target_resource not in resources:
            raise AssetCompileError("presentation mapping has an unresolved role or resource")
        mappings[mapping.id] = mapping

    warnings_list = [item.message for item in diagnostics if item.severity == "warning"]
    distribution_ready = all(
        license_record.redistribution == "allowed" and license_record.spdx_id != "NOASSERTION"
        for resource in resources.values() for license_record in resource.licenses
    )
    manifest_resources = [resources[key].to_mapping() for key in sorted(resources)]
    bundle_payload = json.dumps(
        manifest_resources, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    bundle_hash = hashlib.sha256(bundle_payload).hexdigest()
    return CompiledAssetCatalog(
        document_id=str(document["document_id"]),
        document_hash=expected_document_hash,
        compiler_capability=ASSET_COMPILER_CAPABILITY_ID,
        bundle_hash=bundle_hash,
        resources_by_id=dict(resources),
        roles_by_id=roles_by_id,
        roles_by_semantic=roles_by_semantic,
        mappings_by_id=mappings,
        distribution_ready=distribution_ready,
        warnings=tuple(warnings_list),
    )


def _import_source_asset(
    asset: Mapping[str, Any], root: Path, max_bytes: int, max_image_pixels: int,
) -> CompiledResource:
    source = asset["source"]
    relative = project_uri_relative_path(str(source["uri"]))
    candidate = root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise AssetCompileError("source asset does not exist: {0}".format(source["uri"])) from exc
    if not _is_relative_to(resolved, root) or not resolved.is_file():
        raise AssetCompileError("source asset escapes project root or is not a file: {0}".format(source["uri"]))
    size = resolved.stat().st_size
    if size > max_bytes:
        raise AssetCompileError("source asset exceeds byte limit: {0}".format(source["uri"]))
    if size != int(source["byte_size"]):
        raise AssetCompileError("source byte size changed: {0}".format(source["uri"]))
    payload = resolved.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != source["content_hash"]:
        raise AssetCompileError("source content hash changed: {0}".format(source["uri"]))

    importer = asset["importer"]
    capability = importer["capability"]
    metadata = dict(asset.get("metadata", {}))
    metadata["importer"] = {
        "capability": capability,
        "version": importer["version"],
        "settings": dict(importer.get("settings", {})),
    }
    if capability == "cubeengine.image":
        if not str(asset["media_type"]).startswith("image/"):
            raise AssetCompileError("image importer requires an image media type")
        metadata.update(_inspect_image(payload, max_image_pixels))
    elif capability == "cubeengine.json":
        if asset["media_type"] not in ("application/json", "text/json"):
            raise AssetCompileError("JSON importer requires application/json or text/json")
        try:
            json.loads(
                payload.decode("utf-8"),
                parse_constant=lambda token: _reject_json_constant(token),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise AssetCompileError("source JSON cannot be decoded safely: {0}".format(source["uri"])) from exc

    license_record = _license_from_mapping(asset["license"])
    return CompiledResource(
        id=str(asset["id"]), name=str(asset["name"]), kind=str(asset["kind"]),
        media_type=str(asset["media_type"]), content_hash=digest, byte_size=size,
        cache_uri=_cache_uri(digest, str(asset["media_type"])),
        source_uri=str(source["uri"]), derived_from=(), derivation_strategy=None,
        licenses=(license_record,), metadata=metadata, _payload=payload,
    )


def _compile_derivation(
    item: Mapping[str, Any], resources: Mapping[str, CompiledResource], max_image_pixels: int,
) -> CompiledResource:
    strategy = str(item["strategy"])
    inputs = [resources[identifier] for identifier in item.get("inputs", [])]
    settings = dict(item.get("settings", {}))
    output_media = str(item["media_type"])
    output_kind = str(item["kind"])
    metadata: Dict[str, Any] = {
        "recipe": {
            "strategy": strategy,
            "inputs": [resource.id for resource in inputs],
            "settings": settings,
            "compiler": ASSET_COMPILER_CAPABILITY_ID,
        }
    }

    if strategy == "identity":
        source = inputs[0]
        if output_media != source.media_type or output_kind != source.kind:
            raise AssetCompileError("identity derivation must preserve kind and media type")
        payload = source._payload
    elif strategy == "atlas_region":
        if output_media != "image/png" or output_kind != "image":
            raise AssetCompileError("atlas_region output must be an image/png image")
        payload, image_metadata = _crop_atlas(inputs[0], settings, max_image_pixels)
        metadata.update(image_metadata)
    elif strategy in (
        "billboard", "extrusion", "cube_face_projection",
        "mesh_substitution", "procedural_mesh",
    ):
        if output_media != ASSET_COMPILER_CAPABILITIES["compiled_descriptor_media_type"]:
            raise AssetCompileError("presentation derivation must emit the CubeEngine descriptor media type")
        _validate_presentation_recipe(strategy, inputs, settings)
        descriptor = {
            "format": "cubeengine.presentation-descriptor/1",
            "strategy": strategy,
            "inputs": [
                {"id": resource.id, "content_hash": resource.content_hash, "media_type": resource.media_type}
                for resource in inputs
            ],
            "settings": settings,
        }
        payload = json.dumps(
            descriptor, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    elif strategy == "custom_renderer":
        raise AssetCompileError(
            "custom_renderer requires the later Extension Adapter SDK; arbitrary renderer code is never executed by Asset IR"
        )
    else:
        raise AssetCompileError("unsupported derivation strategy: {0}".format(strategy))

    digest = hashlib.sha256(payload).hexdigest()
    expected = str(item.get("expected_content_hash", ""))
    if expected and digest != expected:
        raise AssetCompileError("derived asset hash does not match expected content: {0}".format(item["id"]))
    licenses = _merge_licenses(inputs)
    if not licenses:
        licenses = (LicenseRecord(
            "LicenseRef-CubeEngine-Generated", "Generated deterministically by CubeEngine",
            None, "allowed",
        ),)
    return CompiledResource(
        id=str(item["id"]), name=str(item["name"]), kind=output_kind,
        media_type=output_media, content_hash=digest, byte_size=len(payload),
        cache_uri=_cache_uri(digest, output_media), source_uri=None,
        derived_from=tuple(resource.id for resource in inputs),
        derivation_strategy=strategy, licenses=licenses, metadata=metadata,
        _payload=payload,
    )


def _inspect_image(payload: bytes, max_pixels: int) -> Dict[str, Any]:
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise AssetCompileError("cubeengine.image importer requires Pillow") from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                width, height = image.size
                mode, format_name = image.mode, image.format
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise AssetCompileError("image dimensions exceed the configured pixel limit")
                image.verify()
    except AssetCompileError:
        raise
    except (OSError, ValueError, Image.DecompressionBombWarning) as exc:
        raise AssetCompileError("source image failed safe decoding") from exc
    return {"width": width, "height": height, "mode": mode, "format": format_name}


def _crop_atlas(
    source: CompiledResource, settings: Mapping[str, Any], max_pixels: int,
) -> Tuple[bytes, Dict[str, Any]]:
    if not source.media_type.startswith("image/"):
        raise AssetCompileError("atlas_region input must be an image")
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise AssetCompileError("atlas_region derivation requires Pillow") from exc
    x, y = int(settings["x"]), int(settings["y"])
    width, height = int(settings["width"]), int(settings["height"])
    if width * height > max_pixels:
        raise AssetCompileError("atlas region exceeds the configured pixel limit")
    try:
        with Image.open(io.BytesIO(source._payload)) as image:
            if x + width > image.width or y + height > image.height:
                raise AssetCompileError("atlas region lies outside the source image")
            crop = image.convert("RGBA").crop((x, y, x + width, y + height))
            output = io.BytesIO()
            crop.save(output, format="PNG", optimize=False, compress_level=9)
    except AssetCompileError:
        raise
    except (OSError, ValueError) as exc:
        raise AssetCompileError("atlas image could not be decoded") from exc
    return output.getvalue(), {"width": width, "height": height, "mode": "RGBA", "format": "PNG"}


def _validate_presentation_recipe(
    strategy: str, inputs: Sequence[CompiledResource], settings: Mapping[str, Any],
) -> None:
    if strategy == "billboard":
        if settings.get("facing") not in ("camera", "axis", "fixed"):
            raise AssetCompileError("billboard requires explicit facing: camera, axis or fixed")
        if not _positive_vector(settings.get("size"), 2) or not isinstance(settings.get("double_sided"), bool):
            raise AssetCompileError("billboard requires positive 2D size and explicit double_sided")
    elif strategy == "extrusion":
        depth = settings.get("depth")
        if not _positive_number(depth) or settings.get("axis") not in ("x", "y", "z"):
            raise AssetCompileError("extrusion requires positive depth and explicit x/y/z axis")
    elif strategy == "cube_face_projection":
        faces = settings.get("faces")
        valid_faces = {"front", "back", "left", "right", "top", "bottom"}
        if faces != "all" and (
            not isinstance(faces, list) or not faces or len(faces) != len(set(faces))
            or any(face not in valid_faces for face in faces)
        ):
            raise AssetCompileError("cube_face_projection requires faces='all' or an explicit face list")
        if settings.get("uv_policy") not in ("stretch", "contain", "tile"):
            raise AssetCompileError("cube_face_projection requires explicit UV policy")
    elif strategy == "mesh_substitution":
        if not inputs or inputs[0].kind != "model":
            raise AssetCompileError("mesh_substitution requires one source model")
    elif strategy == "procedural_mesh":
        if settings.get("primitive") not in ("cube", "sphere", "cylinder", "plane"):
            raise AssetCompileError("procedural_mesh requires a supported primitive")
        if not _positive_vector(settings.get("dimensions"), 3):
            raise AssetCompileError("procedural_mesh requires three positive dimensions")


def _merge_licenses(inputs: Iterable[CompiledResource]) -> Tuple[LicenseRecord, ...]:
    result = {
        license_record
        for resource in inputs for license_record in resource.licenses
    }
    return tuple(sorted(result))


def _license_from_mapping(value: Mapping[str, Any]) -> LicenseRecord:
    return LicenseRecord(
        spdx_id=str(value["spdx_id"]), attribution=str(value["attribution"]),
        source_uri=str(value["source_uri"]) if value["source_uri"] is not None else None,
        redistribution=str(value["redistribution"]),
    )


def _cache_uri(digest: str, media_type: str) -> str:
    return "cache://sha256/{0}{1}".format(digest, _media_extension(media_type))


def _media_extension(media_type: str) -> str:
    return {
        "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
        "image/webp": ".webp", "audio/wav": ".wav", "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3", "font/ttf": ".ttf", "font/otf": ".otf",
        "model/gltf+json": ".gltf", "model/gltf-binary": ".glb",
        "application/json": ".json", "text/plain": ".txt",
        "application/vnd.cubeengine.presentation+json": ".presentation.json",
    }.get(media_type, ".bin")


def _positive_limit(value: Optional[int], default: int, name: str) -> int:
    result = default if value is None else value
    if isinstance(result, bool) or not isinstance(result, int) or result <= 0:
        raise AssetCompileError("{0} must be a positive integer".format(name))
    return result


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _positive_vector(value: Any, length: int) -> bool:
    return isinstance(value, list) and len(value) == length and all(_positive_number(item) for item in value)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_json_constant(token: str) -> None:
    raise ValueError("non-finite JSON constant: {0}".format(token))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    if isinstance(value, tuple):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value
