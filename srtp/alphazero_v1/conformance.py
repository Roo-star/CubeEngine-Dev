"""Executable conformance checks for a compiled nine-API game adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

import numpy as np

from .compiler import AlphaZeroAdapterError, AlphaZeroGame


@dataclass(frozen=True)
class AlphaZeroConformanceReport:
    passed: bool
    checks: Mapping[str, bool]
    plies: int
    terminal_value: float
    messages: Tuple[str, ...]

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": dict(self.checks),
            "plies": self.plies,
            "terminal_value": self.terminal_value,
            "messages": list(self.messages),
        }


def assess_alphazero_conformance(
    game: AlphaZeroGame, *, maximum_plies: int = 4096,
) -> AlphaZeroConformanceReport:
    checks: Dict[str, bool] = {}
    messages = []
    plies = 0
    terminal_value = 0.0
    try:
        first = game.getInitBoard()
        second = game.getInitBoard()
        checks["getInitBoard.independent"] = first is not second and np.array_equal(first, second)
        checks["getBoardSize.shape"] = tuple(first.shape) == tuple(game.getBoardSize())
        checks["getActionSize.positive"] = game.getActionSize() > 0
        representation = game.stringRepresentation(first)
        checks["stringRepresentation.stable"] = (
            isinstance(representation, bytes)
            and representation == game.stringRepresentation(first.copy())
        )
        canonical = game.getCanonicalForm(first, -1)
        checks["getCanonicalForm.involution"] = np.array_equal(
            first, game.getCanonicalForm(canonical, -1),
        )
        initial_policy = np.arange(1, game.getActionSize() + 1, dtype=np.float64)
        forms = game.getSymmetries(first, initial_policy)
        checks["getSymmetries.nonempty"] = bool(forms)
        checks["getSymmetries.policy_permutation"] = all(
            board.shape == first.shape
            and policy.shape == initial_policy.shape
            and np.array_equal(np.sort(policy), np.sort(initial_policy))
            for board, policy in forms
        )

        board = first
        player = 1
        seen = set()
        while plies < maximum_plies:
            ended = float(game.getGameEnded(board, player))
            if ended != 0:
                terminal_value = ended
                break
            key = (game.stringRepresentation(game.getCanonicalForm(board, player)), player)
            if key in seen:
                messages.append("deterministic first-legal rollout repeated a non-terminal state")
                break
            seen.add(key)
            valid = game.getValidMoves(board, player)
            canonical_board = game.getCanonicalForm(board, player)
            canonical_valid = game.getValidMoves(canonical_board, 1)
            if not np.array_equal(valid, canonical_valid):
                messages.append("canonical player swap changed the legal action mask")
                break
            if float(game.getGameEnded(canonical_board, 1)) != ended:
                messages.append("canonical player swap changed the outcome value")
                break
            if valid.shape != (game.getActionSize(),) or valid.dtype.kind not in ("i", "u", "b"):
                messages.append("valid-move mask has the wrong shape or dtype")
                break
            legal = np.flatnonzero(valid)
            if len(legal) == 0:
                messages.append("non-terminal state has no legal action or forced pass")
                break
            before = board.copy()
            action = int(legal[0])
            next_one, next_player_one = game.getNextState(board, player, action)
            next_two, next_player_two = game.getNextState(board, player, action)
            canonical_next, canonical_next_player = game.getNextState(canonical_board, 1, action)
            if not np.array_equal(board, before):
                messages.append("getNextState mutated its input board")
                break
            if next_player_one != -player or next_player_two != next_player_one or not np.array_equal(next_one, next_two):
                messages.append("getNextState is not deterministic strict alternation")
                break
            if canonical_next_player != -1 or not np.array_equal(
                game.getCanonicalForm(canonical_next, -1),
                game.getCanonicalForm(next_one, next_player_one),
            ):
                messages.append("canonical and native next-state branches disagree")
                break
            if game.stringRepresentation(next_one) == game.stringRepresentation(board) and action != game.pass_action:
                messages.append("a non-pass action did not change the authoritative tensor")
                break
            board, player = next_one, next_player_one
            plies += 1

        checks["getValidMoves.binary"] = not messages
        checks["getNextState.pure_deterministic"] = not messages
        checks["getGameEnded.terminal_reached"] = terminal_value != 0
        checks["rollout.bound"] = plies < maximum_plies
    except (AlphaZeroAdapterError, ValueError, TypeError, IndexError) as exc:
        messages.append(str(exc))
    passed = bool(checks) and all(checks.values()) and not messages
    return AlphaZeroConformanceReport(passed, checks, plies, terminal_value, tuple(messages))
