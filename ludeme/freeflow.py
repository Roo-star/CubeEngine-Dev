"""FreeFlow compiler: source evidence to cubeengine.ludeme/0.1 via Gemini."""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol, Sequence

from srtp.source_game import SourceGamePackage

from .grammar import LUDEME_FORMAT_VERSION
from .parser import ParseError, parse_ludeme_text


MAX_SOURCE_CHARS = 80_000
MAX_SIBLING_FILES = 8
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """You are CubeEngine's source-to-Ludeme compiler (FreeFlow).

You compile a 2D source game into CubeEngine Ludeme, then spatially lift it to 3D.
You are a compiler front-end, not a game engine, renderer, or player.

## Inputs
1. Immutable source files (Python/JSON/etc). Treat them as evidence. Do not execute them.
2. Optional static-analysis notes (dimensions, parameters). Prefer source literals over notes when they conflict.
3. Target depth Z and optional designer intent.

## What you must do
1. Reconstruct the 2D rules: board/space, players, pieces/entities, legal actions, scoring, terminals, randomness.
2. Lift to 3D without inventing a new game:
   - Preserve source X and Y.
   - Set board Z to the requested target depth (Z=1 means source-equivalent).
   - Extend movement, neighbourhood, gravity, merge, reveal-flood, and N-in-a-row through Z.
   - Do not add players, pieces, or win conditions the source does not have.
3. Emit one CubeEngine Ludeme document. No markdown, no commentary, no JSON wrapper.

## Output format (mandatory)
First line:
(meta (format "cubeengine.ludeme/0.1"))
Then a single (game ...) tree.

Syntax is Ludii-inspired S-expressions:
  (ludemeName arg1 arg2 ...)
Arguments may be ludemes, "strings", integers, true/false, or {{ ... }} arrays.

Required skeleton:
(meta (format "cubeengine.ludeme/0.1"))
(game "Name"
  (players N)
  (equipment {{
    (board (rect X Y Z))
    (piece "Label" P1)
    ...
  }})
  (rules
    (play ...)
    (end ...)
  )
)

## Play ludemes (choose the family that matches the source)
Empty-cell placement (tic-tac-toe, gomoku):
  (play (move Add (to (sites Empty))))
Gravity drop / Connect Four:
  (play (move Drop (into (sites Column)) (gravity Y-)))
Sliding avatar / Snake:
  (play (move Slide (dirs Orthogonal) (grow OnFood) (die OnSelf Or Wall)))
Shift-and-merge / 2048:
  (play (move ShiftMerge (dirs Orthogonal) (spawn AfterMove)))
Hidden information / Minesweeper:
  (play (move Reveal (to (sites Hidden)) (flood ZeroAdjacent)))

If the source mixes families, compose the real verbs. Never recast Snake, 2048, or Minesweeper as tic-tac-toe.

## End ludemes
N-in-a-row (planar or 3D lines after lift):
  (if (is Line N) (result Mover Win))
Filled board with no line:
  (if (and (not (is Line N)) (is Full)) (result Draw))
Connect Four also uses Line after gravity placement.
Snake:
  (if (is Collision) (result Mover Loss))
  (if (is Length Target) (result Mover Win))
2048:
  (if (is TileValue 2048) (result Mover Win))
  (if (and (is Full) (not (can ShiftMerge))) (result Draw))
Minesweeper:
  (if (is RevealedMine) (result Mover Loss))
  (if (is AllSafeRevealed) (result Mover Win))

## Spatial-lift rules (generic)
- Neighbourhoods: 4-dir planar → 6-dir 3D; 8-dir planar → 26-dir 3D, unless the source hard-codes a 2D-only topology.
- Lines: the same N, now also along Z and 3D diagonals.
- Gravity: keep the source gravity axis; extra axes are optional only if designer intent says so.
- Random spawn / mines / food: same probabilities over the 3D legal sites.
- Unknown behaviour: omit it or comment with ; unresolved: ...  Never guess a genre.

## Executable subset note
CubeEngine's current interpreter runs Add + Line/Full. Still emit the honest ludeme for other families so later runtimes can compile it.

## Example of a placement lift (not a template for unrelated games)
2D 3x3 place-on-empty, win by 3-in-a-row, lift Z=3:

(meta (format "cubeengine.ludeme/0.1"))
(game "3D Tic-Tac-Toe"
  (players 2)
  (equipment {{
    (board (rect 3 3 3))
    (piece "X" P1)
    (piece "O" P2)
  }})
  (rules
    (play (move Add (to (sites Empty))))
    (end
      (if (is Line 3) (result Mover Win))
      (if (and (not (is Line 3)) (is Full)) (result Draw))
    )
  )
)
""".replace("{{", "{").replace("}}", "}")


@dataclass(frozen=True)
class FreeFlowRequest:
    package: SourceGamePackage
    target_z: int = 1
    design_intent: str = ""


@dataclass(frozen=True)
class FreeFlowResult:
    ludeme_text: str
    output_path: Optional[Path] = None
    compiler_id: str = "unknown"
    notes: str = ""


