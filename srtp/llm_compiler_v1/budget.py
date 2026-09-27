"""Per-stage LLM cost allowances with a shared repair reserve, and retry progress.

A job dollar cap (CUBEENGINE_LLM_MAX_COST_USD) is split into a repair reserve
and stage shares. Each stage may spend its weighted share of what is still
unspent when it starts, so an early stage cannot consume the budget of stages
that have not run yet, and unspent shares flow forward. Repair calls may also
draw on the reserve. Before every request the next cost is estimated from the
observed price per token times the next request's input size plus the average
output (or, without token counts, the most expensive request so far); a request
that could exceed the allowance is not sent. Without a configured cap nothing
is enforced.
"""

from __future__ import annotations

import contextlib
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

DEFAULT_REPAIR_RESERVE = 0.25
DEFAULT_WEIGHT = 0.1

STAGED_SOURCE_WEIGHTS = {"rule_ir": 0.35, "asset_ir": 0.1, "scene_ir": 0.35, "input_ir": 0.2}
STAGED_LIFT_WEIGHTS = {"design_intent": 0.05, "rule_ir": 0.35, "asset_ir": 0.1, "scene_ir": 0.3, "input_ir": 0.2}
AGENTIC_SOURCE_WEIGHTS = {"analyst": 0.2, "rule_ir": 0.3, "asset_ir": 0.1, "scene_ir": 0.2, "input_ir": 0.1, "critic": 0.1}
AGENTIC_LIFT_WEIGHTS = {"intent": 0.05, "planner": 0.15, "rule_ir": 0.3, "asset_ir": 0.05, "scene_ir": 0.15,
                        "input_ir": 0.1, "critic": 0.2}


