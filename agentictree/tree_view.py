"""Human-readable projection of a versioned ACT tree."""
from __future__ import annotations

from pathlib import Path
import os
import tempfile


def render_tree_markdown(snapshot: dict) -> str:
    lines = [f"# Abstract Capability Tree — {snapshot.get('version', 'unknown')}", ""]
    tree = snapshot.get("tree", {})
    records = snapshot.get("records", {})
    for domain, tasks in sorted(tree.items(), key=lambda item: item[0].casefold()):
        lines.extend((f"## {domain}", ""))
        for task, capabilities in sorted(tasks.items(), key=lambda item: item[0].casefold()):
            lines.extend((f"### {task}", ""))
            for abstract, instances in sorted(capabilities.items(), key=lambda item: item[0].casefold()):
                lines.extend((f"#### {abstract}", ""))
                for key in sorted(instances):
                    record = records.get(key, {})
                    if record.get("kind") == "comfyui_model":
                        metadata = record.get("metadata", {})
                        label = metadata.get("name", key)
                        path = f"{metadata.get('save_path', '?')}/{metadata.get('filename', '?')}"
                        state = record.get("status", "unknown")
                        lines.append(f"- `{key}` — {label} (`{path}`) [{state}]")
                    elif record.get("kind") == "comfyui_template":
                        label = record.get("metadata", {}).get("title", record.get("title", key))
                        fmt = record.get("format", "api")
                        lines.append(f"- `{key}` — {label} [ComfyUI {fmt}]" if record.get("code_usable")
                                     else f"- `{key}` — {label} [{record.get('status', 'unknown')}]")
                    else:
                        label = record.get("title", key)
                        lines.append(f"- `{key}` — {label}")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_tree_markdown(snapshot: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(render_tree_markdown(snapshot))
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
