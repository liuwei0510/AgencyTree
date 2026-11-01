"""Deterministic planner compatibility API; never substitutes another modality."""
from agentictree.thesis_pipeline import tool_analyze, build_awg, instantiate


def plan_without_llm(user_intent, caps=None):
    if caps is None:
        raise ValueError('ACT capability contracts are required for tool planning')
    att=tool_analyze(user_intent)
    plan,_=instantiate(att,build_awg(att),caps)
    return plan
