from __future__ import annotations

from agentictree.collaborative_pipeline import run_collaborative_workflow_generation
from agentictree.schemas import AtomicCapability, PlanningOutcome


def run_llm_pipeline(
    user_intent: str,
    caps: dict[str, AtomicCapability],
    *,
    model: str,
    base_url: str | None = None,
    max_iterations: int = 5,
    max_verify_rounds: int | None = None,
) -> PlanningOutcome:
    """
    Multi-stage collaborative workflow generation (Algorithm 1 structure).

    `max_verify_rounds` is deprecated and ignored; use `max_iterations` on the outer staged loop.
    """
    _ = max_verify_rounds
    return run_collaborative_workflow_generation(
        user_intent,
        caps,
        model=model,
        base_url=base_url,
        max_iterations=max_iterations,
        use_llm=True,
    )
