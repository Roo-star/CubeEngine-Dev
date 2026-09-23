"""Explicit, allowlisted dependency resources; never execute imported game code."""
import ast
import hashlib
from importlib import metadata
from pathlib import Path, PurePosixPath

PYGAME_FONT_URI = 'runtime://pygame/default-font'
RUNTIME_PATHS = {PYGAME_FONT_URI: PurePosixPath('_runtime/pygame/default-font.ttf')}


def resource_relative_path(uri):
    from srtp.asset_ir_v2.asset_ir import project_uri_relative_path
    if not isinstance(uri, str):
        raise ValueError('Asset source URI must be a string')
    if uri in RUNTIME_PATHS:
        return RUNTIME_PATHS[uri]
    from .system_fonts import font_request
    if font_request(uri):
        return PurePosixPath('_runtime/pygame/sysfont/'+hashlib.sha256(uri.encode()).hexdigest()+'.font')
    return project_uri_relative_path(uri)


def pygame_default_font():
    # Read installed distribution data, not a game-local import named pygame.
    try:
        path = Path(metadata.distribution('pygame').locate_file('pygame/freesansbold.ttf')).resolve(strict=True)
    except (metadata.PackageNotFoundError, OSError) as error:
        raise ValueError('Required pygame default font is unavailable locally. Install the project pygame dependency before calling the LLM.') from error
    if not path.is_file():
        raise ValueError('Required pygame default font is not a file')
    return path


def resolve_resource(root, uri):
    """Runtime bytes come from a sealed bundle, otherwise a fixed dependency."""
    root = Path(root).resolve()
    relative = resource_relative_path(uri)
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError('Asset source escapes project root') from error
    bundled = (root.parent / 'asset.resources.json').is_file() and root.name == 'resources'
    if uri in RUNTIME_PATHS and not bundled:
        return pygame_default_font()
    from .system_fonts import font_request, system_font_path
    if font_request(uri) and not bundled:
        return system_font_path(uri)
    # A damaged portable bundle must not fall back to the machine's copy.
    if not path.is_file():
        raise ValueError('Source asset does not exist: ' + uri)
    return path


def discover_runtime_assets(files):
    """Recognize Font(None, ...) statically, including normal import aliases."""
    references = []
    for relative, path in files.items():
        if path.suffix.lower() != '.py':
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        except (SyntaxError, UnicodeError):
            continue
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name.split('.')[0]] = item.name if item.asname else item.name.split('.')[0]
            elif isinstance(node, ast.ImportFrom) and node.module:
                for item in node.names:
                    aliases[item.asname or item.name] = node.module + '.' + item.name
        def qualified(node):
            if isinstance(node, ast.Name):
                return aliases.get(node.id, node.id)
            if isinstance(node, ast.Attribute):
                return qualified(node.value) + '.' + node.attr
            return ''
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or qualified(node.func) != 'pygame.font.Font':
                continue
            argument = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == 'file'), None)
            if not isinstance(argument, ast.Constant) or argument.value is not None:
                continue
            references.append({'path':relative, 'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                               'span':{'line_start':node.lineno,'line_end':node.end_lineno}})
    from .system_fonts import discover_system_fonts
    assets=discover_system_fonts(files)
    if not references:
        return assets
    path = pygame_default_font()
    payload = path.read_bytes()
    asset = {'id':'asset:font.pygame_default', 'name':'Pygame bundled default font',
        'kind':'font','media_type':'font/ttf',
        'source':{'uri':PYGAME_FONT_URI,'content_hash':hashlib.sha256(payload).hexdigest(),'byte_size':len(payload)},
        'license':{'spdx_id':'LicenseRef-Pygame-Bundled-Font','attribution':'Pygame bundled freesansbold.ttf',
                   'source_uri':None,'redistribution':'unknown'},
        'importer':{'capability':'cubeengine.raw-file','version':'1.0','settings':{}},
        'metadata':{'dependency':'pygame','version':metadata.version('pygame'),'source_references':references}}
    return [asset]+assets
