"""Portable, hash-verified originals for sealed Project bundles."""
import hashlib
import json
from pathlib import Path

from .runtime_assets import resource_relative_path, resolve_resource


def asset_root(root):
    root = Path(root).resolve()
    return root / 'resources' if (root / 'asset.resources.json').is_file() else root


def copy_bundle_assets(document, source_root, destination):
    destination = Path(destination)
    resources = destination / 'resources'
    resources.mkdir()
    root = asset_root(source_root)
    records = []
    for asset in document.get('assets', []):
        source = asset.get('source')
        if not source:
            continue
        relative = resource_relative_path(source['uri'])
        path = resolve_resource(root, source['uri'])
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != source['content_hash'] or len(payload) != source['byte_size']:
            raise ValueError('Source asset changed before publication: ' + source['uri'])
        target = resources / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        records.append({'uri': source['uri'], 'content_hash': digest, 'byte_size': len(payload)})
    (destination / 'asset.resources.json').write_text(
        json.dumps({'version': 1, 'assets': records}, indent=2) + '\n', encoding='utf-8')
