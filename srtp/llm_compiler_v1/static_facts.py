"""Engine-owned facts measured from the source, locked before and after generation.

Resource references (shipped images/fonts/audio, runtime fonts, pygame.draw
pictures) and application lifecycle controls (quit/restart keys) can be read
from the source without executing it. Models used to re-author them and often
dropped or mis-typed them. Here they are measured once, seeded into the base
documents the model sees, and restored after every model patch, so the model
only references them.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

APPLICATION_CONTEXT = "input:context.application"
_MEDIA = {
    ".png": ("image", "image/png"), ".jpg": ("image", "image/jpeg"), ".jpeg": ("image", "image/jpeg"),
    ".gif": ("image", "image/gif"), ".bmp": ("image", "image/bmp"), ".webp": ("image", "image/webp"),
    ".ttf": ("font", "font/ttf"), ".otf": ("font", "font/otf"),
    ".wav": ("audio", "audio/wav"), ".ogg": ("audio", "audio/ogg"), ".mp3": ("audio", "audio/mpeg"),
}
_KEY_NAMES = {
    "ESCAPE": "escape", "RETURN": "enter", "KP_ENTER": "enter", "SPACE": "space", "TAB": "tab",
    "BACKSPACE": "backspace", "UP": "arrow_up", "DOWN": "arrow_down", "LEFT": "arrow_left",
    "RIGHT": "arrow_right", "HOME": "home", "END": "end", "DELETE": "delete", "INSERT": "insert",
    "PAGEUP": "page_up", "PAGEDOWN": "page_down",
}
_EXIT_CALLS = {"pygame.quit", "sys.exit", "quit", "exit", "os._exit", "pygame.display.quit"}
_PROCESSING = {"dead_zone": 0, "sensitivity_numerator": 1, "sensitivity_denominator": 1, "invert": False,
               "clamp_min": -32768, "clamp_max": 32767}
LOCKED_NOTE = (
    "Engine-locked static facts: the assets, derivations and application host_command bindings already in the "
    "base document were measured from the source. Reference their ids; never redeclare, rename or remove them "
    "(the engine restores them after every patch). A host_command intent may add a Rule-state when guard "
    "for its existing locked key when the source handles that key only in specific stages."
)


@dataclass
class StaticFacts:
    assets: List[Dict[str, Any]] = field(default_factory=list)
    derivations: List[Dict[str, Any]] = field(default_factory=list)
    host_commands: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.assets or self.derivations or self.bound_commands())

    def bound_commands(self) -> List[Dict[str, Any]]:
        return [item for item in self.host_commands if item.get("control") != "window_close"]

    def input_entries(self) -> Dict[str, List[Dict[str, Any]]]:
        commands = self.bound_commands()
        if not commands:
            return {"contexts": [], "intents": [], "bindings": []}
        context = {"id": APPLICATION_CONTEXT, "name": "Application", "priority": 1000,
                   "enabled_by_default": True, "focus": "global", "consume_policy": "first_match",
                   "exclusive_group": None}
        intents, bindings = {}, []
        for item in commands:
            command = item["command"]
            intents.setdefault(command, {
                "id": "input:action.intent.{0}".format(command), "name": command.title(),
                "value_type": "digital", "required": True,
                "target": {"kind": "host_command", "command": command},
            })
            key = item["control"].rsplit(".", 1)[-1]
            bindings.append({
                "id": "input:binding.{0}.{1}".format(command, key.replace("_", ".")),
                "name": "{0} ({1})".format(command.title(), key), "context": APPLICATION_CONTEXT,
                "intent": "input:action.intent.{0}".format(command), "priority": 1000, "enabled": True,
                "consume": True, "rebindable": True, "slot": "primary",
                "accessibility_label": "{0} the game".format(command.title()),
                "trigger": {"kind": "control", "device": "keyboard", "control": item["control"],
                            "phase": "press", "modifiers": [], "modifier_policy": "exact"},
                "processing": dict(_PROCESSING),
            })
        return {"contexts": [context], "intents": list(intents.values()), "bindings": bindings}

    def to_model(self) -> Dict[str, Any]:
        """Compact summary for prompts and reviewers (ids and provenance only)."""
        return {
            "policy": LOCKED_NOTE,
            "assets": [{"id": a["id"], "kind": a["kind"], "uri": a["source"]["uri"]} for a in self.assets],
            "derivations": [dict({"id": d["id"], "strategy": d["strategy"], "name": d["name"]},
                                 **{k: v for k, v in (d.get("metadata") or {}).items()
                                    if k in ("drawn_when", "per_loop") and v})
                            for d in self.derivations],
            "host_commands": [{k: item[k] for k in ("command", "control", "source", "basis", "host_owned") if k in item}
                              for item in self.host_commands],
            "notes": list(self.notes),
        }

    def citations(self, slot: str) -> List[Dict[str, Any]]:
        """Verifiable source spans supporting the seeded entries of one IR."""
        spans = []
        if slot == "input_ir":
            spans = [item["source"] for item in self.bound_commands()]
        elif slot == "asset_ir":
            for derivation in self.derivations:
                source = derivation.get("metadata", {}).get("source") if isinstance(derivation.get("metadata"), Mapping) else None
                if source:
                    spans.append(source)
            for asset in self.assets:
                metadata = asset.get("metadata") if isinstance(asset.get("metadata"), Mapping) else {}
                spans.extend(metadata.get("referenced_by", [])[:1])
                for reference in metadata.get("source_references", [])[:1]:
                    spans.append({"path": reference["path"], "file_sha256": reference["file_sha256"],
                                  **reference["span"]})
        result, seen = [], set()
        for span in spans:
            key = (span["path"], span["line_start"], span["line_end"])
            if key in seen:
                continue
            seen.add(key)
            result.append({"evidence_id": "ev:engine.{0}.{1}".format(slot.split("_")[0], len(result)),
                           "path": span["path"], "file_sha256": span["file_sha256"],
                           "span": {"line_start": span["line_start"], "line_end": span["line_end"]},
                           "supports": "/" + slot, "kind": "static"})
        return result


def collect_static_facts(package: Any, *, title: Optional[str] = None) -> StaticFacts:
    """Measure locked facts for a SourceGamePackage (no source execution)."""
    from .source_workspace import SourceWorkspace

    root = Path(package.root).resolve()
    workspace = SourceWorkspace(root, Path(package.entrypoint))
    facts = StaticFacts()
    texts = {name: (_read_text(path), hashlib.sha256(path.read_bytes()).hexdigest())
             for name, path in workspace.files.items()}
    facts.assets.extend(_project_assets(root, workspace.assets, texts, package, title or getattr(package, "title", "")))
    try:
        from srtp.runtime_assets import discover_runtime_assets
        for asset in discover_runtime_assets(workspace.files):
            if all(asset["id"] != item["id"] for item in facts.assets):
                facts.assets.append(asset)
    except (ValueError, OSError) as error:
        facts.notes.append("runtime asset not locked: {0}".format(error))
    try:
        from srtp.drawn_shapes import discover_drawn_shapes
        for group in discover_drawn_shapes(workspace.files):
            if group.get("derivation"):
                # Provenance is kept beside the wire derivation, which has no
                # metadata field; _wire strips it before seeding documents.
                source = group["source"]
                facts.derivations.append(dict(group["derivation"], metadata={"source": {
                    key: source[key] for key in ("path", "file_sha256", "line_start", "line_end")},
                    "drawn_when": _drawn_when(group),
                    "per_loop": [loop["target"] for loop in group.get("loops") or [] if loop.get("target")]}))
    except (ValueError, OSError, RecursionError) as error:
        facts.notes.append("source drawings not locked: {0}".format(error))
    facts.host_commands.extend(discover_lifecycle_controls(workspace.files))
    return facts


def _drawn_when(group: Mapping[str, Any]) -> List[str]:
    """The source branch conditions under which a drawing is made (outermost first)."""
    conditions = sorted(group.get("conditions") or [], key=lambda item: item.get("line") or 0)
    return [str(item["test"]) if item.get("branch") != "else" else "not ({0})".format(item["test"])
            for item in conditions if item.get("test")]


VARIANT_GAP = "state variant but no appearance mapping"


def enrich_diagnostics(diagnostics: List[str], facts: Optional["StaticFacts"]) -> List[str]:
    """Add the source's own per-state drawings to a missing-appearance diagnostic."""
    if facts is None or not any(VARIANT_GAP in str(item) for item in diagnostics):
        return list(diagnostics)
    drawn = [d for d in facts.derivations if (d.get("metadata") or {}).get("drawn_when")]
    if not drawn:
        return list(diagnostics)
    choices = "; ".join("{0} is drawn when {1}".format(d["id"], " and ".join(d["metadata"]["drawn_when"]))
                        for d in drawn[:12])
    hint = ("engine hint: every value a renderer variant binding can produce needs an entry in that renderer's "
            "variants map. The source draws, per cell: {0}. Map each variant name to the drawing made for its "
            "state value, using the locked derivation id as geometry: variants: {{\"<name>\": {{\"geometry\": "
            "\"<derivation id>\", \"visible\": true}}}}, and give the empty value its own entry (e.g. "
            "{{\"visible\": false}} on a piece renderer).").format(choices)
    return list(diagnostics) + [hint]


