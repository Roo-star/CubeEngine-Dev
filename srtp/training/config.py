"""Bounded run settings shared by the UI and worker."""
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class TrainingConfig:
    iterations: int = 1
    episodes: int = 2
    simulations: int = 8
    epochs: int = 1
    arena_games: int = 2
    channels: int = 16
    batch_size: int = 32
    seed: int = 7
    learning_rate: float = 0.001
    acceptance: float = 0.55
    device: str = "cpu"

    def validate(self):
        bounds = {"iterations": (1, 1000), "episodes": (1, 1000), "simulations": (2, 1600),
                  "epochs": (1, 100), "arena_games": (2, 200), "channels": (8, 128),
                  "batch_size": (2, 256), "seed": (0, 2147483647)}
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("{0} must be an integer in {1}..{2}".format(name, low, high))
        if self.arena_games % 2:
            raise ValueError("Arena games must be even so each model starts equally often.")
        if not math.isfinite(self.learning_rate) or not 0 < self.learning_rate <= 0.1:
            raise ValueError("Learning rate must be between 0 (exclusive) and 0.1.")
        if not math.isfinite(self.acceptance) or not 0.5 <= self.acceptance <= 1:
            raise ValueError("Acceptance threshold must be between 0.5 and 1.")
        if self.device not in ("cpu", "cuda"):
            raise ValueError("Device must be cpu or cuda.")
        return self

    def to_mapping(self):
        return asdict(self)
