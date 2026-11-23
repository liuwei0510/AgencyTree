from __future__ import annotations

import copy
from typing import Any

from agentictree.schemas import AtomicCapability


def _set_nested(d: dict, key: str, value: Any) -> None:
    if key not in d:
        d[key] = value
        return
    cur = d[key]
    if isinstance(cur, list) and isinstance(value, list):
        d[key] = value
    else:
        d[key] = value


def build_prompt_graph(
    cap: AtomicCapability,
    workflow: dict[str, Any],
    resolved_inputs: dict[str, Any],
) -> dict[str, Any]:
    """Return a deep copy of the workflow with manifest inject slots filled."""
    g = copy.deepcopy(workflow)
    if "__comment" in g:
        del g["__comment"]

    resolved_inputs = dict(resolved_inputs)
    for inp in cap.inputs:
        if inp.name not in resolved_inputs and inp.default is not None:
            resolved_inputs[inp.name] = inp.default

    for logical_name, inj in cap.inject.items():
        if logical_name not in resolved_inputs:
            continue
        val = resolved_inputs[logical_name]
        if cap.workflow_format == "editor":
            from agentictree.catalog_contracts import set_graph_field
            set_graph_field(g, inj.model_dump(), val)
            continue
        node = g.get(inj.node_id)
        if not isinstance(node, dict):
            raise ValueError(f"Missing injection target {inj.node_id} for {logical_name}")
        inputs = node.setdefault("inputs", {})
        _set_nested(inputs, inj.input_key, val)
    return g


def apply_load_image_filename(
    graph: dict[str, Any],
    node_id: str,
    filename: str,
) -> None:
    node = graph.get(node_id)
    if not isinstance(node, dict):
        return
    if node.get("class_type") != "LoadImage":
        return
    inputs = node.setdefault("inputs", {})
    inputs["image"] = filename