class FreeFlowCompiler(Protocol):
    def compile(self, request: FreeFlowRequest) -> FreeFlowResult:
        ...


class TicTacToeMapper:
    """Deterministic fallback for the Tic-Tac-Toe fixture when Gemini is unavailable."""

    compiler_id = "cubeengine.freeflow.tictactoe-mapper/0.1"

    def compile(self, request: FreeFlowRequest) -> FreeFlowResult:
        package = request.package
        width, height = _board_dimensions(package)
        depth = max(1, int(request.target_z))
        title = _display_title(package)
        if depth > 1 and "3d" not in title.lower():
            title = "3D {0}".format(title)
        win_length = min(width, height, depth, 3)
        ludeme_text = render_tictactoe_ludeme(
            title=title,
            width=width,
            height=height,
            depth=depth,
            win_length=win_length,
        )
        return FreeFlowResult(
            ludeme_text=ludeme_text,
            compiler_id=self.compiler_id,
            notes="Deterministic mapper fallback (CUBEENGINE_FREEFLOW_BACKEND=mapper).",
        )


class GeminiLudemeCompiler:
    """Compile source games to .cube.lud with freeflow-llm Gemini."""

    compiler_id = "cubeengine.freeflow.gemini/0.1"

    def __init__(self, model: Optional[str] = None) -> None:
        self.model = model or os.environ.get("CUBEENGINE_GEMINI_MODEL", DEFAULT_GEMINI_MODEL)

    def compile(self, request: FreeFlowRequest) -> FreeFlowResult:
        user_prompt = build_user_prompt(request)
        raw = self._chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ])
        ludeme_text = extract_ludeme_document(raw)
        ludeme_text = _ensure_target_z(ludeme_text, request.target_z)
        ludeme_text = _try_repair(self, ludeme_text, request)
        return FreeFlowResult(
            ludeme_text=ludeme_text,
            compiler_id=self.compiler_id,
            notes="Compiled with freeflow-llm Gemini ({0}).".format(self.model),
        )

    def _chat(self, messages: Sequence[dict]) -> str:
        try:
            from freeflow_llm import FreeFlowClient
            from freeflow_llm.providers import GeminiProvider
        except ImportError as error:
            raise RuntimeError(
                "freeflow-llm is required. Install it with: pip install freeflow-llm"
            ) from error

        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Get a key at https://aistudio.google.com/app/apikey"
            )

        providers = [GeminiProvider(api_key=api_key)]
        with FreeFlowClient(providers=providers) as client:
            response = client.chat(
                messages=list(messages),
                model=self.model,
                temperature=0.1,
                max_tokens=4096,
            )
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Gemini returned an empty Ludeme document.")
        return content


def render_tictactoe_ludeme(
    title: str,
    width: int,
    height: int,
    depth: int,
    win_length: int,
) -> str:
    safe_title = title.replace('"', "'")
    return "\n".join([
        '(meta (format "{0}"))'.format(LUDEME_FORMAT_VERSION),
        '(game "{0}"'.format(safe_title),
        "  (players 2)",
        "  (equipment {",
        "    (board (rect {0} {1} {2}))".format(width, height, depth),
        '    (piece "X" P1)',
        '    (piece "O" P2)',
        "  })",
        "  (rules",
        "    (play (move Add (to (sites Empty))))",
        "    (end",
        "      (if (is Line {0}) (result Mover Win))".format(win_length),
        "      (if (and (not (is Line {0})) (is Full)) (result Draw))".format(win_length),
        "    )",
        "  )",
        ")",
        "",
    ])


def build_user_prompt(request: FreeFlowRequest) -> str:
    package = request.package
    sources = _collect_source_texts(package)
    dims = package.transformation.source_dimensions
    intent = request.design_intent.strip() or "(none)"
    parts = [
        "Compile the following 2D source game into cubeengine.ludeme/0.1 and lift it to 3D.",
        "",
        "Target depth Z: {0}".format(max(1, int(request.target_z))),
        "Designer intent: {0}".format(intent),
        "Package title: {0}".format(package.title),
        "Entrypoint: {0}".format(package.entrypoint),
        "Importer source plane: X={0} Y={1}".format(dims.get("x"), dims.get("y")),
        "Treat importer dimensions as hints only; prefer constants in the source files.",
        "",
        "Source files:",
    ]
    for path, text in sources:
        parts.append("----- FILE: {0} -----".format(path))
        parts.append(text)
        parts.append("----- END FILE -----")
        parts.append("")
    parts.append("Return only the .cube.lud document.")
    return "\n".join(parts)


def extract_ludeme_document(text: str) -> str:
    stripped = text.strip()
    fence = re.search(r"```(?:ludeme|lisp|scheme|text)?\s*([\s\S]*?)```", stripped, re.IGNORECASE)
    if fence:
        stripped = fence.group(1).strip()
    meta = stripped.find("(meta")
    game = stripped.find("(game")
    start = meta if meta >= 0 else game
    if start < 0:
        raise ValueError("Gemini output did not contain (meta ...) or (game ...).")
    stripped = stripped[start:].strip()
    if not stripped.endswith(")"):
        last = stripped.rfind(")")
        if last >= 0:
            stripped = stripped[: last + 1]
    return stripped + "\n"


