"""Fetch current official ComfyUI catalog and workflow examples, without a server.

Pin each repository to a resolved commit, retain raw JSON by content hash, and
never infer deletions from a failed or deliberately limited fetch.
"""
from __future__ import annotations

import ast
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx

from agentictree.storage import digest, now, read_json, write_json
from agentictree.editor_to_api import convert_editor_workflow
from agentictree.graph_validate import validate_api_graph

REPOSITORIES = {
    "models": ("Comfy-Org/ComfyUI-Manager", "model-list.json"),
    "templates": ("Comfy-Org/workflow_templates", "templates/index.json"),
}
API_EXAMPLE = ("Comfy-Org/ComfyUI", "script_examples/basic_api_example.py")


def parse_api_example_script(source: str) -> dict:
    """Extract ComfyUI's directly-published API prompt from its example script."""
    tree = ast.parse(source)
    for statement in tree.body:
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target] if isinstance(statement, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == "prompt_text" for target in targets):
            value = ast.literal_eval(statement.value)
            if not isinstance(value, str):
                break
            graph = __import__("json").loads(value)
            issues = validate_api_graph(graph)
            if issues:
                raise ValueError("Official API example is not a valid API graph: " + "; ".join(issues))
            return graph
    raise ValueError("Official API example script has no literal prompt_text JSON")


def _fetch(client: httpx.Client, url: str):
    for attempt in range(3):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            if attempt == 2:
                raise
            time.sleep(0.3 * (attempt + 1))


def sync_official(root: Path, *, limit: int | None = None, workers: int = 8,
                  offline: bool = False, convert_editor: bool = False) -> dict:
    if offline:
        cached = read_json(root / "latest.json")
        return {**cached, "fresh": False, "offline": True}
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    with httpx.Client(timeout=45, follow_redirects=True,
                      headers={"User-Agent": "agentictreeforworkflow/1"}) as client:
        provenance = {}
        payloads = {}
        for key, (repo, path) in REPOSITORIES.items():
            revision = _fetch(client, f"https://api.github.com/repos/{repo}/commits/main")["sha"]
            url = f"https://raw.githubusercontent.com/{repo}/{revision}/{path}"
            payloads[key] = _fetch(client, url)
            provenance[key] = {"repository": repo, "revision": revision, "url": url,
                               "sha256": digest(payloads[key])}
        api_repo, api_path = API_EXAMPLE
        api_revision = _fetch(client, f"https://api.github.com/repos/{api_repo}/commits/master")["sha"]
        api_url = f"https://raw.githubusercontent.com/{api_repo}/{api_revision}/{api_path}"
        response = client.get(api_url)
        response.raise_for_status()
        api_graph = parse_api_example_script(response.text)
        api_cache = root / "api_examples" / api_revision / "basic_api_example.json"
        write_json(api_cache, api_graph)
        api_example = {"name": "basic_api_example", "url": api_url, "sha256": digest(api_graph),
                       "path": str(api_cache.resolve()), "format": "api", "status": "available",
                       "metadata": {"title": "ComfyUI Basic API Example",
                                    "description": "Official API prompt graph published by ComfyUI."}}
        provenance["api_examples"] = {"repository": api_repo, "revision": api_revision, "url": api_url,
                                       "sha256": digest(api_graph)}
        models = payloads["models"].get("models")
        groups = payloads["templates"]
        if not isinstance(models, list) or not models or not isinstance(groups, list) or not groups:
            raise ValueError("Official catalog schema changed or is empty; retain previous snapshot")
        by_name = {}
        for group in groups:
            for item in group["templates"]:
                name = item["name"]
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                    raise ValueError(f"Unsafe template name: {name}")
                by_name[name] = item
        names = sorted(by_name)
        selected = names[:limit] if limit else names
        revision = provenance["templates"]["revision"]

        def download(name: str) -> dict:
            url = f"https://raw.githubusercontent.com/Comfy-Org/workflow_templates/{revision}/templates/{quote(name)}.json"
            cache = root / "revisions" / revision / f"{name}.json"
            try:
                graph = read_json(cache) if cache.exists() else _fetch(client, url)
                if not isinstance(graph, dict) or not graph:
                    raise ValueError("empty or non-object workflow")
                write_json(cache, graph)
                conversion = {"status": "not_requested"}
                if convert_editor:
                    api_graph, conversion_issues = convert_editor_workflow(graph)
                    conversion = {"status": "converted" if api_graph is not None else "needs_object_info",
                                  "issues": conversion_issues}
                    if api_graph is not None:
                        api_path = root / "api_revisions" / revision / f"{name}.json"
                        write_json(api_path, api_graph)
                        conversion.update(path=str(api_path.resolve()), sha256=digest(api_graph))
                from agentictree.catalog_contracts import validate_editor_graph
                fmt = "editor" if "nodes" in graph else "api"
                issues = validate_editor_graph(graph) if fmt == "editor" else validate_api_graph(graph)
                return {"name": name, "url": url, "sha256": digest(graph),
                        "path": str(cache.resolve()), "format": fmt,
                        "api_conversion": conversion, "metadata": by_name[name],
                        "status": "unusable_format" if issues else "available", "validation_errors": issues}
            except (httpx.HTTPError, ValueError, OSError) as error:
                return {"name": name, "url": url, "status": "fetch_failed", "error": str(error)}

        with ThreadPoolExecutor(max_workers=max(1, min(workers, 16))) as pool:
            workflows = list(pool.map(download, selected))
    complete = len(selected) == len(names) and all(x["status"] != "fetch_failed" for x in workflows)
    result = {"fetched_at": now(), "fresh": True, "offline": False, "complete": complete,
              "provenance": provenance, "models": models,
              "template_index": groups, "template_count": len(names), "workflows": workflows,
              "api_examples": [api_example]}
    result["version"] = "sources-" + digest({
        "commits": {key: value["revision"] for key, value in provenance.items()},
        "models": provenance["models"]["sha256"],
        "index": provenance["templates"]["sha256"],
        "api_examples": provenance["api_examples"]["sha256"],
        "workflows": [(item["name"], item.get("sha256"), item["status"]) for item in workflows],
    })[:16]
    write_json(root / "snapshots" / f"{result['version']}.json", result)
    # Failed partial runs never displace a complete source snapshot.
    write_json(root / "last_attempt.json", result)
    if complete:
        write_json(root / "latest.json", result)
    return result
