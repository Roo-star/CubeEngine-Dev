"""The minimal SRTP → STAL spatial-topology contract.

STAL deliberately knows only the shape of space.  SRTP owns game-specific
meaning: players, turns, movement, victory, draws, terrain semantics and any
natural-language interpretation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple


class RuleInputError(ValueError):
    """Raised when an SRTP object cannot safely create a 3D topology."""


@dataclass(frozen=True)
class GridRules:
    """The topology slice of an SRTP 3D Rule Schema.

    ``x``, ``y`` and ``z`` are the only mandatory designer-supplied values for
    STAL Function 1.  The engine fixes the zero-based coordinate convention and
    initialises every cell to the neutral state ``0``.  It does *not* infer what
    any non-zero state represents.
    """

    x: int
    y: int
    z: int
    game_id: str = "grid_topology"
    schema_version: str = "cubeengine.srtp-stal/topology-v1"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in self.dimensions):
            raise RuleInputError("dimensions.x, dimensions.y and dimensions.z must be positive integers")
        if not isinstance(self.game_id, str) or not self.game_id.strip():
            raise RuleInputError("game_id must be a non-empty string")
        if not isinstance(self.metadata, Mapping):
            raise RuleInputError("metadata must be a mapping")

    @property
    def dimensions(self) -> Tuple[int, int, int]:
        """Public coordinate and NumPy index order: ``(X, Y, Z)``."""

        return (self.x, self.y, self.z)

    @property
    def cell_count(self) -> int:
        return self.x * self.y * self.z

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GridRules":
        """Extract only STAL's topology contract from a complete SRTP object.

        Other keys (for example ``players``, ``turn_order`` or
        ``win_condition``) are intentionally ignored here.  They remain SRTP
        data and may later register validators/evaluators against the board.
        """

        if not isinstance(value, Mapping):
            raise RuleInputError("rule object must be a mapping")
        dimensions = value.get("dimensions")
        if isinstance(dimensions, Mapping):
            try:
                x, y, z = dimensions["x"], dimensions["y"], dimensions["z"]
            except KeyError as error:
                raise RuleInputError("dimensions must define x, y and z") from error
        elif isinstance(dimensions, Sequence) and not isinstance(dimensions, (str, bytes)) and len(dimensions) == 3:
            x, y, z = dimensions
        else:
            raise RuleInputError("dimensions must be {x, y, z} or a three-item list")

        metadata = value.get("metadata", {})
        return cls(
            x=x,
            y=y,
            z=z,
            game_id=value.get("game_id", "grid_topology"),
            schema_version=value.get("schema_version", "cubeengine.srtp-stal/topology-v1"),
            metadata=dict(metadata),
        )

    def to_mapping(self) -> Dict[str, Any]:
        """Return STAL's serialisable topology object for SRTP and local files."""

        return {
            "schema_version": self.schema_version,
            "game_id": self.game_id,
            "dimensions": {"x": self.x, "y": self.y, "z": self.z},
            "metadata": dict(self.metadata),
        }


def load_rules_json(path: Path) -> GridRules:
    """Load the topology portion of a JSON SRTP rule file."""

    try:
        with Path(path).open("r", encoding="utf-8") as file:
            value = json.load(file)
    except OSError as error:
        raise RuleInputError("could not read rule file: {0}".format(path)) from error
    except json.JSONDecodeError as error:
        raise RuleInputError("rule file is not valid JSON: {0}".format(error.msg)) from error
    return GridRules.from_mapping(value)


_DIMENSIONS_PATTERN = re.compile(
    r"(?P<x>\d+)\s*(?:x|X|×)\s*(?P<y>\d+)\s*(?:x|X|×)\s*(?P<z>\d+)",
    re.IGNORECASE,
)


def parse_rule_text(text: str) -> GridRules:
    """Temporary offline helper: extract only an explicit ``X × Y × Z``.

    Full natural-language understanding belongs to SRTP and will use the GPT
    adapter once SRTP work begins.  This helper purposely makes no guesses
    about players, moves, turns or victory conditions.
    """

    if not isinstance(text, str) or not text.strip():
        raise RuleInputError("natural-language rule text is empty")
    dimensions_match = _DIMENSIONS_PATTERN.search(text)
    if not dimensions_match:
        raise RuleInputError("please specify dimensions like '3 x 3 x 3'")
    x, y, z = (int(dimensions_match.group(name)) for name in ("x", "y", "z"))
    return GridRules(x=x, y=y, z=z, game_id="text_topology")
