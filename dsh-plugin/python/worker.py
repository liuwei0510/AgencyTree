"""AgencyTree worker for a single DSH Run."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import signal
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parent
WIRE = sys.stdout
LOADERS = {"LoadImage": "image", "LoadImageMask": "image", "LoadAudio": "audio",
           "LoadVideo": "video", "VHS_LoadVideo": "video"}


class Job:
    def __init__(self, message):
        self.run_id = message["runId"]
        self.operation = message["operation"]
        self.input = message["input"]
        self.output = Path(message["output"]).resolve(strict=True)
        if self.output.parent.name != "runs" or self.output.parent.parent.name != "outputs":
            raise ValueError("需要 DSH Run 提供 workspace/outputs/runs 下的任务目录")
        self.workspace = self.output.parents[2]
        self.state = self.workspace / "outputs" / "agencytree"
        self.final = self.output / "final"
        for directory in (self.state, self.final):
            directory.mkdir(exist_ok=True)
            if directory.resolve() != directory:
                raise ValueError("任务或状态目录不能指向其他位置")
        self.settings = message["settings"]
        self.model = message.get("model")
        self.files = []

    def send(self, kind, **data):
        print(json.dumps({"protocol": 1, "runId": self.run_id, "type": kind, **data}, ensure_ascii=False),
              file=WIRE, flush=True)

    def stage(self, stage, label):
        self.send("stage", stage=stage, label=label)

    def artifact(self, name, value, mime="application/json"):
        path = self.final / name
        with path.open("x", encoding="utf-8") as stream:
            if mime == "application/json":
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            else:
                stream.write(value)
        self.add(path, mime)

    def add(self, path, mime):
        self.files.append({"relativePath": path.relative_to(self.output).as_posix(),
                           "name": path.name, "mime": mime})

    def workspace_file(self, value):
        path = self.workspace / value
        if Path(value).is_absolute() or PureWindowsPath(value).drive or ".." in re.split(r"[/\\]", value):
            raise ValueError("文件须使用当前 DSH 工作区相对路径")
        path = path.resolve(strict=True)
        if not path.is_relative_to(self.workspace) or not path.is_file():
            raise ValueError("文件须位于当前 DSH 工作区内")
        return path

    def asset(self):
        from agentictree.storage import read_json
        embedded, path = self.input.get("asset"), self.input.get("asset_path")
        if (embedded is None) == (path is None):
            raise ValueError("请提供 asset 或 asset_path，两者择一")
        return embedded if embedded is not None else read_json(self.workspace_file(path))

    def act(self, version=None, bundled=False):
        from agentictree.storage import read_json
        act = self.state / "act"
        if version and not re.fullmatch(r"act-[a-f0-9]{20}", version):
            raise ValueError("ACT 版本格式无效")
        root = ROOT / "data" / "act"
        if bundled:
            path = root / "latest.json"
        elif version:
            path = act / "versions" / (version + ".json")
            if not path.is_file():
                path = root / "latest.json"
        else:
            path = act / "latest.json"
            if not path.is_file():
                path = root / "latest.json"
        snapshot = read_json(path)
        if version and snapshot["version"] != version:
            raise ValueError(f"找不到 ACT 版本 {version}")
        for record in snapshot["records"].values():
            if record.get("graph_object"):
                digest = record.get("workflow_sha256") or Path(record["graph_object"]).stem
                if not re.fullmatch(r"[a-f0-9]{64}", digest):
                    raise ValueError("ACT 图对象摘要无效")
                name = digest + ".json"
                graph = act / "objects" / name
                if not graph.is_file():
                    graph = root / "objects" / name
                record["graph_object"] = str(graph)
        return snapshot

    @contextlib.contextmanager
    def maintenance(self):
        with contextlib.closing(sqlite3.connect(self.state / "maintenance.sqlite", timeout=120)) as lock:
            lock.execute("BEGIN EXCLUSIVE")
            yield
            lock.commit()

    def backend(self):
        if not self.model:
            return None
        from agentictree.backends import APIBackend
        os.environ["AGENCYTREE_DSH_GATEWAY_TOKEN"] = self.model["apiKey"]
        try:
            return APIBackend({"api_key_env": "AGENCYTREE_DSH_GATEWAY_TOKEN",
                               "base_url": self.model["baseUrl"], "model": self.model["model"],
                               "timeout_sec": min(240, self.settings["timeoutSeconds"]), "trust_env": False})
        finally:
            os.environ.pop("AGENCYTREE_DSH_GATEWAY_TOKEN", None)

    def config(self):
        from agentictree.comfy_connection import connection_mode
        url = self.settings["comfyUrl"]
        mode = connection_mode(url, self.settings["comfyMode"])
        options = {"base_url": url, "mode": mode, "timeout_sec": self.settings["timeoutSeconds"]}
        if self.settings.get("comfyAuthorization"):
            os.environ["AGENCYTREE_DSH_COMFY_TOKEN"] = self.settings["comfyAuthorization"].removeprefix("Bearer ")
            options["api_key_env"] = "AGENCYTREE_DSH_COMFY_TOKEN"
        runtime = {"policy": "prompt", "dsh_managed_process": True}
        if self.settings["manageComfy"] and self.settings["comfyAuto"] and mode == "local":
            runtime["policy"] = "auto"
        if self.settings.get("comfyAutoUpdate"):
            runtime["auto_update"] = True
        if self.settings.get("comfyRoot"):
            runtime["install_dir"] = self.settings["comfyRoot"]
        if self.settings.get("comfyPython"):
            runtime["runtime_python"] = self.settings["comfyPython"]
        if self.settings.get("comfyInstallPython"):
            runtime["python_executable"] = self.settings["comfyInstallPython"]
        if self.settings.get("comfyCpu"):
            runtime["launch_args"] = ["--cpu"]
        return {"comfy": options, "comfy_runtime": runtime}

    def export(self, asset, api=False):
        from agentictree.compose import compose_workflow
        from agentictree.editor_to_api import convert_editor_workflow
        graph, info = compose_workflow(asset)
        if not info["complete"]:
            raise ValueError("工作流导出失败：" + "; ".join(info["issues"]))
        kind = info["format"]
        if api and kind == "editor":
            graph, errors = convert_editor_workflow(graph)
            if graph is None:
                raise ValueError("编辑器工作流无法转换：" + "; ".join(errors))
            kind = "api"
        self.artifact("workflow-" + kind + ".json", graph)
        return graph

    def execute(self, graph):
        import copy
        import mimetypes
        import httpx
        from agentictree.comfy_connection import connection_options
        from agentictree.comfy_runtime import execute_workflow
        from agentictree.graph_validate import validate_api_graph
        from agentictree.comfy_client import ComfyClient
        errors = validate_api_graph(graph)
        if errors:
            raise ValueError("; ".join(errors))
        graph = copy.deepcopy(graph)
        config = self.config()
        # Only an explicit workspace-relative "file:" input uploads a local file.
        # Ordinary names refer to files already present on the ComfyUI server.
        uploads = []
        for node in graph.values():
            field = LOADERS.get(node.get("class_type"))
            if not field:
                continue
            value = node.get("inputs", {}).get(field)
            if not isinstance(value, str):
                continue
            if value.startswith("file:"):
                uploads.append((node, field, self.workspace_file(value[5:])))
            elif (Path(value).is_absolute() or PureWindowsPath(value).drive
                  or ".." in re.split(r"[/\\]", value) or (self.output / value).is_file()):
                raise ValueError("请用 file:工作区相对路径上传本地文件，或使用 ComfyUI 服务端输入文件名")
        self.stage("execution", "提交并等待 ComfyUI 执行")
        url = config["comfy"]["base_url"].rstrip("/")
        if uploads:
            from agentictree.comfy_runtime import ComfyRuntime
            ComfyRuntime(config).ensure()
            with ComfyClient(url, **connection_options(config)) as client:
                for node, field, path in uploads:
                    node["inputs"][field] = client.upload_image(path, overwrite=False)
        result = execute_workflow(graph, config)
        self.artifact("execution.json", result)
        self.stage("download", "从 ComfyUI 下载产物")
        with httpx.Client(timeout=120, **connection_options(config)) as client:
            for entry in result[0]["outputs"].values():
                for item in entry:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    name = re.split(r"[/\\]", item["filename"])[-1]
                    name = re.sub(r"[^\w.()-]", "_", name).strip(".") or "output"
                    path = self.final / f"{len(self.files):02d}-{name}"
                    with client.stream("GET", url + "/view", params=item) as response:
                        response.raise_for_status()
                        with path.open("xb") as stream:
                            for chunk in response.iter_bytes():
                                stream.write(chunk)
                    self.add(path, mimetypes.guess_type(name)[0] or "application/octet-stream")
        return "ComfyUI 执行完成"

    def work(self):
        from agentictree.storage import read_json, write_json
        from agentictree.tree_view import render_tree_markdown
        from agentictree.assets import generate_asset
        from agentictree.evolution import evolve_asset
        from agentictree.knowledge import update_act
        from agentictree.sources import sync_official
        from agentictree.catalog_tools import search_catalog
        from agentictree.comfy_runtime import ComfyRuntime

        data, op = self.input, self.operation
        self.stage(op, {"catalog": "查询能力树", "sync": "同步官方目录", "maintain": "维护能力树",
                        "plan": "生成工作流", "run": "生成工作流", "evolve": "演化工作流",
                        "export": "导出工作流", "execute": "加载工作流", "comfy": "检查 ComfyUI",
                        "benchmark": "查询实验需求"}[op])
        if op == "catalog":
            act = self.act()
            if data.get("query"):
                hits = search_catalog(act, data["query"], kind=data.get("kind", "all"), limit=data.get("limit", 12))
                self.artifact("search-results.json", hits)
                return f"检索到 {len(hits)} 条记录"
            self.artifact("capability-tree.md", render_tree_markdown(act), "text/markdown")
            return f"能力树版本 {act['version']}"
        if op in {"sync", "maintain"}:
            with self.maintenance():
                sources_dir = self.state / "sources"
                if op == "sync" or data.get("sync"):
                    source = sync_official(sources_dir, offline=data.get("offline", False),
                                           convert_editor=data.get("convert_editor", False))
                else:
                    cached = sources_dir / "latest.json"
                    source = read_json(cached) if cached.is_file() else None
                    if source:
                        source["fresh"] = False
                if op == "sync":
                    self.artifact("source-sync.json", source)
                    if not source["complete"]:
                        raise RuntimeError("官方目录同步不完整，请查看网络后重试")
                    return f"已同步官方目录 {source['version']}"
                tree = self.state / "atomic_workflows"
                if not tree.exists():
                    staged = self.state / ("tree-" + uuid.uuid4().hex)
                    try:
                        shutil.copytree(ROOT / "atomic_workflows", staged)
                        staged.rename(tree)
                    finally:
                        if staged.exists():
                            shutil.rmtree(staged)
                act_dir = self.state / "act"
                if not (act_dir / "latest.json").exists():
                    baseline = self.act(bundled=True)
                    write_json(act_dir / "versions" / (baseline["version"] + ".json"), baseline)
                    write_json(act_dir / "latest.json", baseline)
                snapshot = update_act(tree, act_dir, source, self.backend())
            self.artifact("act.json", snapshot)
            self.artifact("act-changes.json", snapshot["changes"])
            self.artifact("capability-tree.md", render_tree_markdown(snapshot), "text/markdown")
            return f"能力树 {snapshot['version']}，记录 {len(snapshot['records'])} 条"
        if op in {"plan", "run"}:
            snapshot = self.act()
            asset = generate_asset(data["intent"], snapshot, self.output / "intermediate",
                                   backend=self.backend(), att=data.get("att"), awg=data.get("awg"), inputs=data.get("inputs"))
            self.artifact("asset.json", asset)
            if asset["status"] != "validated_offline":
                raise ValueError(asset["generation"].get("failure_reason") or "工作流规划失败")
            graph = self.export(asset, api=op == "run" and not data.get("dry_run"))
            if op == "run" and not data.get("dry_run"):
                return self.execute(graph)
            return f"已生成工作流 {asset['version']}"
        if op == "export":
            self.export(self.asset(), api=data.get("api", False))
            return "已导出 ComfyUI 工作流"
        if op == "evolve":
            asset = self.asset()
            old = self.act(data.get("old_act") or asset["act_version"])
            new = self.act(data.get("new_act"))
            result = evolve_asset(asset, old, new, self.output / "intermediate", backend=self.backend())
            self.artifact("evolution.json", result)
            if not result["committed"]:
                raise ValueError("演化未提交：" + str(result.get("diagnostics")))
            candidate = result["candidate"]
            self.artifact("asset.json", candidate)
            self.export(candidate)
            return f"已完成工作流演化 {candidate['version']}"
        if op == "execute":
            saved = self.asset()
            graph = self.export(saved, api=not data.get("dry_run")) if "execution_plan" in saved else saved
            if data.get("dry_run"):
                from agentictree.graph_validate import validate_api_graph
                from agentictree.catalog_contracts import validate_editor_graph
                errors = validate_editor_graph(graph) if "nodes" in graph else validate_api_graph(graph)
                if errors:
                    raise ValueError("; ".join(errors))
                self.artifact("execution.json", {"status": "skipped", "nodes": len(graph.get("nodes", graph))})
                return "工作流离线结构检查通过"
            return self.execute(graph)
        if op == "comfy":
            runtime = ComfyRuntime(self.config())
            action = data["action"]
            if action in {"install", "update", "start"}:
                if not self.settings["manageComfy"]:
                    raise ValueError("需先由部署者在插件配置里启用本地 ComfyUI 管理")
                result = getattr(runtime, action)()
            else:
                result = runtime.status(check_updates=action == "check_updates")
            self.artifact("comfy-status.json", result)
            if action == "start" and result.get("pid"):
                return "ComfyUI 启动检查通过。本次任务结束时 DSH 会清理自己启动的进程"
            return str(result.get("next_action", "ComfyUI 操作完成"))
        rows = read_json(ROOT / "data" / "benchmarks" / "requirements_220.json")["requirements"]
        if data.get("id"):
            rows = [row for row in rows if str(row["id"]) == data["id"].zfill(3)]
        if data.get("query"):
            rows = [row for row in rows if data["query"].casefold() in row["instruction"].casefold()]
        self.artifact("requirements.json", rows)
        return f"找到 {len(rows)} 条实验需求"


def main():
    job = None
    request = {}
    try:
        request = json.loads(sys.stdin.readline())
        job = Job(request)
        probe = job.output / (".agencytree-ready-" + uuid.uuid4().hex)
        with probe.open("x"):
            pass
        probe.unlink()
        job.send("ready")
        with contextlib.redirect_stdout(sys.stderr):
            result = job.work()
        job.send("result", text=result, files=job.files)
        return 0
    except Exception as error:
        text = str(error)
        httpx = sys.modules.get("httpx")
        if httpx is not None and isinstance(error, httpx.HTTPError):
            status = getattr(getattr(error, "response", None), "status_code", None)
            text = f"ComfyUI 或目录服务请求失败 ({status or type(error).__name__})"
        secrets = [request.get("settings", {}).get("comfyAuthorization"),
                   (request.get("model") or {}).get("apiKey")]
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]").replace(secret.removeprefix("Bearer "), "[REDACTED]")
        print(json.dumps({"protocol": 1, "runId": request.get("runId"), "type": "error",
                          "message": text[:3500]}, ensure_ascii=False), file=WIRE, flush=True)
        return 1


def terminate(signum, frame):
    # Let atexit stop any task-scoped ComfyUI child when DSH cancels this Run.
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    raise SystemExit(main())
