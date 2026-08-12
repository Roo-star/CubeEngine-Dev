"""Versioned deterministic randomness for authoritative Rule IR state."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


RANDOM_SERVICE_CAPABILITY_ID = "cubeengine.random-service/1.0"
PCG32_ALGORITHM = "cubeengine.pcg32/1"
RECORDED_ALGORITHM = "cubeengine.recorded/1"
SUPPORTED_ALGORITHMS = (PCG32_ALGORITHM, RECORDED_ALGORITHM)
SUPPORTED_SEED_POLICIES = ("fixed", "session", "external", "recorded")
SUPPORTED_DISTRIBUTIONS = (
    "choice", "uniform_int", "bernoulli", "weighted_choice", "shuffle", "sample",
)

_MASK_32 = (1 << 32) - 1
_MASK_64 = (1 << 64) - 1
_PCG32_MULTIPLIER = 6364136223846793005


class RandomServiceError(ValueError):
    pass


@dataclass(frozen=True)
class ChanceResult:
    sequence: int
    stream_id: str
    algorithm: str
    seed_policy: str
    draw_index: int
    distribution: Mapping[str, Any]
    result: Any
    before_state_hash: str
    after_state_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "distribution", MappingProxyType(deepcopy(dict(self.distribution))))
        object.__setattr__(self, "result", deepcopy(self.result))

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "sequence": self.sequence,
            "stream_id": self.stream_id,
            "algorithm": self.algorithm,
            "seed_policy": self.seed_policy,
            "draw_index": self.draw_index,
            "distribution": deepcopy(dict(self.distribution)),
            "result": deepcopy(self.result),
            "before_state_hash": self.before_state_hash,
            "after_state_hash": self.after_state_hash,
        }


class PCG32:
    """Minimal PCG-XSH-RR with a frozen cross-language integer contract."""

    def __init__(self, seed: int, sequence: int) -> None:
        _uint64(seed, "seed")
        _uint64(sequence, "sequence")
        self.state = 0
        self.increment = ((sequence << 1) | 1) & _MASK_64
        self.next_uint32()
        self.state = (self.state + seed) & _MASK_64
        self.next_uint32()

    def next_uint32(self) -> int:
        old_state = self.state
        self.state = (old_state * _PCG32_MULTIPLIER + self.increment) & _MASK_64
        xorshifted = (((old_state >> 18) ^ old_state) >> 27) & _MASK_32
        rotation = (old_state >> 59) & 31
        return ((xorshifted >> rotation) | (xorshifted << ((-rotation) & 31))) & _MASK_32

    def bounded(self, bound: int) -> int:
        if isinstance(bound, bool) or not isinstance(bound, int) or not 1 <= bound <= (1 << 32):
            raise RandomServiceError("PCG32 bound must be an integer from 1 through 2^32")
        threshold = ((1 << 32) - bound) % bound
        while True:
            value = self.next_uint32()
            if value >= threshold:
                return value % bound

    def snapshot(self) -> Dict[str, int]:
        return {"state": self.state, "increment": self.increment}

    def restore(self, value: Mapping[str, Any]) -> None:
        state = value.get("state")
        increment = value.get("increment")
        _uint64(state, "PCG32 state")
        _uint64(increment, "PCG32 increment")
        if increment % 2 != 1:
            raise RandomServiceError("PCG32 increment must be odd")
        self.state = state
        self.increment = increment


class RandomStream:
    def __init__(
        self, declaration: Mapping[str, Any], source: Optional[Any] = None,
    ) -> None:
        self.stream_id = str(declaration["id"])
        self.algorithm = str(declaration.get("algorithm", ""))
        self.seed_policy = str(declaration.get("seed_policy", ""))
        if self.algorithm not in SUPPORTED_ALGORITHMS:
            raise RandomServiceError("unsupported random algorithm: {0}".format(self.algorithm))
        if self.seed_policy not in SUPPORTED_SEED_POLICIES:
            raise RandomServiceError("unsupported seed policy: {0}".format(self.seed_policy))
        self.draw_index = 0
        self.recorded_index = 0
        self.recorded_values: List[Any] = []
        self.generator: Optional[PCG32] = None

        if self.seed_policy == "recorded":
            if self.algorithm != RECORDED_ALGORITHM:
                raise RandomServiceError("recorded streams require cubeengine.recorded/1")
            values = source if source is not None else declaration.get("recorded_values")
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise RandomServiceError("recorded stream requires an ordered result sequence")
            _reject_floats(values, "recorded values")
            self.recorded_values = deepcopy(list(values))
            return

        if self.algorithm != PCG32_ALGORITHM:
            raise RandomServiceError("generated streams require cubeengine.pcg32/1")
        if self.seed_policy == "fixed":
            seed = declaration.get("seed")
        else:
            seed = source
        _uint64(seed, "{0} seed".format(self.stream_id))
        sequence = declaration.get("sequence", _stable_sequence(self.stream_id))
        _uint64(sequence, "{0} sequence".format(self.stream_id))
        self.generator = PCG32(seed, sequence)

    def draw(self, distribution: Mapping[str, Any], audit_sequence: int) -> Tuple[Any, ChanceResult]:
        normalized = _normalize_distribution(distribution)
        before = self.state_hash()
        current_index = self.draw_index
        if self.seed_policy == "recorded":
            if self.recorded_index >= len(self.recorded_values):
                raise RandomServiceError("recorded random stream is exhausted: {0}".format(self.stream_id))
            result = deepcopy(self.recorded_values[self.recorded_index])
            _validate_recorded_result(normalized, result)
            self.recorded_index += 1
        else:
            if self.generator is None:
                raise RandomServiceError("random generator is not initialized")
            result = _draw_generated(self.generator, normalized)
        self.draw_index += 1
        after = self.state_hash()
        return result, ChanceResult(
            sequence=audit_sequence,
            stream_id=self.stream_id,
            algorithm=self.algorithm,
            seed_policy=self.seed_policy,
            draw_index=current_index,
            distribution=normalized,
            result=result,
            before_state_hash=before,
            after_state_hash=after,
        )

    def snapshot(self) -> Dict[str, Any]:
        result = {
            "stream_id": self.stream_id,
            "algorithm": self.algorithm,
            "seed_policy": self.seed_policy,
            "draw_index": self.draw_index,
            "recorded_index": self.recorded_index,
        }
        if self.generator is not None:
            result["generator"] = self.generator.snapshot()
        return result

    def restore(self, value: Mapping[str, Any]) -> None:
        if value.get("stream_id") != self.stream_id:
            raise RandomServiceError("random snapshot stream ID mismatch")
        if value.get("algorithm") != self.algorithm or value.get("seed_policy") != self.seed_policy:
            raise RandomServiceError("random snapshot capability mismatch")
        draw_index = value.get("draw_index")
        recorded_index = value.get("recorded_index")
        _non_negative_integer(draw_index, "draw index")
        _non_negative_integer(recorded_index, "recorded index")
        if recorded_index > len(self.recorded_values):
            raise RandomServiceError("recorded snapshot position exceeds supplied results")
        if self.generator is not None:
            generator = value.get("generator")
            if not isinstance(generator, Mapping):
                raise RandomServiceError("generated random snapshot is missing generator state")
            self.generator.restore(generator)
        elif "generator" in value:
            raise RandomServiceError("recorded random snapshot cannot contain generator state")
        self.draw_index = draw_index
        self.recorded_index = recorded_index

    def state_hash(self) -> str:
        return _canonical_hash(self.snapshot())


class DeterministicRandomService:
    def __init__(
        self, declarations: Sequence[Mapping[str, Any]],
        sources: Optional[Mapping[str, Any]] = None,
    ) -> None:
        supplied = dict(sources or {})
        declared_ids = {
            str(item["id"]) for item in declarations if isinstance(item, Mapping)
        }
        unknown = sorted(set(supplied) - declared_ids)
        if unknown:
            raise RandomServiceError("random source supplied for unknown stream: {0}".format(unknown[0]))
        self.streams: Dict[str, RandomStream] = {}
        self.next_audit_sequence = 1
        for item in declarations:
            if not isinstance(item, Mapping):
                continue
            stream_id = str(item["id"])
            if stream_id in self.streams:
                raise RandomServiceError("duplicate random stream: {0}".format(stream_id))
            self.streams[stream_id] = RandomStream(item, supplied.get(stream_id))

    def draw(self, stream_id: str, distribution: Mapping[str, Any]) -> Tuple[Any, ChanceResult]:
        if stream_id not in self.streams:
            raise RandomServiceError("unknown random stream: {0}".format(stream_id))
        result, audit = self.streams[stream_id].draw(distribution, self.next_audit_sequence)
        self.next_audit_sequence += 1
        return result, audit

    def snapshot(self) -> Dict[str, Any]:
        return {
            "capability_id": RANDOM_SERVICE_CAPABILITY_ID,
            "next_audit_sequence": self.next_audit_sequence,
            "streams": {
                stream_id: stream.snapshot()
                for stream_id, stream in sorted(self.streams.items())
            },
        }

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        if not isinstance(snapshot, Mapping) or snapshot.get("capability_id") != RANDOM_SERVICE_CAPABILITY_ID:
            raise RandomServiceError("random snapshot capability ID mismatch")
        streams = snapshot.get("streams")
        if not isinstance(streams, Mapping) or set(streams) != set(self.streams):
            raise RandomServiceError("random snapshot stream set mismatch")
        sequence = snapshot.get("next_audit_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise RandomServiceError("random snapshot audit sequence must be positive")
        staged = deepcopy(self.streams)
        for stream_id, stream in staged.items():
            value = streams[stream_id]
            if not isinstance(value, Mapping):
                raise RandomServiceError("random stream snapshot must be an object")
            stream.restore(value)
        self.streams = staged
        self.next_audit_sequence = sequence

    def state_hash(self) -> str:
        return _canonical_hash(self.snapshot())


def _draw_generated(generator: PCG32, distribution: Mapping[str, Any]) -> Any:
    kind = distribution["kind"]
    if kind == "uniform_int":
        minimum = distribution["minimum"]
        maximum = distribution["maximum"]
        return minimum + generator.bounded(maximum - minimum + 1)
    if kind == "choice":
        values = distribution["values"]
        return deepcopy(values[generator.bounded(len(values))])
    if kind == "bernoulli":
        return generator.bounded(distribution["denominator"]) < distribution["numerator"]
    if kind == "weighted_choice":
        values = distribution["values"]
        weights = distribution["weights"]
        target = generator.bounded(sum(weights))
        total = 0
        for value, weight in zip(values, weights):
            total += weight
            if target < total:
                return deepcopy(value)
        raise RandomServiceError("weighted choice failed to resolve")
    values = deepcopy(list(distribution["values"]))
    if kind == "shuffle":
        for index in range(len(values) - 1, 0, -1):
            other = generator.bounded(index + 1)
            values[index], values[other] = values[other], values[index]
        return values
    if kind == "sample":
        count = distribution["count"]
        for index in range(count):
            other = index + generator.bounded(len(values) - index)
            values[index], values[other] = values[other], values[index]
        return values[:count]
    raise RandomServiceError("unsupported distribution: {0}".format(kind))


def _normalize_distribution(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RandomServiceError("random distribution must be an object")
    kind = value.get("kind")
    if kind not in SUPPORTED_DISTRIBUTIONS:
        raise RandomServiceError("unsupported random distribution: {0}".format(kind))
    result = {"kind": str(kind)}
    if kind == "uniform_int":
        minimum = value.get("minimum")
        maximum = value.get("maximum")
        _integer(minimum, "uniform minimum")
        _integer(maximum, "uniform maximum")
        if minimum > maximum or maximum - minimum + 1 > (1 << 32):
            raise RandomServiceError("uniform integer range must contain 1 through 2^32 values")
        result.update({"minimum": minimum, "maximum": maximum})
    elif kind == "bernoulli":
        numerator = value.get("numerator")
        denominator = value.get("denominator")
        _non_negative_integer(numerator, "bernoulli numerator")
        _integer(denominator, "bernoulli denominator")
        if denominator <= 0 or denominator > (1 << 32) or numerator > denominator:
            raise RandomServiceError("bernoulli requires 0 <= numerator <= denominator <= 2^32")
        result.update({"numerator": numerator, "denominator": denominator})
    else:
        values = value.get("values")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise RandomServiceError("distribution values must be an ordered array")
        _reject_floats(values, "distribution values")
        values = deepcopy(list(values))
        if kind in ("choice", "weighted_choice") and not values:
            raise RandomServiceError("choice distribution cannot be empty")
        result["values"] = values
        if kind == "weighted_choice":
            weights = value.get("weights")
            if not isinstance(weights, Sequence) or isinstance(weights, (str, bytes)) or len(weights) != len(values):
                raise RandomServiceError("weighted choice requires one integer weight per value")
            weights = list(weights)
            for weight in weights:
                _non_negative_integer(weight, "choice weight")
            if not 1 <= sum(weights) <= (1 << 32):
                raise RandomServiceError("weight total must be from 1 through 2^32")
            result["weights"] = weights
        if kind == "sample":
            count = value.get("count")
            _non_negative_integer(count, "sample count")
            if count > len(values):
                raise RandomServiceError("sample count exceeds the value count")
            result["count"] = count
    return result


def _validate_recorded_result(distribution: Mapping[str, Any], result: Any) -> None:
    kind = distribution["kind"]
    _reject_floats(result, "recorded result")
    if kind == "uniform_int":
        if isinstance(result, bool) or not isinstance(result, int) or not distribution["minimum"] <= result <= distribution["maximum"]:
            raise RandomServiceError("recorded uniform integer is outside the declared range")
    elif kind == "bernoulli":
        if not isinstance(result, bool):
            raise RandomServiceError("recorded Bernoulli result must be boolean")
    elif kind == "choice":
        if not any(_equal(result, value) for value in distribution["values"]):
            raise RandomServiceError("recorded choice is outside the declared values")
    elif kind == "weighted_choice":
        allowed = [value for value, weight in zip(distribution["values"], distribution["weights"]) if weight > 0]
        if not any(_equal(result, value) for value in allowed):
            raise RandomServiceError("recorded weighted choice has zero or missing weight")
    elif kind == "shuffle":
        if not isinstance(result, Sequence) or isinstance(result, (str, bytes)) or _multiset(result) != _multiset(distribution["values"]):
            raise RandomServiceError("recorded shuffle is not a permutation of the declared values")
    elif kind == "sample":
        if not isinstance(result, Sequence) or isinstance(result, (str, bytes)) or len(result) != distribution["count"]:
            raise RandomServiceError("recorded sample has the wrong size")
        available = _multiset(distribution["values"])
        selected = _multiset(result)
        if any(selected.get(key, 0) > available.get(key, 0) for key in selected):
            raise RandomServiceError("recorded sample contains unavailable values")


def _stable_sequence(stream_id: str) -> int:
    return int.from_bytes(hashlib.sha256(stream_id.encode("utf-8")).digest()[:8], "big")


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _multiset(values: Sequence[Any]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for value in values:
        key = _canonical_value(value)
        result[key] = result.get(key, 0) + 1
    return result


def _equal(left: Any, right: Any) -> bool:
    try:
        return _canonical_value(left) == _canonical_value(right)
    except (TypeError, ValueError):
        return False


def _reject_floats(value: Any, path: str) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        raise RandomServiceError("{0} cannot contain binary floating-point values".format(path))
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise RandomServiceError("{0} object keys must be strings".format(path))
            _reject_floats(item, path)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            _reject_floats(item, path)
    else:
        raise RandomServiceError("{0} contains unsupported value type {1}".format(path, type(value).__name__))


def _integer(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RandomServiceError("{0} must be an integer".format(name))


def _non_negative_integer(value: Any, name: str) -> None:
    _integer(value, name)
    if value < 0:
        raise RandomServiceError("{0} must be non-negative".format(name))


def _uint64(value: Any, name: str) -> None:
    _integer(value, name)
    if not 0 <= value <= _MASK_64:
        raise RandomServiceError("{0} must be an unsigned 64-bit integer".format(name))
