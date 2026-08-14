"""FreeFlow compiler interface — source evidence to .cube.lud."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from srtp.source_game import SourceGamePackage


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
    """Deterministic stand-in for FreeFlow LLM on the Tic-Tac-Toe fixture."""

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
            notes="Deterministic mapper for Tic-Tac-Toe fixture; replace with local LLM via FreeFlowCompiler.",
        )


def render_tictactoe_ludeme(
    title: str,
    width: int,
    height: int,
    depth: int,
    win_length: int,
) -> str:
    safe_title = title.replace('"', "'")
    return "\n".join([
        '(meta (format "cubeengine.ludeme/0.1"))',
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


def select_compiler(package: SourceGamePackage) -> FreeFlowCompiler:
    stem = package.entrypoint.stem.lower()
    title = package.title.lower()
    if "tictactoe" in stem or "tic-tac-toe" in title or "tic tac toe" in title:
        return TicTacToeMapper()
    schema_game = package.rule_report.schema.get("game", {})
    game_id = str(schema_game.get("id", "")).lower()
    if "tictactoe" in game_id or "tic_tac_toe" in game_id:
        return TicTacToeMapper()
    raise ValueError(
        "No FreeFlow mapper is registered for {0}. MVP supports Tic-Tac-Toe only.".format(package.entrypoint)
    )