def select_compiler(package: SourceGamePackage) -> FreeFlowCompiler:
    backend = os.environ.get("CUBEENGINE_FREEFLOW_BACKEND", "gemini").strip().lower()
    if backend in ("mapper", "tictactoe", "deterministic"):
        if not _looks_like_tictactoe(package):
            raise ValueError("Mapper backend only supports Tic-Tac-Toe fixtures.")
        return TicTacToeMapper()
    return GeminiLudemeCompiler()


def _try_repair(compiler: GeminiLudemeCompiler, ludeme_text: str, request: FreeFlowRequest) -> str:
    try:
        parse_ludeme_text(ludeme_text, source="<gemini>")
        return ludeme_text
    except (ParseError, ValueError) as error:
        repair_prompt = "\n".join([
            "Your previous .cube.lud failed to parse:",
            str(error),
            "",
            "Previous document:",
            ludeme_text,
            "",
            "Emit a corrected cubeengine.ludeme/0.1 document only. Keep the same game family and target Z={0}.".format(
                request.target_z
            ),
        ])
        raw = compiler._chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(request)},
            {"role": "user", "content": repair_prompt},
        ])
        repaired = _ensure_target_z(extract_ludeme_document(raw), request.target_z)
        parse_ludeme_text(repaired, source="<gemini-repair>")
        return repaired


def _ensure_target_z(ludeme_text: str, target_z: int) -> str:
    depth = max(1, int(target_z))

    def replace(match: re.Match[str]) -> str:
        return "(rect {0} {1} {2})".format(match.group(1), match.group(2), depth)

    updated, count = re.subn(r"\(rect\s+(\d+)\s+(\d+)\s+(\d+)\)", replace, ludeme_text, count=1)
    if count:
        return updated
    updated, count = re.subn(r"\(rect\s+(\d+)\s+(\d+)\)", replace, ludeme_text, count=1)
    return updated if count else ludeme_text


def _collect_source_texts(package: SourceGamePackage) -> List[tuple[str, str]]:
    files: List[Path] = [package.entrypoint]
    if package.entrypoint.is_file():
        siblings = sorted(
            path for path in package.entrypoint.parent.glob("*.py")
            if path.resolve() != package.entrypoint.resolve()
        )
        files.extend(siblings[:MAX_SIBLING_FILES])
    collected: List[tuple[str, str]] = []
    remaining = MAX_SOURCE_CHARS
    for path in files:
        if remaining <= 0:
            break
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        if len(text) > remaining:
            text = text[:remaining] + "\n; ... truncated ...\n"
        remaining -= len(text)
        try:
            relative = str(path.relative_to(package.root))
        except ValueError:
            relative = str(path)
        collected.append((relative, text))
    if not collected:
        collected.append((str(package.entrypoint), "; unreadable source"))
    return collected


def _board_dimensions(package: SourceGamePackage) -> tuple[int, int]:
    from_source = _literal_board_from_source(package.entrypoint)
    if from_source is not None:
        return from_source

    width = height = None
    for parameter in package.parameters:
        if parameter.id in ("board_width", "width") and isinstance(parameter.value, int):
            width = parameter.value
        if parameter.id in ("board_height", "height") and isinstance(parameter.value, int):
            height = parameter.value
        if parameter.id == "source_grid_x" and width is None and isinstance(parameter.value, int):
            width = parameter.value
        if parameter.id == "source_grid_y" and height is None and isinstance(parameter.value, int):
            height = parameter.value

    source = package.transformation.source_dimensions
    if width is None and isinstance(source.get("x"), int):
        width = source["x"]
    if height is None and isinstance(source.get("y"), int):
        height = source["y"]

    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        if width == height and width <= 8:
            return width, height
        if _looks_like_tictactoe(package):
            return 3, 3
        return width, height

    if _looks_like_tictactoe(package):
        return 3, 3
    return 3, 3


def _literal_board_from_source(entrypoint: Path) -> Optional[tuple[int, int]]:
    try:
        tree = ast.parse(entrypoint.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError):
        return None
    values: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, int):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                values[target.id] = int(node.value.value)
    width = values.get("BOARD_WIDTH") or values.get("GRID_SIZE")
    height = values.get("BOARD_HEIGHT") or values.get("GRID_SIZE")
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        return width, height
    return None


def _display_title(package: SourceGamePackage) -> str:
    stem = package.entrypoint.stem.replace("_", " ").replace("-", " ")
    if "tictactoe" in stem.lower() or "tic tac toe" in stem.lower():
        return "Tic-Tac-Toe"
    title = (package.title or stem).strip()
    if len(title) > 48 or "static-analysis" in title.lower():
        return stem.title()
    return title


def _looks_like_tictactoe(package: SourceGamePackage) -> bool:
    stem = package.entrypoint.stem.lower()
    title = (package.title or "").lower()
    game_id = str(package.rule_report.schema.get("game", {}).get("id", "")).lower()
    return any(
        token in text
        for text in (stem, title, game_id)
        for token in ("tictactoe", "tic-tac-toe", "tic_tac_toe")
    )
