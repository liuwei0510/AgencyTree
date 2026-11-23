import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import urlsplit

project = Path(__file__).resolve().parents[1]
root = Path(tempfile.mkdtemp(prefix="agencytree-dsh-test-"))
settings = dict(comfyUrl="http://127.0.0.1:8188", comfyMode="local",
                manageComfy=False, comfyAuto=False, timeoutSeconds=20)
runs = root / "outputs" / "runs"
runs.mkdir(parents=True)
counter = 0


def run(operation, data, match=None):
    global counter
    counter += 1
    output = runs / f"task-{counter}"
    output.mkdir()
    payload = dict(runId=output.name, output=str(output), operation=operation, input=data, settings=settings)
    res = subprocess.run([sys.executable, "-B", str(project / "python" / "worker.py")],
                         input=json.dumps(payload), capture_output=True, text=True, timeout=150, cwd=output)
    lines = [json.loads(line) for line in res.stdout.splitlines()]
    assert lines[0]["type"] == "ready", lines
    if match:
        assert res.returncode and match in lines[-1]["message"], lines
        print(f"{operation}: expected failure ({match})", flush=True)
        return
    assert res.returncode == 0 and lines[-1]["type"] == "result", (lines, res.stderr[-4000:])
    for artifact in lines[-1]["files"]:
        assert (output / artifact["relativePath"]).is_file(), artifact
    print(operation, lines[-1]["text"], flush=True)
    return output


def ref(path):
    return path.relative_to(root).as_posix()


run("catalog", {"query": "upscale", "limit": 5})
run("benchmark", {"id": "201"})
package_data = project / "python" / "data" / "benchmarks"
rows = json.loads((package_data / "requirements_220.json").read_text())["requirements"]
row = next(row for row in rows if row["id"] == "201")
plans = json.loads((package_data / "smoke_plans.json").read_text())["plans"]
generated = run("plan", {"intent": row["instruction"], "att": plans["201"]})
asset = ref(generated / "final" / "asset.json")
run("export", {"asset_path": asset, "api": True})
run("execute", {"asset_path": asset, "dry_run": True})
run("maintain", {})
evolved = run("evolve", {"asset_path": asset})
run("run", {"intent": "Create a blue square image", "dry_run": True})
run("comfy", {"action": "status"})
run("comfy", {"action": "install"}, match="启用本地")
run("export", {"asset_path": "../secret.json"}, match="工作区")

received = []


class Server(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def reply(self, body):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())

    def authorized(self):
        assert self.headers["Authorization"] == "Bearer test-token"

    def do_GET(self):
        self.authorized()
        path = urlsplit(self.path).path
        replies = {
            "/proxy/system_stats": {"system": {}},
            "/proxy/object_info": {"LoadImage": {"input": {"required": {"image": [["workspace/uploaded.png"]]}}, "output": ["IMAGE", "MASK"]},
                                   "SaveImage": {"input": {"required": {"images": ["IMAGE"]}}, "output": []}},
            "/proxy/history": {"task": {"outputs": {"2": {"images": [{"filename": "render.png", "type": "output"}]}}}},
            "/proxy/view": b"fake image",
        }
        self.reply(replies[path])

    def do_POST(self):
        self.authorized()
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/proxy/upload/image":
            assert b"fixture image" in body
            self.reply({"name": "uploaded.png", "subfolder": "workspace"})
        else:
            assert self.path == "/proxy/prompt"
            received.append(json.loads(body)["prompt"])
            self.reply({"prompt_id": "task"})


server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    settings.update(comfyUrl=f"http://127.0.0.1:{server.server_port}/proxy", comfyMode="remote",
                    comfyAuthorization="Bearer test-token")
    (root / "sample.png").write_bytes(b"fixture image")
    result = run("execute", {"asset": {
        "1": {"class_type": "LoadImage", "inputs": {"image": "file:sample.png"}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}}
    }})
    assert received[0]["1"]["inputs"]["image"] == "workspace/uploaded.png"
    assert (result / "final" / "01-render.png").read_bytes() == b"fake image"
finally:
    server.shutdown()
    server.server_close()
print(f"Smoke complete: {root}")
