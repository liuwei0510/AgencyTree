"""Compatibility entry point for the thesis ATT -> AWG -> engineering pipeline."""
from __future__ import annotations

from agentictree.backends import make_backend
from agentictree.schemas import PlanningOutcome
from agentictree.thesis_pipeline import run_generation


def run_collaborative_workflow_generation(user_intent, caps, *, model, base_url=None,
                                          max_iterations=5, use_llm=True, backend=None):
    if use_llm and backend is None:
        backend=make_backend({'provider':'api','model':model,'base_url':base_url})
    snapshot={'records':{key:{**cap.model_dump(mode='json'),'status':'active'} for key,cap in caps.items()}}
    result=run_generation(user_intent,snapshot,caps,backend=backend if use_llm else None,
                          max_iterations=max_iterations)
    return PlanningOutcome(analyst=result['att'],plan=result['awg'],execution_plan=result['execution_plan'],
        ewc=result['ewc'],converged=result['converged'],generation_mode=result['generation_mode'],
        validation_log=result['validation_log'],failure_reason=result['failure_reason'],
        stage_state='CONVERGED' if result['converged'] else 'FAILED')
