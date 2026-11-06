"""Per-stage LLM cost allowances with a shared repair reserve, and retry progress.

A job dollar cap (CUBEENGINE_LLM_MAX_COST_USD) is split into a repair reserve
and stage shares. Each stage may spend its weighted share of what is still
unspent when it starts, so an early stage cannot consume the budget of stages
that have not run yet, and unspent shares flow forward. A planned stage that
sends no request (reused from a checkpoint) takes no share. Repair calls may also
draw on the reserve. Before every request the next cost is estimated from the
observed price per token times the next request's input size plus the average
output (or, without token counts, the most expensive request so far); a request
that could exceed the allowance is not sent. Without a configured cap nothing
is enforced.

Hard limits use the worst case of the next request, computed before it is sent
from list prices: an upper bound of its input tokens at the highest input rate
(cache writes included) plus its full max_output_tokens at the output rate.
CUBEENGINE_LLM_MAX_REQUEST_USD caps every single request this way, and no
request is sent whose worst case exceeds what is left of the job cap. Prices come
from CUBEENGINE_LLM_PRICE_INPUT_PER_M / CUBEENGINE_LLM_PRICE_OUTPUT_PER_M or the
table below (mock clients have no price). With a per-request cap and no known
price nothing is sent; a job cap without a price falls back to the estimate.
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
# US$ per million tokens: worst input rate (max of input and cache write) and output rate.
# OpenRouter list prices; override with CUBEENGINE_LLM_PRICE_*_PER_M when they change.
KNOWN_PRICES_PER_M = {"openai/gpt-6-sol": (2.5, 10.0)}

AGENTIC_LIFT_WEIGHTS = {"intent": 0.05, "planner": 0.15, "rule_ir": 0.3, "asset_ir": 0.05, "scene_ir": 0.15,
                        "input_ir": 0.1, "critic": 0.2}


class CostBudget:
    def __init__(self, total_usd: Optional[float] = None, *, reserve: float = DEFAULT_REPAIR_RESERVE,
                 first_estimate_usd: Optional[float] = None, max_request_usd: Optional[float] = None,
                 prices_per_m: Optional[Sequence[float]] = None) -> None:
        if total_usd is not None and not total_usd > 0:
            raise ValueError("cost cap must be positive")
        if max_request_usd is not None and not max_request_usd > 0:
            raise ValueError("per-request cap must be positive")
        self.max_request_usd = max_request_usd
        self.configured_prices = tuple(prices_per_m) if prices_per_m else None
        self.prices = self.configured_prices
        self.last_worst_case: Optional[float] = None
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
        price_in, price_out = number("CUBEENGINE_LLM_PRICE_INPUT_PER_M"), number("CUBEENGINE_LLM_PRICE_OUTPUT_PER_M")
        if (price_in is None) != (price_out is None) or any(v is not None and v < 0 for v in (price_in, price_out)):
            raise ValueError("Set both CUBEENGINE_LLM_PRICE_INPUT_PER_M and CUBEENGINE_LLM_PRICE_OUTPUT_PER_M (US$ per million tokens).")
        return cls(number("CUBEENGINE_LLM_MAX_COST_USD"),
                   reserve=DEFAULT_REPAIR_RESERVE if reserve is None else reserve,
                   first_estimate_usd=number("CUBEENGINE_LLM_FIRST_CALL_ESTIMATE_USD"),
                   max_request_usd=number("CUBEENGINE_LLM_MAX_REQUEST_USD"),
                   prices_per_m=None if price_in is None else (price_in, price_out))

    def set_model(self, model: Optional[str]) -> None:
        """Use the model's list price unless prices were configured explicitly."""
        if self.configured_prices is None:
            self.prices = KNOWN_PRICES_PER_M.get(str(model or "").strip())

    def worst_case(self, input_tokens: Optional[int], max_output_tokens: Optional[int]) -> Optional[float]:
        """Most the next request can cost at list price, or None when the price or sizes are unknown."""
        if self.prices is None or input_tokens is None or max_output_tokens is None:
            return None
        return (input_tokens * self.prices[0] + max_output_tokens * self.prices[1]) / 1_000_000

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
        # Planned stages before this one that never sent a request (reused from a checkpoint
        # or built by the engine) are finished at no cost: their shares stay in the pool.
        position = self.order.index(self.stage) if self.stage in self.order else 0
        later = [name for name in self.order[position:] if name not in self.allowance]
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

    def before_request(self, input_tokens: Optional[int] = None, *, max_output_tokens: Optional[int] = None,
                       input_tokens_upper: Optional[int] = None) -> None:
        """Raise LLMCostBudgetExceeded when the next request could exceed the allowance."""
        hard = self.max_request_usd is not None or self.total is not None
        worst = self.worst_case(input_tokens_upper if input_tokens_upper is not None else input_tokens,
                                max_output_tokens) if hard else None
        self.last_worst_case = worst
        if self.max_request_usd is not None or (self.total is not None and max_output_tokens is not None):
            from .client import LLMCostBudgetExceeded
            if worst is None and self.max_request_usd is not None:
                message = ("CUBEENGINE_LLM_MAX_REQUEST_USD is set but this model has no known price. Set "
                           "CUBEENGINE_LLM_PRICE_INPUT_PER_M and CUBEENGINE_LLM_PRICE_OUTPUT_PER_M; nothing was sent.")
                self.stops.append(message)
                raise LLMCostBudgetExceeded(message)
            if worst is not None and self.max_request_usd is not None and worst > self.max_request_usd:
                message = ("Request cap: the next request could cost up to US${0:.4f} ({1} input tokens upper bound, "
                           "{2} max output tokens) > CUBEENGINE_LLM_MAX_REQUEST_USD={3:.2f}. Stopped before sending "
                           "it; lower CUBEENGINE_LLM_MAX_TOKENS or raise the cap.").format(
                               worst, input_tokens_upper or input_tokens, max_output_tokens, self.max_request_usd)
                self.stops.append(message)
                raise LLMCostBudgetExceeded(message)
            if worst is not None and self.total is not None and self.spent + worst > self.total:
                message = ("Job cap: the next request could cost up to US${0:.4f}, but only US${1:.4f} of "
                           "US${2:.2f} is left. Stopped before sending it; accepted stages are kept.").format(
                               worst, max(0.0, self.total - self.spent), self.total)
                self.stops.append(message)
                raise LLMCostBudgetExceeded(message)
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
                "max_request_usd": self.max_request, "request_cap_usd": self.max_request_usd,
                "last_worst_case_usd": None if self.last_worst_case is None else round(self.last_worst_case, 6),
                "stops": list(self.stops)}


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
