from __future__ import annotations

import copy
import json
from typing import Any

from agentictree.plan_validate import topological_plan_nodes
from agentictree.graph_validate import validate_api_graph
from agentictree.registry import load_capability_graph
from agentictree.schemas import AtomicCapability, WorkflowPlan
from agentictree.workflow_build import apply_load_image_filename, build_prompt_graph


def _first_load_image_node_id(graph: dict[str, Any]) -> str | None:
    for nid, node in graph.items():
        if str(nid).startswith("__"):
            continue
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            return str(nid)
    return None


def _placeholder_binding_filename(ref_from: str, output: str, index: int, target_type: str = 'image') -> str:
    safe = f"{ref_from}_{output}_{index}".replace(" ", "_")
    from agentictree.thesis_pipeline import media_type
    ext={'image':'png','video':'mp4','audio':'wav','mesh':'glb','text':'txt'}.get(media_type(target_type),'bin')
    return f"__PLACEHOLDER_{safe}.{ext}"


def materialize_ewc(
    plan: WorkflowPlan,
    caps: dict[str, AtomicCapability],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """
    Build per–dag_id Comfy workflow JSON (Executable Workflow Code) from AWG + ACT.
    Binding-driven image inputs use deterministic placeholders for offline validation.
    """
    issues: list[str] = []
    graphs: dict[str, dict[str, Any]] = {}

    try:
        order = topological_plan_nodes(plan.nodes)
    except ValueError as e:
        return {}, [f"DAG: {e}"]

    for node in order:
        cap = caps.get(node.atomic_id)
        if not cap:
            issues.append(f"Node {node.dag_id}: unknown atomic_id {node.atomic_id!r}")
            continue
        wf_path = cap.workflow_path()
        if not wf_path.is_file():
            issues.append(f"Node {node.dag_id}: workflow file missing: {wf_path}")
            continue

        try:
            raw = load_capability_graph(cap)
        except (OSError, json.JSONDecodeError) as e:
            issues.append(f"Node {node.dag_id}: cannot load workflow JSON: {e}")
            continue

        graph = copy.deepcopy(raw)
        if "__comment" in graph:
            del graph["__comment"]

        resolved: dict[str, Any] = dict(plan.global_inputs)
        resolved.update(node.inputs)
        for in_name, ref in node.bindings.items():
            typ=next((i.type for i in cap.inputs if i.name==in_name),'image')
            resolved[in_name] = _placeholder_binding_filename(ref.from_node, ref.output, ref.index,typ)

        try:
            graph = build_prompt_graph(cap, graph, resolved)
        except Exception as e:  # noqa: BLE001
            issues.append(f"Node {node.dag_id}: build_prompt_graph failed: {e}")
            continue

        for in_name, val in resolved.items():
            if in_name not in ("source_image", "prior_image", "candidate_image"):
                continue
            if not isinstance(val, str):
                continue
            lid = _first_load_image_node_id(graph)
            if lid:
                apply_load_image_filename(graph, lid, val)

        from agentictree.engineering import mask_issues, align_video_frames
        preconditions = mask_issues(graph,resolved)
        if preconditions:
            issues.extend(f'Node {node.dag_id}: {item}' for item in preconditions)
            continue
        try:
            graph = align_video_frames(graph)
        except ValueError as error:
            issues.append(f'Node {node.dag_id}: {error}')
            continue

        for logical_name, inj in cap.inject.items():
            if logical_name not in resolved:
                continue
            n = (next((n for n in graph.get('nodes', []) if str(n.get('id')) == inj.node_id), None)
                 if cap.workflow_format == 'editor' else graph.get(inj.node_id))
            if not isinstance(n, dict):
                issues.append(
                    f"Node {node.dag_id}: inject target node_id {inj.node_id!r} missing in workflow graph"
                )

        from agentictree.catalog_contracts import validate_editor_graph
        graph_issues = (validate_editor_graph(graph) if cap.workflow_format == 'editor' else validate_api_graph(graph))
        issues.extend(f"Node {node.dag_id}: {item}" for item in graph_issues)
        if not graph_issues:
            graphs[node.dag_id] = graph

    return graphs, issues


def validate_ewc_files(plan: WorkflowPlan, caps: dict[str, AtomicCapability]) -> list[str]:
    """Lightweight check without full materialize (used for fast failures)."""
    issues: list[str] = []
    for n in plan.nodes:
        cap = caps.get(n.atomic_id)
        if not cap:
            continue
        p = cap.workflow_path()
        if not p.is_file():
            issues.append(f"Node {n.dag_id}: workflow file missing: {p}")
    return issues
