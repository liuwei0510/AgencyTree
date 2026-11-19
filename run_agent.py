#!/usr/bin/env python3
"""Maintain ACT, generate ATT/AWG/EWC assets, and evolve saved workflows."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import subprocess
import sys
import httpx
from pathlib import Path

from agentictree.assets import generate_asset
from agentictree.backends import make_backend
from agentictree.catalog_tools import search_catalog
from agentictree.config import load_config
from agentictree.evolution import evolve_asset
from agentictree.knowledge import active_capabilities, update_act
from agentictree.sources import sync_official
from agentictree.storage import read_json, write_json
from agentictree.tree_view import render_tree_markdown

ROOT = Path(__file__).resolve().parent


def resolve(value) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync", help="Fetch official model catalog and example workflows")
    sync.add_argument("--limit", type=int, help="Download this many workflows; marks snapshot incomplete")
    sync.add_argument("--offline", action="store_true", help="Read last complete cache, explicitly stale")
    sync.add_argument("--convert-editor", action="store_true", help="Also attempt optional editor-to-API conversion for fetched templates")
    maintain = sub.add_parser("maintain", help="Publish ACT snapshot and evidence-backed changes")
    maintain.add_argument("--sync", action="store_true")
    maintain.add_argument("--agent-review", action="store_true", help="Use one optional agent review after deterministic ACT tools")
    maintain.add_argument("--no-llm", action="store_true", help="Compatibility flag; tool-only maintenance is the default")
    sub.add_parser("catalog")
    search = sub.add_parser("search", help="Find models, ComfyUI examples, or local atomic workflows")
    search.add_argument("query", help="Words from a model, capability, workflow title, or description")
    search.add_argument("--kind", choices=("all", "models", "templates", "workflows"), default="all")
    search.add_argument("--limit", type=int, default=12)
    export = sub.add_parser('export',help='Export one connected ComfyUI workflow from a saved asset')
    export.add_argument('asset',type=Path)
    export.add_argument('--output',required=True,type=Path)
    export.add_argument('--api',action='store_true',help='Explicitly convert a single editor workflow to API format when possible')
    comfy = sub.add_parser('comfy', help='Optional ComfyUI installation and service management')
    comfy.add_argument('action', choices=('status', 'install', 'update', 'start'))
    comfy.add_argument('--check-updates', action='store_true')
    comfy.add_argument('--comfy-url')
    comfy.add_argument('--comfy-mode', choices=('auto', 'local', 'remote'))
    execute = sub.add_parser('execute', help='Execute a saved asset or exported API workflow')
    execute.add_argument('asset', type=Path)
    execute.add_argument('--comfy-policy', choices=('prompt', 'auto', 'never'))
    execute.add_argument('--dry-run', action='store_true')
    execute.add_argument('--comfy-url')
    execute.add_argument('--comfy-mode', choices=('auto', 'local', 'remote'))
    execute.add_argument('--output', type=Path)
    for name in ("plan", "run"):
        child = sub.add_parser(name)
        child.add_argument("intent")
        child.add_argument("--no-llm", action="store_true")
        child.add_argument("--agent-assisted", action="store_true", help="Allow an agent fallback if deterministic tools cannot build a valid workflow")
        child.add_argument("--output", type=Path)
        child.add_argument("--att", type=Path, help="Host agent's implementation-independent ATT JSON")
        child.add_argument("--awg", type=Path, help="Optional host-provided abstract AWG JSON")
        child.add_argument("--inputs", type=Path, help="JSON object of supplied media and literal parameters")
        if name == "run":
            child.add_argument("--dry-run", action="store_true")
            child.add_argument("--comfy-url")
            child.add_argument("--comfy-mode", choices=("auto", "local", "remote"))
            child.add_argument("--comfy-policy", choices=("prompt", "auto", "never"))
    evolve = sub.add_parser("evolve", help="Evolve an immutable saved workflow asset")
    evolve.add_argument("asset", type=Path)
    evolve.add_argument("--old-act", required=True, type=Path)
    evolve.add_argument("--new-act", type=Path, help="Defaults to latest ACT")
    evolve.add_argument("--no-llm", action="store_true")
    evolve.add_argument("--agent-assisted", action="store_true", help="Allow an agent for semantic review or full regeneration")
    evolve.add_argument("--output", type=Path)
    args = parser.parse_args()
    cfg = load_config(args.config)
    if getattr(args, 'comfy_url', None):
        cfg.setdefault('comfy', {})['base_url'] = args.comfy_url
    if getattr(args, 'comfy_mode', None):
        cfg.setdefault('comfy', {})['mode'] = args.comfy_mode
    state = resolve(cfg.get("state_root", "./data"))
    registry = resolve(cfg.get("atomic_workflows_root", "./atomic_workflows"))
    agent_cfg = cfg.get("agents") or {"provider": "api", **(cfg.get("openai") or {})}
    try:
        if getattr(args, 'output', None) and args.output.exists():
            raise ValueError(f'Output exists; choose a new path: {args.output}')
        if args.command == 'comfy':
            from agentictree.comfy_runtime import ComfyRuntime
            runtime = ComfyRuntime(cfg)
            result = runtime.status(args.check_updates) if args.action == 'status' else getattr(runtime, args.action)()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'execute':
            from agentictree.compose import compose_workflow
            from agentictree.graph_validate import validate_api_graph
            from agentictree.comfy_runtime import execute_workflow
            saved = read_json(args.asset)
            if 'execution_plan' in saved:
                graph, info = compose_workflow(saved)
                if not info['complete'] or info['format'] != 'api':
                    raise ValueError('Export/convert a complete API workflow before execution: ' + str(info['issues']))
            else:
                graph = saved
            errors = validate_api_graph(graph)
            if errors:
                raise ValueError('; '.join(errors))
            result = {'execution': [{'status': 'skipped', 'node_count': len(graph)}] if args.dry_run else execute_workflow(graph, cfg, args.comfy_policy)}
            if args.output:
                write_json(args.output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command=='export':
            from agentictree.compose import compose_workflow
            graph,info=compose_workflow(read_json(args.asset))
            if not info['complete']:
                raise ValueError('; '.join(info['issues']))
            if args.api and info['format']=='editor':
                from agentictree.editor_to_api import convert_editor_workflow
                graph,issues=convert_editor_workflow(graph)
                if graph is None:
                    raise ValueError('; '.join(issues))
                info['format']='api'
            if args.output.exists():
                raise ValueError(f'Output exists; choose a new path: {args.output}')
            write_json(args.output,graph)
            print(json.dumps({'output':str(args.output.resolve()),'format':info['format'],'complete':True},ensure_ascii=False))
            return 0
        if args.command == "sync":
            result = sync_official(state / "sources", limit=args.limit, offline=args.offline,
                                   convert_editor=args.convert_editor)
            conversions = {}
            for item in result["workflows"]:
                status = (item.get("api_conversion") or {}).get("status", "unavailable")
                conversions[status] = conversions.get(status, 0) + 1
            summary = {k: result[k] for k in ("version", "fetched_at", "fresh", "complete", "template_count", "provenance")}
            summary["api_conversion"] = conversions
            summary["direct_api_examples"] = len(result.get("api_examples", []))
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return int(any(w["status"] == "fetch_failed" for w in result["workflows"]))
        backend = None
        agent_requested = bool(getattr(args, "agent_assisted", False) or getattr(args, "agent_review", False))
        if getattr(args, "no_llm", False) and agent_requested:
            raise ValueError("Choose either --no-llm or an agent-assisted option")
        if agent_requested:
            backend = make_backend(agent_cfg)
        if args.command == "maintain":
            source = sync_official(state / "sources") if args.sync else None
            if source is None and (state / "sources" / "latest.json").exists():
                source = read_json(state / "sources" / "latest.json")
                fetched_at = datetime.fromisoformat(source["fetched_at"])
                source["fresh"] = (datetime.now(timezone.utc) - fetched_at).total_seconds() <= 300
            result = update_act(registry, state / "act", source, backend)
            summary = {k: v for k, v in result.items() if k not in ("records", "changes")}
            if source:
                summary["source_fresh"] = source.get("fresh", False)
                summary["source_version"] = source.get("version")
                summary["source_complete"] = source.get("complete")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print(f"Saved ACT: {state / 'act' / 'versions' / (result['version'] + '.json')}", file=sys.stderr)
            return 0
        latest = state / "act" / "latest.json"
        snapshot = read_json(latest) if latest.exists() else update_act(registry, state / "act")
        if args.command == "catalog":
            print(render_tree_markdown(snapshot))
            return 0
        if args.command == "search":
            print(json.dumps(search_catalog(snapshot, args.query, kind=args.kind, limit=args.limit),
                             ensure_ascii=False, indent=2))
            return 0
        if args.command == "evolve":
            result = evolve_asset(read_json(args.asset), read_json(args.old_act),
                                  read_json(args.new_act) if args.new_act else snapshot,
                                  state / "workflows", backend=backend,
                                  max_iterations=int(agent_cfg.get("max_iterations", 5)))
            code = 0 if result["committed"] else 1
        else:
            result = generate_asset(args.intent, snapshot, state / "workflows", backend=backend,
                                    max_iterations=int(agent_cfg.get("max_iterations", 5)),
                                    att=read_json(args.att) if args.att else None,
                                    awg=read_json(args.awg) if args.awg else None,
                                    inputs=read_json(args.inputs) if args.inputs else None)
            code = 0 if result["status"] == "validated_offline" else 1
            if args.command == "run" and code == 0:
                if result['export_info']['format']=='editor':
                    if not args.dry_run:
                        raise ValueError('Export the complete editor workflow and load it in the ComfyUI frontend, or explicitly convert it for /prompt')
                    result['execution']=[{'dag_id':'composed','status':'skipped','format':'editor'}]
                elif args.dry_run:
                    result['execution']=[{'dag_id':'composed','status':'skipped','format':'api',
                                          'node_count':len(result['comfy_workflow'])}]
                else:
                    from agentictree.comfy_runtime import execute_workflow
                    result['execution'] = execute_workflow(result['comfy_workflow'], cfg, args.comfy_policy)

        if args.output:
            if args.output.exists():
                raise ValueError(f"Output exists; choose a new path: {args.output}")
            write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError, httpx.HTTPError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