class CostBudget:
    def __init__(self, total_usd: Optional[float] = None, *, reserve: float = DEFAULT_REPAIR_RESERVE,
                 first_estimate_usd: Optional[float] = None) -> None:
        if total_usd is not None and not total_usd > 0:
            raise ValueError("cost cap must be positive")
        if not 0 <= reserve < 1:
            raise ValueError("repair reserve must be in [0, 1)")
        self.total = total_usd
        self.reserve = (total_usd or 0.0) * reserve
        self.first_estimate = first_estimate_usd
        self.order: List[str] = []
        self.weights: Dict[str, float] = {}
        self.stage: Optional[str] = None
        self.repair = False
        self.allowance: Dict[str, float] = {}
        self.spent_stage: Dict[str, float] = {}
        self.spent_reserve = 0.0
        self.max_request: Optional[float] = None
        self.priced_cost = 0.0
        self.priced_tokens = 0
        self.output_tokens = 0
        self.priced_requests = 0
        self.stops: List[str] = []

    @classmethod
    def from_env(cls) -> "CostBudget":
        def number(name: str) -> Optional[float]:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                raise ValueError("{0} must be a number".format(name)) from None
        reserve = number("CUBEENGINE_LLM_REPAIR_RESERVE")
        return cls(number("CUBEENGINE_LLM_MAX_COST_USD"),
                   reserve=DEFAULT_REPAIR_RESERVE if reserve is None else reserve,
                   first_estimate_usd=number("CUBEENGINE_LLM_FIRST_CALL_ESTIMATE_USD"))

    @property
    def spent(self) -> float:
        return sum(self.spent_stage.values()) + self.spent_reserve

    def plan(self, order: Sequence[str], weights: Mapping[str, float]) -> None:
        self.order = [str(item) for item in order]
        self.weights = {str(k): float(v) for k, v in weights.items()}

    def begin(self, stage: str, *, repair: bool = False) -> None:
        self.stage, self.repair = str(stage), bool(repair)
        if self.total is None or self.stage in self.allowance:
            return
        pool_left = max(0.0, self.total - self.reserve - sum(self.spent_stage.values()))
        later = [name for name in self.order if name not in self.allowance]
        if self.stage not in later:
            later.insert(0, self.stage)
        weight = self.weights.get(self.stage, DEFAULT_WEIGHT)
        total_weight = sum(self.weights.get(name, DEFAULT_WEIGHT) for name in later) or weight
        self.allowance[self.stage] = pool_left * weight / total_weight

    def estimate(self, input_tokens: Optional[int] = None) -> Optional[float]:
        if self.priced_tokens and input_tokens is not None:
            average_output = self.output_tokens / max(1, self.priced_requests)
            return self.priced_cost / self.priced_tokens * (input_tokens + average_output)
        return self.max_request if self.max_request is not None else self.first_estimate

    def before_request(self, input_tokens: Optional[int] = None) -> None:
        """Raise LLMCostBudgetExceeded when the next request could exceed the allowance."""
        if self.total is None:
            return
        estimate = self.estimate(input_tokens)
        if estimate is None:
            return  # nothing observed yet in this job
        stage = self.stage or "unstaged"
        reserve_left = self.reserve - self.spent_reserve
        if self.stage is None:
            # Callers that do not declare stages are bounded by the job cap only.
            stage_left, available = self.total - self.spent, self.total - self.spent
        else:
            stage_left = self.allowance.get(stage, 0.0) - self.spent_stage.get(stage, 0.0)
            available = max(0.0, stage_left) + (max(0.0, reserve_left) if self.repair else 0.0)
        if estimate > available or self.spent + estimate > self.total:
            from .client import LLMCostBudgetExceeded
            message = ("Stage budget: {0} has US${1:.4f} left{2} and the next {3} request is estimated at "
                       "US${4:.4f} (job spent US${5:.4f} of US${6:.2f}). Stopped before sending it; accepted "
                       "stages are kept.").format(stage, max(0.0, stage_left),
                                                  " plus US${0:.4f} repair reserve".format(max(0.0, reserve_left)) if self.repair else "",
                                                  "repair" if self.repair else "generation", estimate, self.spent, self.total)
            self.stops.append(message)
            raise LLMCostBudgetExceeded(message)

    def record(self, cost: Any, *, input_tokens: Any = None, output_tokens: Any = None) -> None:
        if type(cost) not in (int, float) or not 0 <= cost < 1_000_000:
            return
        cost = float(cost)
        if type(input_tokens) is int and type(output_tokens) is int and input_tokens >= 0 and output_tokens >= 0:
            self.priced_cost += cost
            self.priced_tokens += input_tokens + output_tokens
            self.output_tokens += output_tokens
            self.priced_requests += 1
        self.max_request = cost if self.max_request is None else max(self.max_request, cost)
        stage = self.stage or "unstaged"
        left = self.allowance.get(stage, 0.0) - self.spent_stage.get(stage, 0.0)
        charged = min(cost, max(0.0, left)) if self.total is not None and self.stage is not None else cost
        self.spent_stage[stage] = self.spent_stage.get(stage, 0.0) + charged
        self.spent_reserve += cost - charged

    def summary(self) -> Dict[str, Any]:
        return {"cap_usd": self.total, "repair_reserve_usd": round(self.reserve, 6) if self.total else None,
                "allowance_usd": {k: round(v, 6) for k, v in self.allowance.items()},
                "spent_usd": {k: round(v, 6) for k, v in self.spent_stage.items()},
                "reserve_spent_usd": round(self.spent_reserve, 6),
                "max_request_usd": self.max_request, "stops": list(self.stops)}


@contextlib.contextmanager
def budget_stage(client: Any, stage: str, *, repair: bool = False):
    """Attribute the client's requests inside the block to one pipeline stage."""
    budget = getattr(client, "budget", None)
    if budget is None:
        yield
        return
    previous = (budget.stage, budget.repair)
    budget.begin(stage, repair=repair)
    try:
        yield
    finally:
        budget.stage, budget.repair = previous


# ------------------------------------------------------------------ retries

# Entry indexes in JSON pointers ("/actions/2/") and item indexes ("[0]") only;
# other numbers and ids are part of what a diagnostic says.
_INDEXES = re.compile(r"(?<=/)\d+(?=/|:|\s|$)|\[\d+\]")


def diagnostic_signatures(diagnostics: Iterable[Any]) -> set:
    """Compare diagnostics across attempts without incidental entry indexes."""
    return {re.sub(r"\s+", " ", _INDEXES.sub("#", str(item))).strip() for item in diagnostics or []}


def made_progress(previous: Iterable[Any], current: Iterable[Any]) -> bool:
    """True when the repair removed at least one previous diagnostic."""
    before = diagnostic_signatures(previous)
    after = diagnostic_signatures(current)
    return not before or not before <= after
