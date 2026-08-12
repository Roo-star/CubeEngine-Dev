"""Deterministic bounded-grid topology queries used by Rule IR v2.

The helpers in this module know nothing about any particular game.  They only
operate on coordinates, shapes and dense topology-site state.  Game semantics
such as Othello capture are assembled in Rule IR from these primitives.
"""

from __future__ import annotations

from collections import deque
from itertools import product
from typing import Any, Iterable, Sequence, Tuple

import numpy as np


Coordinate = Tuple[int, ...]
Direction = Tuple[int, ...]


class TopologyQueryError(ValueError):
    pass


def validate_shape(shape: Sequence[int]) -> Tuple[int, ...]:
    if not isinstance(shape, Sequence) or isinstance(shape, (str, bytes)) or not shape:
        raise TopologyQueryError("topology shape must be a non-empty integer sequence")
    result = tuple(shape)
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in result):
        raise TopologyQueryError("topology extents must be positive integers")
    return result


def validate_coordinate(shape: Sequence[int], coordinate: Sequence[int]) -> Coordinate:
    bounds = validate_shape(shape)
    if not isinstance(coordinate, Sequence) or isinstance(coordinate, (str, bytes)):
        raise TopologyQueryError("coordinate must be an integer sequence")
    result = tuple(coordinate)
    if len(result) != len(bounds):
        raise TopologyQueryError("coordinate rank does not match topology")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in result):
        raise TopologyQueryError("coordinate values must be integers")
    if any(value < 0 or value >= bounds[index] for index, value in enumerate(result)):
        raise TopologyQueryError("coordinate is outside topology")
    return result


def directions(rank: int, include_diagonals: bool = True) -> Tuple[Direction, ...]:
    """Return canonical unit directions in deterministic lexicographic order."""

    if isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0:
        raise TopologyQueryError("topology rank must be a positive integer")
    candidates = tuple(product((-1, 0, 1), repeat=rank))
    if include_diagonals:
        return tuple(item for item in candidates if any(item))
    return tuple(item for item in candidates if sum(value != 0 for value in item) == 1)


def neighbors(
    shape: Sequence[int], coordinate: Sequence[int], include_diagonals: bool = True,
) -> Tuple[Coordinate, ...]:
    bounds = validate_shape(shape)
    origin = validate_coordinate(bounds, coordinate)
    result = []
    for direction in directions(len(bounds), include_diagonals):
        candidate = tuple(origin[index] + direction[index] for index in range(len(bounds)))
        if _contains(bounds, candidate):
            result.append(candidate)
    return tuple(result)


def ray(shape: Sequence[int], origin: Sequence[int], direction: Sequence[int]) -> Tuple[Coordinate, ...]:
    """Return sites after ``origin`` until the first bounded edge."""

    bounds = validate_shape(shape)
    start = validate_coordinate(bounds, origin)
    step = _validate_direction(len(bounds), direction)
    result = []
    current = start
    while True:
        current = tuple(current[index] + step[index] for index in range(len(bounds)))
        if not _contains(bounds, current):
            return tuple(result)
        result.append(current)


def region(
    shape: Sequence[int], origin: Sequence[int], radius: int, metric: str = "manhattan",
) -> Tuple[Coordinate, ...]:
    """Return a bounded region including its origin, distance then coordinate ordered."""

    bounds = validate_shape(shape)
    start = validate_coordinate(bounds, origin)
    if isinstance(radius, bool) or not isinstance(radius, int) or radius < 0:
        raise TopologyQueryError("region radius must be a non-negative integer")
    if metric not in ("manhattan", "chebyshev"):
        raise TopologyQueryError("region metric must be manhattan or chebyshev")

    def distance(coordinate: Coordinate) -> int:
        deltas = tuple(abs(coordinate[index] - start[index]) for index in range(len(bounds)))
        return sum(deltas) if metric == "manhattan" else max(deltas)

    sites = product(*(range(size) for size in bounds))
    return tuple(sorted((site for site in sites if distance(site) <= radius), key=lambda site: (distance(site), site)))