def seed_documents(documents: Mapping[str, Mapping[str, Any]], facts: StaticFacts) -> Dict[str, Dict[str, Any]]:
    """Base documents including the locked facts, resealed at their revision."""
    from copy import deepcopy
    from srtp.asset_ir_v2 import seal_asset_ir
    from srtp.input_ir_v2 import seal_input_ir

    result = {key: deepcopy(dict(value)) for key, value in documents.items()}
    if facts.assets or facts.derivations:
        asset, _ = enforce_locked("asset_ir", result["asset_ir"], facts)
        result["asset_ir"] = seal_asset_ir(asset, revision=int(asset.get("revision") or 0))
    if facts.bound_commands():
        input_doc, _ = enforce_locked("input_ir", result["input_ir"], facts)
        result["input_ir"] = seal_input_ir(input_doc, revision=int(input_doc.get("revision") or 0))
    return result


def enforce_locked(slot: str, document: Mapping[str, Any], facts: Optional[StaticFacts]) -> Tuple[Dict[str, Any], List[str]]:
    """Restore locked entries a patch removed/altered; fold duplicates into them."""
    from copy import deepcopy

    result = deepcopy(dict(document))
    notes: List[str] = []
    if facts is None:
        return result, notes
    if slot == "asset_ir" and (facts.assets or facts.derivations):
        aliases: Dict[str, str] = {}
        locked_uris = {a["source"]["uri"]: a["id"] for a in facts.assets}
        locked_settings = {_canonical(d["settings"]): d["id"] for d in facts.derivations}
        for field_name, locked, same in (
            ("assets", facts.assets, lambda item: locked_uris.get(((item.get("source") or {}) if isinstance(item.get("source"), Mapping) else {}).get("uri"))),
            ("derivations", facts.derivations, lambda item: locked_settings.get(_canonical(item.get("settings")))
                if item.get("strategy") == "vector_shape" else None),
        ):
            if not locked:
                continue
            ids = {item["id"] for item in locked}
            kept, overrides = [], {}
            for item in result.get(field_name) or []:
                if not isinstance(item, Mapping):
                    kept.append(item)
                    continue
                identifier = item.get("id")
                if identifier in ids:
                    if field_name == "derivations" and isinstance(item.get("settings"), Mapping):
                        overrides[identifier] = {key: item["settings"][key] for key in _PRESENTATION_SETTINGS
                                                 if key in item["settings"]}
                    continue
                duplicate = same(item)
                if duplicate:
                    aliases[str(identifier)] = duplicate
                    notes.append("{0} duplicates locked {1}; references were folded into it".format(identifier, duplicate))
                    continue
                kept.append(item)
            present = {item.get("id") for item in document.get(field_name) or [] if isinstance(item, Mapping)}
            for item in locked:
                if item["id"] not in present:
                    notes.append("restored locked {0}".format(item["id"]))
            result[field_name] = [_wire(field_name, item, overrides.get(item["id"])) for item in locked] + kept
        if aliases:
            for role in result.get("roles") or []:
                if isinstance(role, dict) and role.get("resource") in aliases:
                    role["resource"] = aliases[role["resource"]]
            for derivation in result.get("derivations") or []:
                if isinstance(derivation, dict) and isinstance(derivation.get("inputs"), list):
                    derivation["inputs"] = [aliases.get(item, item) for item in derivation["inputs"]]
            for mapping in result.get("presentation_mappings") or []:
                if isinstance(mapping, dict) and mapping.get("target_resource") in aliases:
                    mapping["target_resource"] = aliases[mapping["target_resource"]]
    if slot == "input_ir" and facts.bound_commands():
        entries = facts.input_entries()
        locked_triggers = {_trigger_key(b["trigger"]) for b in entries["bindings"]}
        commands = {i["target"]["command"]: i["id"] for i in entries["intents"]}
        guarded = {}
        proposed_intents = {i.get("id"): i for i in result.get("intents") or [] if isinstance(i, Mapping)}
        for binding in result.get("bindings") or []:
            if not isinstance(binding, Mapping) or _trigger_key(binding.get("trigger")) not in locked_triggers:
                continue
            intent = proposed_intents.get(binding.get("intent"))
            target = intent.get("target") if isinstance(intent, Mapping) else None
            if not isinstance(target, Mapping) or target.get("kind") != "host_command" or "when" not in target:
                continue
            for locked_binding in entries["bindings"]:
                if _trigger_key(binding["trigger"]) == _trigger_key(locked_binding["trigger"]):
                    locked_intent = next(i for i in entries["intents"] if i["id"] == locked_binding["intent"])
                    if target.get("command") == locked_intent["target"]["command"]:
                        guarded[locked_intent["id"]] = target["when"]
        for intent in entries["intents"]:
            if intent["id"] in guarded:
                intent["target"]["when"] = guarded[intent["id"]]
        aliases = {}
        intents = []
        for intent in result.get("intents") or []:
            target = intent.get("target") if isinstance(intent, Mapping) else None
            if isinstance(target, Mapping) and target.get("kind") == "host_command" and target.get("command") in commands:
                # A second source key can share the command but have a different
                # Rule-stage guard (2048: Q in play/menu, N in modal prompts).
                # Folding it into the locked intent would erase that distinction.
                if "when" in target and (intent.get("id") != commands[target["command"]]
                                         and target["when"] != guarded.get(commands[target["command"]])):
                    intents.append(intent)
                    continue
                if intent.get("id") != commands[target["command"]]:
                    aliases[str(intent.get("id"))] = commands[target["command"]]
                continue
            if isinstance(intent, Mapping) and intent.get("id") in commands.values():
                continue
            intents.append(intent)
        bindings = []
        locked_ids = {b["id"] for b in entries["bindings"]}
        for binding in result.get("bindings") or []:
            if not isinstance(binding, Mapping):
                bindings.append(binding)
                continue
            if binding.get("id") in locked_ids:
                continue
            if _trigger_key(binding.get("trigger")) in locked_triggers:
                notes.append("{0} used a locked lifecycle control; the source binds it to a host command".format(binding.get("id")))
                continue
            # Other controls may still reach a lifecycle command (e.g. a Scene
            # restart button); they just share the locked intent.
            if binding.get("intent") in aliases:
                binding = dict(binding, intent=aliases[binding["intent"]])
            bindings.append(binding)
        present = {item.get("id") for field_name in ("contexts", "intents", "bindings")
                   for item in document.get(field_name) or [] if isinstance(item, Mapping)}
        for field_name in ("contexts", "intents", "bindings"):
            notes.extend("restored locked {0}".format(item["id"]) for item in entries[field_name]
                         if item["id"] not in present)
        contexts = [c for c in result.get("contexts") or [] if not (isinstance(c, Mapping) and c.get("id") == APPLICATION_CONTEXT)]
        result["contexts"] = entries["contexts"] + contexts
        result["intents"] = entries["intents"] + intents
        result["bindings"] = entries["bindings"] + bindings
    return result, notes


