from __future__ import annotations

import json
from pathlib import Path

import yaml

from agentictree.schemas import AtomicCapability, InjectSpec, ManifestInput, ManifestOutput


def _parse_inject(raw: dict) -> dict[str, InjectSpec]:
    out: dict[str, InjectSpec] = {}
    for k, v in (raw or {}).items():
        if not isinstance(v, dict):
            continue
        out[k] = InjectSpec(node_id=str(v["node_id"]), input_key=str(v["input_key"]), widget_index=v.get("widget_index"))
    return out


def _parse_inputs(raw: list) -> list[ManifestInput]:
    items: list[ManifestInput] = []
    for x in raw or []:
        if not isinstance(x, dict):
            continue
        items.append(
            ManifestInput(
                name=str(x["name"]),
                type=str(x.get("type", "string")),
                required=bool(x.get("required", True)),
                default=x.get("default"),
                description=x.get("description"),
            )
        )
    return items


def _parse_outputs(raw: list) -> list[ManifestOutput]:
    items: list[ManifestOutput] = []
    for x in raw or []:
        if not isinstance(x, dict):
            continue
        items.append(
            ManifestOutput(
                name=str(x["name"]),
                type=str(x.get("type", "string")),
                description=x.get("description"),
            )
        )
    return items


def _tree_branch(path: Path) -> tuple[str, str, str] | None:
    parts = path.resolve().parts
    if "atomic_workflows" not in parts:
        return None
    i = parts.index("atomic_workflows")
    rest = list(parts[i + 1 : -1])
    if len(rest) >= 3:
        return (rest[0], rest[1], rest[2])
    return None


def load_manifest(path: Path) -> AtomicCapability:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    branch = _tree_branch(path)

    cap = AtomicCapability(
        id=str(data["id"]),
        title=str(data.get("title", data["id"])),
        modality=list(data.get("modality") or []),
        description=str(data.get("description") or ""),
        tags=list(data.get("tags") or []),
        workflow_file=str(data["workflow_file"]),
        workflow_format=data.get("workflow_format", "api"),
        inject=_parse_inject(data.get("inject") or {}),
        inputs=_parse_inputs(data.get("inputs")),
        outputs=_parse_outputs(data.get("outputs")),
        fixed_constraints=data.get("fixed_constraints") or {},
        manifest_path=path.resolve(),
        tree_branch=branch,
    )
    return cap


def scan_registry(root: Path) -> dict[str, AtomicCapability]:
    root = root.resolve()
    by_id: dict[str, AtomicCapability] = {}
    for path in sorted(root.rglob("*.manifest.yaml")):
        cap = load_manifest(path)
        parts = path.relative_to(root).parts[:-1]
        if len(parts) >= 3:
            cap.tree_branch = tuple(parts[:3])
        if cap.id in by_id:
            raise ValueError(f"Duplicate capability id {cap.id}: {path}")
        by_id[cap.id] = cap
    return by_id


def catalog_markdown(caps: dict[str, AtomicCapability]) -> str:
    lines: list[str] = ["# Atomic capability catalog", ""]
    for cap in sorted(caps.values(), key=lambda c: c.id):
        branch = "/".join(cap.tree_branch) if cap.tree_branch else "?"
        lines.append(f"- **{cap.id}** ({branch})")
        lines.append(f"  - {cap.title}")
        if cap.modality:
            lines.append(f"  - modality: {', '.join(cap.modality)}")
        if cap.tags:
            lines.append(f"  - tags: {', '.join(cap.tags)}")
        ins = ", ".join(f"{i.name}:{i.type}" for i in cap.inputs) or "(none)"
        outs = ", ".join(f"{o.name}:{o.type}" for o in cap.outputs) or "(none)"
        lines.append(f"  - inputs: {ins}")
        lines.append(f"  - outputs: {outs}")
        lines.append("")
    return "\n".join(lines)


def load_workflow_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_capability_graph(cap: AtomicCapability) -> dict:
    graph = load_workflow_json(cap.workflow_path())
    if cap.workflow_sha256:
        from agentictree.storage import digest
        if digest(graph) != cap.workflow_sha256:
            raise ValueError(f'Immutable graph object changed: {cap.id}')
    return graph
