from __future__ import annotations

from collections import deque

from agentictree.schemas import AtomicCapability, PlanNode, WorkflowPlan


def topological_plan_nodes(nodes: list[PlanNode]) -> list[PlanNode]:
    """Topological order of plan nodes (raises ValueError if cycle or broken deps)."""
    by_id = {n.dag_id: n for n in nodes}
    pending = {n.dag_id: set(n.depends_on) for n in nodes}
    ready = deque([nid for nid, deps in pending.items() if not deps])
    ordered: list[str] = []
    while ready:
        nid = ready.popleft()
        ordered.append(nid)
        for other, deps in pending.items():
            if nid in deps:
                deps.remove(nid)
                if not deps and other not in ordered and other not in ready:
                    ready.append(other)
    if len(ordered) != len(nodes):
        raise ValueError("cycle detected or missing dependency in DAG")
    return [by_id[i] for i in ordered]


def _dag_toposort_or_error(nodes: list[PlanNode]) -> None:
    topological_plan_nodes(nodes)


def structural_validate(plan: WorkflowPlan, caps: dict[str, AtomicCapability]) -> list[str]:
    """Deterministic checks before / alongside the LLM verifier."""
    issues: list[str] = []
    if not plan.nodes:
        issues.append("Workflow plan must contain at least one node")
    by_id = {n.dag_id: n for n in plan.nodes}
    if len(by_id) != len(plan.nodes):
        issues.append("Duplicate dag_id values in plan.nodes")

    try:
        _dag_toposort_or_error(plan.nodes)
    except ValueError as e:
        issues.append(f"DAG: {e}")

    out_names_by_node: dict[str, set[str]] = {}
    for n in plan.nodes:
        cap = caps.get(n.atomic_id)
        if not cap:
            issues.append(f"Node {n.dag_id}: unknown atomic_id {n.atomic_id!r}")
            continue
        out_names_by_node[n.dag_id] = {o.name for o in cap.outputs}

    for n in plan.nodes:
        cap = caps.get(n.atomic_id)
        if not cap:
            continue
        for dep in n.depends_on:
            if dep not in by_id:
                issues.append(f"Node {n.dag_id}: depends_on references missing dag_id {dep!r}")
        for in_name, ref in n.bindings.items():
            if ref.index < 0:
                issues.append(f"Node {n.dag_id}: negative binding index")
            target = next((i for i in cap.inputs if i.name == in_name), None)
            if target is None:
                issues.append(f"Node {n.dag_id}: unknown input binding {in_name}")
            if ref.from_node not in by_id:
                issues.append(f"Node {n.dag_id}: binding {in_name} from unknown node {ref.from_node!r}")
            elif ref.from_node not in n.depends_on:
                issues.append(
                    f"Node {n.dag_id}: binding {in_name} from {ref.from_node} "
                    f"but that id is not listed in depends_on"
                )
            ups = out_names_by_node.get(ref.from_node)
            if ups and ref.output not in ups:
                issues.append(
                    f"Node {n.dag_id}: binding expects output {ref.output!r} from {ref.from_node}; "
                    f"upstream manifest has {sorted(ups)}"
                )
            upstream = caps.get(by_id[ref.from_node].atomic_id) if ref.from_node in by_id else None
            output = next((o for o in upstream.outputs if o.name == ref.output), None) if upstream else None
            if target and output and target.type != output.type and target.type != "any":
                issues.append(f"Node {n.dag_id}: binding type {output.type} != {target.type}")

        provided = set(plan.global_inputs) | set(n.inputs) | set(n.bindings)
        for inp in cap.inputs:
            if not inp.required:
                continue
            if inp.name in provided:
                continue
            if inp.default is not None:
                continue
            issues.append(
                f"Node {n.dag_id} ({n.atomic_id}): required manifest input {inp.name!r} "
                f"not in global_inputs, node.inputs, or bindings"
            )

    return issues