def discover_lifecycle_controls(files: Mapping[str, Path]) -> List[Dict[str, Any]]:
    """Keys whose handler quits or fully re-initializes the game."""
    found: List[Dict[str, Any]] = []
    for relative, path in sorted(files.items()):
        if PurePosixPath(relative).suffix.lower() != ".py":
            continue
        raw = path.read_bytes()
        try:
            tree = ast.parse(raw.decode("utf-8-sig", errors="replace"))
        except (SyntaxError, ValueError, RecursionError):
            continue
        found.extend(_LifecycleScanner(relative, hashlib.sha256(raw).hexdigest(), tree).scan())
    unique, seen = [], set()
    for item in found:
        key = (item["command"], item["control"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


class _LifecycleScanner:
    def __init__(self, relative: str, digest: str, tree: ast.Module) -> None:
        self.relative, self.digest, self.tree = relative, digest, tree
        self.aliases: Dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    self.aliases[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module:
                for item in node.names:
                    self.aliases[item.asname or item.name] = node.module + "." + item.name
        # Main-loop flags (while running: / while self.running:). A loop inside a
        # function that an event loop calls is modal (pause/menu); clearing its
        # flag resumes the game instead of quitting it.
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        owner = {}
        for function in functions:
            for child in ast.walk(function):
                if child is not function:
                    owner.setdefault(id(child), function.name)
        event_loops = [n for n in ast.walk(tree) if isinstance(n, ast.While) and any(
            isinstance(c, ast.Call) and self.qualified(c.func) == "pygame.event.get" for c in ast.walk(n))]
        called = {c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
                  for loop in event_loops for c in ast.walk(loop) if isinstance(c, ast.Call)}
        self.modal_loops = {id(node) for node in ast.walk(tree)
                            if isinstance(node, ast.While) and owner.get(id(node)) in called}
        self.loop_flags = {_text(node.test) for node in ast.walk(tree)
                           if isinstance(node, ast.While) and isinstance(node.test, (ast.Name, ast.Attribute))
                           and id(node) not in self.modal_loops}
        # Nearest enclosing while loop of every node (handlers inside modal
        # prompts, e.g. "RESTART? (y/n)", only mean something there).
        self.enclosing_loop = {}
        for loop in [n for n in ast.walk(tree) if isinstance(n, ast.While)]:  # breadth-first: outer loops first
            for child in ast.walk(loop):
                if child is not loop:
                    self.enclosing_loop[id(child)] = loop
        # Initializers: methods the constructor calls, functions called before the main loop.
        self.initializers: Dict[str, str] = {}
        self.classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
        # Functions that build the starting state right before an event loop
        # (board = new_game(...); while True: ...) are initializers too.
        for scope in [tree] + functions:
            body = list(getattr(scope, "body", []))
            for index, statement in enumerate(body):
                if not (isinstance(statement, ast.While) and id(statement) in {id(l) for l in event_loops}):
                    continue
                for earlier in body[:index]:
                    for call in [n for n in ast.walk(earlier) if isinstance(n, ast.Call)]:
                        name = call.func.id if isinstance(call.func, ast.Name) else None
                        if name and name in {f.name for f in functions}:
                            self.initializers.setdefault(name, "{0}() builds the state before the main loop".format(name))
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for method in cls.body:
                if isinstance(method, ast.FunctionDef) and method.name == "__init__":
                    for call in [n for n in ast.walk(method) if isinstance(n, ast.Call)]:
                        if isinstance(call.func, ast.Attribute) and _text(call.func.value) == "self":
                            self.initializers[call.func.attr] = "{0}.__init__ calls {1}()".format(cls.name, call.func.attr)

    def qualified(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return self.qualified(node.value) + "." + node.attr
        return ""

    def scan(self) -> List[Dict[str, Any]]:
        result = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.If):
                continue
            keys = self._keys(node.test)
            window_close = self._is_window_close(node.test)
            if not keys and not window_close:
                continue
            loop = self.enclosing_loop.get(id(node))
            if loop is not None and id(loop) in self.modal_loops:
                continue
            command, basis = self._command(node.body)
            if command is None:
                continue
            source = {"path": self.relative, "file_sha256": self.digest,
                      "line_start": node.lineno, "line_end": node.end_lineno or node.lineno}
            if window_close:
                if command == "quit":
                    result.append({"command": "quit", "control": "window_close", "host_owned": True,
                                   "source": source, "basis": basis})
                continue
            for symbol in keys:
                control = _key_control(symbol)
                if control is None:
                    continue
                result.append({"command": command, "control": control, "source": source, "basis": basis})
        return result

    def _keys(self, test: ast.AST) -> List[str]:
        if isinstance(test, ast.BoolOp):
            keys: List[str] = []
            for value in test.values:
                keys.extend(self._keys(value))
            return keys
        if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
            return []
        left, right = test.left, test.comparators[0]
        if not _text(left).endswith("event.key"):
            left, right = right, left
        if not _text(left).endswith("event.key"):
            return []
        if isinstance(test.ops[0], ast.Eq):
            symbols = [right]
        elif isinstance(test.ops[0], ast.In) and isinstance(right, (ast.Tuple, ast.List, ast.Set)):
            symbols = list(right.elts)
        else:
            return []
        return [self.qualified(item) for item in symbols if isinstance(item, (ast.Name, ast.Attribute))]

    def _is_window_close(self, test: ast.AST) -> bool:
        return (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
                and any(_text(side).endswith("event.type") for side in (test.left, test.comparators[0]))
                and any(self.qualified(side).endswith(".QUIT") or self.qualified(side) == "QUIT"
                        for side in (test.left, test.comparators[0])))

    def _command(self, body: Sequence[ast.stmt]) -> Tuple[Optional[str], str]:
        for statement in body:
            for node in ast.walk(statement):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value in (False, 0):
                    for target in node.targets:
                        if _text(target) in self.loop_flags:
                            return "quit", "sets main loop flag {0} = {1}".format(_text(target), node.value.value)
                if isinstance(node, ast.Call) and self.qualified(node.func) in _EXIT_CALLS:
                    return "quit", "calls {0}()".format(self.qualified(node.func))
                if (isinstance(node, ast.Call) and self.qualified(node.func) == "pygame.event.post" and node.args
                        and isinstance(node.args[0], ast.Call) and node.args[0].args
                        and self.qualified(node.args[0].args[0]).endswith("QUIT")):
                    return "quit", "posts a pygame.QUIT event"
                if isinstance(node, ast.Raise) and node.exc is not None and _text(getattr(node.exc, "func", node.exc)) == "SystemExit":
                    return "quit", "raises SystemExit"
        for statement in body:
            for node in ast.walk(statement):
                if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name)):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
                    if name in self.initializers:
                        return "restart", "calls {0}(); {1}".format(_text(node.func), self.initializers[name])
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and _text(node.value.func) in self.classes:
                    return "restart", "re-creates {0}()".format(_text(node.value.func))
        return None, ""


def _project_assets(root: Path, indexed: Sequence[Mapping[str, Any]], texts: Mapping[str, str],
                    package: Any, title: str) -> List[Dict[str, Any]]:
    corpus = [(name, text, digest) for name, (text, digest) in texts.items() if text]
    license_id = str(getattr(package, "license_name", "") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9.+-]+", license_id) or license_id.lower() == "unknown":
        license_id = "NOASSERTION"
    assets, used_ids = [], set()
    for entry in indexed:
        relative = str(entry.get("path") or "")
        suffix = PurePosixPath(relative).suffix.lower()
        if suffix not in _MEDIA or not entry.get("uri"):
            continue
        references = _references(relative, corpus)
        if not references:
            continue
        kind, media_type = _MEDIA[suffix]
        payload = (root / relative).read_bytes()
        local = ".".join(_slug(part) for part in PurePosixPath(relative).with_suffix("").parts if _slug(part))
        identifier = "asset:{0}.{1}".format(kind, local or "file")
        while identifier in used_ids:
            identifier += ".x"
        used_ids.add(identifier)
        assets.append({
            "id": identifier, "name": PurePosixPath(relative).name, "kind": kind, "media_type": media_type,
            "source": {"uri": entry["uri"], "content_hash": hashlib.sha256(payload).hexdigest(), "byte_size": len(payload)},
            "license": {"spdx_id": license_id, "attribution": "Source project {0}; per-file license not verified".format(title or "files"),
                        "source_uri": None, "redistribution": "unknown"},
            "importer": {"capability": "cubeengine.image" if kind == "image" else "cubeengine.raw-file",
                         "version": "1.0", "settings": {}},
            "metadata": {"engine_fact": "source_file", "referenced_by": references[:4]},
        })
    return assets


def _references(relative: str, corpus: Iterable[Tuple[str, str, str]]) -> List[Dict[str, Any]]:
    """Source lines naming the file, its name, or (for dynamic joins) its folder."""
    path = PurePosixPath(relative)
    needles = [relative, relative.replace("/", "\\"), path.name]
    folder = path.parent.name
    result = []
    for name, text, digest in corpus:
        if not name.endswith((".py", ".json")):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            quoted = re.findall(r"""['"]([^'"]+)['"]""", line)
            if any(needle in item for item in quoted for needle in needles) or (
                    folder and any(item.strip("/\\") == folder for item in quoted)):
                result.append({"path": name, "file_sha256": digest, "line_start": number, "line_end": number})
    return result


_PRESENTATION_SETTINGS = ("depth", "axis", "size", "segments")


def _wire(field_name: str, item: Mapping[str, Any], overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Locked entry as stored; derivations keep a patch's presentation-only choices."""
    from copy import deepcopy
    value = deepcopy(dict(item))
    if field_name == "derivations":
        value.pop("metadata", None)
        if overrides:
            value["settings"] = dict(value["settings"], **deepcopy(dict(overrides)))
    return value


def _trigger_key(trigger: Any) -> Tuple[Any, ...]:
    if not isinstance(trigger, Mapping):
        return ()
    return (trigger.get("device"), trigger.get("control"), trigger.get("phase", "press"))


def _key_control(symbol: str) -> Optional[str]:
    from srtp.input_adapter_contract import KEY_CONTROLS
    token = symbol.rsplit(".", 1)[-1]
    if not token.startswith("K_"):
        return None
    name = token[2:]
    key = _KEY_NAMES.get(name.upper()) or (name.lower() if len(name) == 1 and name.isalnum() else None)
    control = "keyboard.key.{0}".format(key) if key else None
    return control if control in KEY_CONTROLS else None


def _canonical(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _text(node: Any) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return ""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace") if path.suffix.lower() in (".py", ".json", ".txt", ".md") else ""
    except OSError:
        return ""