def connected(
    shape: Sequence[int], sites: Iterable[Sequence[int]], include_diagonals: bool = False,
) -> bool:
    """Return whether a non-empty set of sites forms one connected component."""

    bounds = validate_shape(shape)
    remaining = {validate_coordinate(bounds, site) for site in sites}
    if not remaining:
        return False
    frontier = deque([min(remaining)])
    visited = set()
    while frontier:
        current = frontier.popleft()
        if current in visited:
            continue
        visited.add(current)
        for candidate in neighbors(bounds, current, include_diagonals):
            if candidate in remaining and candidate not in visited:
                frontier.append(candidate)
    return visited == remaining


def shortest_path(
    shape: Sequence[int],
    start: Sequence[int],
    goal: Sequence[int],
    blocked: Iterable[Sequence[int]] = (),
    include_diagonals: bool = False,
) -> Tuple[Coordinate, ...]:
    """Return one deterministic shortest path, including start and goal.

    Equal-length alternatives are resolved by canonical neighbor order.  An
    empty tuple means that either endpoint is blocked or the goal is
    unreachable.
    """

    bounds = validate_shape(shape)
    origin = validate_coordinate(bounds, start)
    target = validate_coordinate(bounds, goal)
    obstacles = {validate_coordinate(bounds, site) for site in blocked}
    if origin in obstacles or target in obstacles:
        return ()
    if origin == target:
        return (origin,)

    frontier = deque([origin])
    predecessor = {origin: None}
    while frontier:
        current = frontier.popleft()
        for candidate in neighbors(bounds, current, include_diagonals):
            if candidate in obstacles or candidate in predecessor:
                continue
            predecessor[candidate] = current
            if candidate == target:
                path = [target]
                while predecessor[path[-1]] is not None:
                    path.append(predecessor[path[-1]])
                return tuple(reversed(path))
            frontier.append(candidate)
    return ()


def bracketed_run(
    array: np.ndarray,
    origin: Sequence[int],
    direction: Sequence[int],
    middle_value: Any,
    end_value: Any,
) -> Tuple[Coordinate, ...]:
    """Return a contiguous middle-value run terminated by end-value.

    The origin itself is not inspected.  An empty result means that the ray
    does not begin with at least one ``middle_value`` followed immediately by
    ``end_value`` before reaching a boundary or any other value.
    """

    start = validate_coordinate(array.shape, origin)
    sites = ray(array.shape, start, direction)
    captured = []
    for coordinate in sites:
        value = array[coordinate]
        if value == middle_value:
            captured.append(coordinate)
            continue
        if value == end_value and captured:
            return tuple(captured)
        return ()
    return ()


def bracketed_sites(
    array: np.ndarray,
    origin: Sequence[int],
    middle_value: Any,
    end_value: Any,
    include_diagonals: bool = True,
) -> Tuple[Coordinate, ...]:
    """Combine bracketed runs once each in lexicographic coordinate order."""

    start = validate_coordinate(array.shape, origin)
    result = []
    seen = set()
    for direction in directions(array.ndim, include_diagonals):
        for coordinate in bracketed_run(array, start, direction, middle_value, end_value):
            if coordinate not in seen:
                seen.add(coordinate)
                result.append(coordinate)
    return tuple(sorted(result))


def has_bracketed_site(
    array: np.ndarray,
    empty_value: Any,
    middle_value: Any,
    end_value: Any,
    include_diagonals: bool = True,
) -> bool:
    """Return whether any empty origin brackets a run on at least one ray."""

    for coordinate in product(*(range(size) for size in array.shape)):
        if array[coordinate] == empty_value and bracketed_sites(
            array, coordinate, middle_value, end_value, include_diagonals,
        ):
            return True
    return False


def _validate_direction(rank: int, direction: Sequence[int]) -> Direction:
    if not isinstance(direction, Sequence) or isinstance(direction, (str, bytes)):
        raise TopologyQueryError("direction must be an integer sequence")
    result = tuple(direction)
    if len(result) != rank:
        raise TopologyQueryError("direction rank does not match topology")
    if any(isinstance(value, bool) or not isinstance(value, int) or value not in (-1, 0, 1) for value in result):
        raise TopologyQueryError("direction components must be -1, 0 or 1")
    if not any(result):
        raise TopologyQueryError("direction cannot be the zero vector")
    return result


def _contains(shape: Sequence[int], coordinate: Sequence[int]) -> bool:
    return len(shape) == len(coordinate) and all(
        0 <= coordinate[index] < shape[index] for index in range(len(shape))
    )
