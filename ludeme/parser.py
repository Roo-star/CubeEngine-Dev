"""Parse .cube.lud S-expression files into GameDescription AST."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, List, Optional, Union

from .grammar import (
    BoardSpec,
    EndRule,
    GameDescription,
    LudemeNode,
    LUDEME_FORMAT_VERSION,
    PieceSpec,
    PlayRule,
)


class ParseError(ValueError):
    """Raised when a .cube.lud file cannot be parsed."""


@dataclass
class _Token:
    kind: str
    value: str
    line: int
    column: int


def parse_ludeme_file(path: Path) -> GameDescription:
    text = Path(path).read_text(encoding="utf-8-sig")
    return parse_ludeme_text(text, source=str(path))


def parse_ludeme_text(text: str, source: str = "<string>") -> GameDescription:
    tokens = list(_tokenize(text, source))
    index = 0

    def peek() -> Optional[_Token]:
        return tokens[index] if index < len(tokens) else None

    def consume(expected: Optional[str] = None) -> _Token:
        nonlocal index
        token = peek()
        if token is None:
            raise ParseError("Unexpected end of input in {0}".format(source))
        if expected is not None and token.kind != expected:
            raise ParseError(
                "Expected {0} at {1}:{2}, got {3}".format(expected, token.line, token.column, token.kind)
            )
        index += 1
        return token

    def parse_value() -> Any:
        token = peek()
        if token is None:
            raise ParseError("Unexpected end of input in {0}".format(source))
        if token.kind == "LPAREN":
            return parse_list()
        if token.kind == "LBRACE":
            return parse_block()
        if token.kind == "STRING":
            consume("STRING")
            return token.value
        if token.kind == "INT":
            consume("INT")
            return int(token.value)
        if token.kind == "IDENT":
            consume("IDENT")
            return token.value
        raise ParseError("Unexpected token {0} at {1}:{2}".format(token.kind, token.line, token.column))

    def parse_list() -> LudemeNode:
        start = consume("LPAREN")
        name_token = consume("IDENT")
        args: List[Any] = []
        while peek() is not None and peek().kind != "RPAREN":
            args.append(parse_value())
        consume("RPAREN")
        return LudemeNode(name=name_token.value, args=args)

    def parse_block() -> List[Any]:
        consume("LBRACE")
        items: List[Any] = []
        while peek() is not None and peek().kind != "RBRACE":
            items.append(parse_value())
        consume("RBRACE")
        return items

    if not tokens:
        raise ParseError("Empty Ludeme document: {0}".format(source))

    format_version = LUDEME_FORMAT_VERSION
    game_root: Optional[LudemeNode] = None
    while peek() is not None:
        value = parse_value()
        if isinstance(value, LudemeNode) and value.name == "meta":
            format_version = _meta_format(value) or format_version
        elif isinstance(value, LudemeNode) and value.name == "game":
            if game_root is not None:
                raise ParseError("Multiple (game ...) roots are not allowed in {0}".format(source))
            game_root = value

    if game_root is None:
        raise ParseError("Root ludeme must include (game ...) in {0}".format(source))
    game = _game_from_root(game_root)
    game.format_version = format_version
    return game


def _tokenize(text: str, source: str) -> Iterator[_Token]:
    line = 1
    column = 1
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if char in " \t\r\n":
            if char == "\n":
                line += 1
                column = 0
            index += 1
            column += 1
            continue
        if char == ";":
            while index < length and text[index] != "\n":
                index += 1
            continue
        if char == "(":
            yield _Token("LPAREN", "(", line, column)
            index += 1
            column += 1
            continue
        if char == ")":
            yield _Token("RPAREN", ")", line, column)
            index += 1
            column += 1
            continue
        if char == "{":
            yield _Token("LBRACE", "{", line, column)
            index += 1
            column += 1
            continue
        if char == "}":
            yield _Token("RBRACE", "}", line, column)
            index += 1
            column += 1
            continue
        if char == '"':
            index += 1
            column += 1
            start_column = column
            parts: List[str] = []
            while index < length and text[index] != '"':
                if text[index] == "\\" and index + 1 < length:
                    parts.append(text[index + 1])
                    index += 2
                    column += 2
                    continue
                if text[index] == "\n":
                    line += 1
                    column = 1
                else:
                    column += 1
                parts.append(text[index])
                index += 1
            if index >= length:
                raise ParseError("Unterminated string at {0}:{1}".format(line, start_column))
            index += 1
            column += 1
            yield _Token("STRING", "".join(parts), line, start_column)
            continue
        if char.isdigit() or (char == "-" and index + 1 < length and text[index + 1].isdigit()):
            start = index
            index += 1
            column += 1
            while index < length and (text[index].isdigit()):
                index += 1
                column += 1
            yield _Token("INT", text[start:index], line, column - (index - start))
            continue
        if re.match(r"[A-Za-z_+\-*/?<>=!]", char):
            start = index
            while index < length and re.match(r"[A-Za-z0-9_+\-*/?<>=!]", text[index]):
                index += 1
                column += index - start
            yield _Token("IDENT", text[start:index], line, column - (index - start))
            continue
        raise ParseError("Unexpected character {0!r} at {1}:{2} in {3}".format(char, line, column, source))


def _game_from_root(root: LudemeNode) -> GameDescription:
    name = ""
    players = 0
    board: Optional[BoardSpec] = None
    pieces: List[PieceSpec] = []
    play: Optional[PlayRule] = None
    end_rules: List[EndRule] = []
    format_version = LUDEME_FORMAT_VERSION

    for arg in root.args:
        if isinstance(arg, str):
            name = arg
            continue
        if not isinstance(arg, LudemeNode):
            continue
        if arg.name == "meta":
            format_version = _meta_format(arg) or format_version
        elif arg.name == "players":
            players = _require_int(arg.args, "players")
        elif arg.name == "equipment":
            board, pieces = _parse_equipment(arg)
        elif arg.name == "rules":
            play, end_rules = _parse_rules(arg)

    if not name:
        raise ParseError("(game ...) requires a name string")
    if players < 1:
        raise ParseError("(players N) must be a positive integer")
    if board is None:
        raise ParseError("(equipment ...) with (board ...) is required")
    if play is None:
        raise ParseError("(rules (play ...)) is required")
    if not end_rules:
        raise ParseError("(rules (end ...)) requires at least one terminal rule")

    return GameDescription(
        name=name,
        players=players,
        board=board,
        pieces=pieces,
        play=play,
        end_rules=end_rules,
        format_version=format_version,
        raw_root=root,
    )


def _meta_format(node: LudemeNode) -> Optional[str]:
    for arg in node.args:
        if isinstance(arg, LudemeNode) and arg.name == "format" and arg.args:
            value = arg.args[0]
            if isinstance(value, str):
                return value
    return None


def _parse_equipment(node: LudemeNode) -> tuple[BoardSpec, List[PieceSpec]]:
    board: Optional[BoardSpec] = None
    pieces: List[PieceSpec] = []
    items = node.args
    if len(items) == 1 and isinstance(items[0], list):
        items = items[0]
    for item in items:
        if not isinstance(item, LudemeNode):
            continue
        if item.name == "board":
            board = _parse_board(item)
        elif item.name == "piece":
            pieces.append(_parse_piece(item))
    if board is None:
        raise ParseError("(equipment ...) must contain (board ...)")
    return board, pieces


def _parse_board(node: LudemeNode) -> BoardSpec:
    if not node.args or not isinstance(node.args[0], LudemeNode):
        raise ParseError("(board ...) requires a shape ludeme")
    shape = node.args[0]
    if shape.name == "rect":
        values = [_require_int([value], "rect dimension") for value in shape.args[:3]]
        while len(values) < 3:
            values.append(1)
        return BoardSpec(x=values[0], y=values[1], z=values[2])
    raise ParseError("Unsupported board shape: {0}".format(shape.name))


def _parse_piece(node: LudemeNode) -> PieceSpec:
    if len(node.args) < 2:
        raise ParseError("(piece label player) requires two arguments")
    label = node.args[0]
    player = node.args[1]
    if not isinstance(label, str) or not isinstance(player, str):
        raise ParseError("(piece ...) expects string label and player id")
    return PieceSpec(label=label, player_id=player)


def _parse_rules(node: LudemeNode) -> tuple[PlayRule, List[EndRule]]:
    play: Optional[PlayRule] = None
    end_rules: List[EndRule] = []
    for arg in node.args:
        if not isinstance(arg, LudemeNode):
            continue
        if arg.name == "play":
            play = _parse_play(arg)
        elif arg.name == "end":
            end_rules.extend(_parse_end(arg))
    if play is None:
        raise ParseError("(rules ...) must include (play ...)")
    return play, end_rules


def _parse_play(node: LudemeNode) -> PlayRule:
    if not node.args or not isinstance(node.args[0], LudemeNode):
        raise ParseError("(play ...) must contain (move ...)")
    move = node.args[0]
    if move.name != "move" or not move.args:
        raise ParseError("Unsupported play rule")
    kind = move.args[0]
    if not isinstance(kind, str):
        raise ParseError("(move Kind ...) expects an identifier kind")
    target = "Empty"
    for arg in move.args[1:]:
        if isinstance(arg, LudemeNode) and arg.name == "to":
            target = _extract_sites(arg)
    if kind != "Add" or target != "Empty":
        raise ParseError("MVP supports only (move Add (to (sites Empty)))")
    return PlayRule(kind=kind, target=target)


def _extract_sites(node: LudemeNode) -> str:
    for arg in node.args:
        if isinstance(arg, LudemeNode) and arg.name == "sites" and arg.args:
            value = arg.args[0]
            if isinstance(value, str):
                return value
    return "Empty"


def _parse_end(node: LudemeNode) -> List[EndRule]:
    rules: List[EndRule] = []
    for arg in node.args:
        if isinstance(arg, LudemeNode) and arg.name == "if" and len(arg.args) >= 2:
            condition = arg.args[0]
            result = arg.args[1]
            if isinstance(condition, LudemeNode) and isinstance(result, LudemeNode):
                rules.append(EndRule(condition=condition, result=result))
    return rules


def _require_int(values: List[Any], label: str) -> int:
    if not values:
        raise ParseError("{0} requires an integer".format(label))
    value = values[0]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError("{0} must be an integer".format(label))
    if value <= 0:
        raise ParseError("{0} must be positive".format(label))
    return value
