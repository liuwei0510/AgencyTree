"""Small, deterministic search tools over the versioned ACT snapshot."""
from __future__ import annotations

import re


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _strings(child)


def search_catalog(snapshot: dict, query: str, *, kind: str = "all", limit: int = 12) -> list[dict]:
    """Return concise, ranked catalog references without dumping workflow graphs."""
    if limit < 1:
        raise ValueError("limit must be positive")
    selected = {"all", "models", "templates", "workflows"}
    if kind not in selected:
        raise ValueError(f"kind must be one of {sorted(selected)}")
    terms = [word.casefold() for word in re.findall(r"[\w.-]+", query) if word]
    results = []
    for key, record in snapshot.get("records", {}).items():
        record_kind = record.get("kind")
        category = ("models" if record_kind == "comfyui_model" else
                    "templates" if record_kind == "comfyui_template" else "workflows")
        if kind != "all" and category != kind:
            continue
        if record.get("status") not in {"active", "available"} or record.get("code_usable") is False:
            continue
        searchable = " ".join([key, record.get("title", ""), *record.get("tree_branch", []),
                               *list(_strings(record.get("metadata", {}))),
                               *list(_strings(record.get("six_tuple", {})))]).casefold()
        score = sum(searchable.count(term) for term in terms)
        if terms and not score:
            continue
        if not terms:
            score = 1
        label = (record.get("metadata", {}).get("name") if record_kind == "comfyui_model" else
                 record.get("metadata", {}).get("title") if record_kind == "comfyui_template" else
                 record.get("title")) or key
        results.append({
            "id": key, "kind": category, "title": label,
            "tree_branch": record.get("tree_branch"),
            "atomic_workflow_path": record.get("atomic_workflow_path"),
            "workflow_format": record.get("format") or record.get("six_tuple", {}).get("invocation", {}).get("binding"),
            "code_usable": record.get("code_usable", record.get("status") == "active"),
            "six_tuple": record.get("six_tuple"),
            "source": record.get("evidence", record.get("source")),
            "score": score,
        })
    results.sort(key=lambda item: (-item["score"], item["kind"], item["title"].casefold(), item["id"]))
    return results[:limit]
