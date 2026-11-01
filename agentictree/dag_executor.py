from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

from agentictree.comfy_client import ComfyClient, extract_media_outputs
from agentictree.plan_validate import topological_plan_nodes
from agentictree.registry import load_workflow_json
from agentictree.schemas import AtomicCapability, ExecutionRecord, PlanNode, WorkflowPlan
from agentictree.workflow_build import apply_load_image_filename, build_prompt_graph


def topological_order(nodes: list[PlanNode]) -> list[PlanNode]:
    return topological_plan_nodes(nodes)


def _first_load_image_node_id(graph: dict[str, Any]) -> str | None:
    for nid, node in graph.items():
        if str(nid).startswith("__"):
            continue
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            return str(nid)
    return None


def _filename_from_media(
    media: list[Any],
    index: int,
) -> str:
    if not media:
        raise ValueError("No media to bind from upstream node")
    item = media[index] if index < len(media) else media[-1]
    if isinstance(item, dict) and "filename" in item:
        return str(item["filename"])
    return str(item)


def _resolve_output_list(ctx: dict[str, dict[str, list[Any]]], ref_from: str, output: str) -> list[Any]:
    block = ctx.get(ref_from)
    if not block:
        raise ValueError(f"Unknown upstream node {ref_from!r} in bindings")
    lst = block.get(output)
    if lst is None:
        for alt in ("images", "gifs", "video", "audio"):
            if block.get(alt):
                lst = block[alt]
                break
    if not lst:
        raise ValueError(f"No output {output!r} on node {ref_from!r}; have {list(block.keys())}")
    return lst


def _copy_to_comfy_input(
    local_root: Path,
    file_meta: dict[str, Any],
    dest_name: str | None = None,
) -> Path:
    """Copy from Comfy output (or input) tree into input folder for LoadImage."""
    root = local_root.resolve()
    sub = str(file_meta.get("subfolder") or "")
    fname = str(file_meta["filename"])
    typ = str(file_meta.get("type") or "output")
    src_dir = root / typ if typ in ("output", "input", "temp") else root / "output"
    if sub:
        src = src_dir / sub / fname
    else:
        src = src_dir / fname
    if not src.is_file():
        raise FileNotFoundError(f"Expected media at {src}")
    inp = root / "input"
    inp.mkdir(parents=True, exist_ok=True)
    dst_name = dest_name or fname
    dst = inp / dst_name
    shutil.copy2(src, dst)
    return dst


class DAGExecutor:
    def __init__(
        self,
        caps: dict[str, AtomicCapability],
        client: ComfyClient | None,
        *,
        local_comfy_root: Path | None = None,
        dry_run: bool = False,
    ) -> None:
        self.caps = caps
        self.client = client
        self.local_comfy_root = local_comfy_root.resolve() if local_comfy_root else None
        self.dry_run = dry_run

    def run(self, plan: WorkflowPlan) -> list[ExecutionRecord]:
        order = topological_order(plan.nodes)
        ctx: dict[str, dict[str, list[Any]]] = {}
        records: list[ExecutionRecord] = []

        for node in order:
            rec = self._run_node(node, plan, ctx)
            records.append(rec)
        return records

    def _run_node(
        self,
        node: PlanNode,
        plan: WorkflowPlan,
        ctx: dict[str, dict[str, list[Any]]],
    ) -> ExecutionRecord:
        cap = self.caps.get(node.atomic_id)
        if not cap:
            return ExecutionRecord(
                dag_id=node.dag_id,
                atomic_id=node.atomic_id,
                status="failed",
                error=f"Unknown atomic_id {node.atomic_id!r}",
            )

        wf_path = cap.workflow_path()
        raw = load_workflow_json(wf_path)
        graph = copy.deepcopy(raw)
        if "__comment" in graph:
            del graph["__comment"]

        resolved: dict[str, Any] = dict(plan.global_inputs)
        resolved.update(node.inputs)

        for in_name, ref in node.bindings.items():
            media = _resolve_output_list(ctx, ref.from_node, ref.output)
            fname = _filename_from_media(media, ref.index)
            meta = media[min(ref.index, max(len(media) - 1, 0))]
            if isinstance(meta, dict) and "filename" in meta:
                bridged = False
                if self.local_comfy_root:
                    try:
                        copied = _copy_to_comfy_input(self.local_comfy_root, meta)
                        fname = copied.name
                        bridged = True
                    except FileNotFoundError:
                        pass
                if (
                    not bridged
                    and self.client
                    and not self.dry_run
                ):
                    try:
                        fname = self.client.relay_output_to_input(meta)
                    except Exception:
                        pass
            resolved[in_name] = fname

        graph = build_prompt_graph(cap, graph, resolved)

        for in_name, val in resolved.items():
            if in_name not in ("source_image", "prior_image", "candidate_image"):
                continue
            if not isinstance(val, str):
                continue
            lid = _first_load_image_node_id(graph)
            if lid:
                apply_load_image_filename(graph, lid, val)

        if self.dry_run or not self.client:
            ctx[node.dag_id] = {
                "images": [{"filename": f"dryrun_{node.dag_id}.png", "subfolder": "", "type": "output"}]
            }
            return ExecutionRecord(
                dag_id=node.dag_id,
                atomic_id=node.atomic_id,
                prompt_id=None,
                status="skipped",
                outputs=ctx[node.dag_id],
            )

        try:
            pid = self.client.queue_prompt(graph)
            entry = self.client.wait_for_prompt(pid)
            outs = extract_media_outputs(entry)
            if not outs:
                outs = {"raw": [str(entry)[:1500]]}
            ctx[node.dag_id] = outs
            return ExecutionRecord(
                dag_id=node.dag_id,
                atomic_id=node.atomic_id,
                prompt_id=pid,
                status="completed",
                outputs=outs,
            )
        except Exception as e:  # noqa: BLE001
            return ExecutionRecord(
                dag_id=node.dag_id,
                atomic_id=node.atomic_id,
                status="failed",
                error=str(e),
            )
