from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml

from agentictree.assets import generate_asset
from agentictree.backends import APIBackend, CommandBackend, json_object, make_backend
from agentictree.catalog_tools import search_catalog
from agentictree.comfy_models import usable_model_catalog
from agentictree.comfy_taxonomy import route_template
from agentictree.evolution import evolve_asset
from agentictree.editor_to_api import convert_editor_workflow
from agentictree.graph_validate import validate_api_graph
from agentictree.knowledge import active_capabilities, diff_records, update_act
from agentictree.plan_validate import structural_validate
from agentictree.registry import scan_registry
from agentictree.schemas import PlanNode, WorkflowPlan
from agentictree.storage import digest, read_json, save_version
from agentictree.sources import parse_api_example_script
from agentictree.tree_view import render_tree_markdown

T2I = "core.generative.text_to_image.flux_t2i"


def fixture(root: Path, identifier=T2I, *, output_type="image_filenames", input_type=None):
    parts = identifier.split(".")
    folder = root.joinpath(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    graph = {"1": {"class_type": "EmptyImage", "inputs": {"width": 512, "height": 512, "batch_size": 1, "color": 0}},
             "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "fixture"}}}
    inputs = [{"name": "prompt", "type": "string", "required": True}]
    inject = {"prompt": {"node_id": "2", "input_key": "filename_prefix"}}
    if input_type:
        graph["1"] = {"class_type": "LoadImage", "inputs": {"image": "input.png"}}
        inputs = [{"name": "source_image", "type": input_type, "required": True}]
        inject = {"source_image": {"node_id": "1", "input_key": "image"}}
    manifest = {"id": identifier, "title": identifier, "workflow_file": "workflow.json", "inputs": inputs,
                "outputs": [{"name": "images", "type": output_type}], "inject": inject}
    (folder / "workflow.json").write_text(json.dumps(graph))
    (folder / "cap.manifest.yaml").write_text(yaml.safe_dump(manifest))
    return folder


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.registry = self.root / "atomic_workflows"
        self.folder = fixture(self.registry)
        self.store = self.root / "act"
        self.assets = self.root / "assets"

    def act(self):
        return update_act(self.registry, self.store)

    def test_snapshot_is_idempotent_and_immutable(self):
        a = self.act()
        self.assertEqual(a, self.act())
        self.assertEqual(a["tree"]["core"]["generative"]["text_to_image"], [T2I])
        with self.assertRaises(ValueError):
            save_version(self.store / "versions", {**a, "parent": "tampered"})

    def test_graph_upgrade_and_tombstones(self):
        a = self.act()
        graph = read_json(self.folder / "workflow.json")
        graph["1"]["inputs"]["width"] = 768
        (self.folder / "workflow.json").write_text(json.dumps(graph))
        b = self.act()
        self.assertEqual(b["changes"][0]["type"], "upgraded")
        self.assertNotEqual(a["version"], b["version"])
        (self.folder / "cap.manifest.yaml").unlink()
        c = self.act()
        self.assertEqual(c["records"][T2I]["status"], "deprecated")
        self.assertEqual(c["changes"][0]["type"], "deprecated")
        self.assertEqual(c, self.act())
        self.assertEqual(active_capabilities(c, self.root / "cache"), {})

    def test_editor_and_broken_inject_quarantined(self):
        (self.folder / "workflow.json").write_text('{"nodes":[]}')
        a = self.act()
        self.assertEqual(a["records"][T2I]["status"], "quarantined")
        self.assertFalse(a["tree"])

    def test_duplicate_id_is_rejected(self):
        path = self.folder / "cap.manifest.yaml"
        (self.folder / "duplicate.manifest.yaml").write_bytes(path.read_bytes())
        with self.assertRaises(ValueError):
            scan_registry(self.registry)

    def test_generation_and_immutable_graph_reconstruction(self):
        a = self.act()
        (self.folder / "workflow.json").unlink()
        asset = generate_asset("a blue square", a, self.assets)
        self.assertEqual(asset["status"], "validated_offline")
        self.assertEqual(asset["ewc"]["n1"]["2"]["inputs"]["filename_prefix"], "a blue square")
        self.assertEqual(len(asset["generation"]["validation_log"]), 3)
        self.assertFalse(asset["runtime_verified"])
        self.assertEqual(asset["generation_mode"], "tools_first")

    def test_tools_first_does_not_call_agent_for_a_valid_tool_plan(self):
        class UnusedAgent:
            def complete_json(self, system, user):
                raise AssertionError("agent should be fallback-only")
        asset = generate_asset("blue square", self.act(), self.assets, backend=UnusedAgent())
        self.assertEqual(asset["status"], "validated_offline")
        self.assertEqual(asset["generation_mode"], "tools_first")

    def test_editor_workflow_converts_only_when_widget_mapping_is_known(self):
        editor = {"nodes": [
            {"id": 1, "type": "PromptNode", "inputs": [
                {"name": "prompt", "widget": {"name": "prompt"}, "link": None}],
             "outputs": [{"name": "STRING", "links": [10]}], "widgets_values": ["blue"]},
            {"id": 2, "type": "SaveText", "inputs": [{"name": "text", "link": 10}],
             "outputs": [], "widgets_values": []},
        ], "links": [[10, 1, 0, 2, 0, "STRING"]]}
        api, issues = convert_editor_workflow(editor)
        self.assertFalse(issues)
        self.assertEqual(api["1"]["inputs"]["prompt"], "blue")
        self.assertEqual(api["2"]["inputs"]["text"], ["1", 0])
        ambiguous = {"nodes": [{"id": 1, "type": "Legacy", "inputs": [], "widgets_values": ["blue"]}], "links": []}
        self.assertIsNone(convert_editor_workflow(ambiguous)[0])
        api, issues = convert_editor_workflow(ambiguous, {"Legacy": {"input": {"required": {"prompt": ["STRING"]}}}})
        self.assertFalse(issues)
        self.assertEqual(api["1"]["inputs"]["prompt"], "blue")

    def test_direct_api_example_script_extraction(self):
        source = 'prompt_text = """{"1":{"class_type":"EmptyLatentImage","inputs":{"width":512}}}"""'
        graph = parse_api_example_script(source)
        self.assertEqual(graph["1"]["class_type"], "EmptyLatentImage")

    def test_generation_failure_cannot_report_success(self):
        (self.folder / "workflow.json").write_text('{"nodes":[]}')
        asset = generate_asset("draw a cat", self.act(), self.assets)
        self.assertEqual(asset["status"], "failed")

    def test_reuse(self):
        a = self.act()
        asset = generate_asset("blue square", a, self.assets)
        report = evolve_asset(asset, a, a, self.assets)
        self.assertTrue(report["committed"])
        self.assertEqual(report["classification"], "reuse")
        self.assertEqual(report["candidate"]["ewc"], asset["ewc"])

    def test_partial_rebinding_preserves_original(self):
        a = self.act()
        asset = generate_asset("blue square", a, self.assets)
        original = copy.deepcopy(asset)
        graph = read_json(self.folder / "workflow.json")
        graph["1"]["inputs"]["width"] = 768
        (self.folder / "workflow.json").write_text(json.dumps(graph))
        b = self.act()
        report = evolve_asset(asset, a, b, self.assets)
        self.assertTrue(report["committed"], report["diagnostics"])
        self.assertEqual(report["classification"], "partial")
        self.assertEqual(report["candidate"]["ewc"]["n1"]["1"]["inputs"]["width"], 768)
        self.assertEqual(asset, original)

    def test_deprecated_implementation_uses_equivalent(self):
        a = self.act()
        asset = generate_asset("blue square", a, self.assets)
        (self.folder / "cap.manifest.yaml").unlink()
        fixture(self.registry, "core.generative.text_to_image.replacement")
        report = evolve_asset(asset, a, self.act(), self.assets)
        self.assertTrue(report["committed"], report["diagnostics"])
        self.assertTrue(report["candidate"]["execution_plan"]["nodes"][0]["atomic_id"].endswith("replacement"))

    def test_total_failure_rolls_back(self):
        a = self.act()
        asset = generate_asset("blue square", a, self.assets)
        (self.folder / "cap.manifest.yaml").unlink()
        report = evolve_asset(asset, a, self.act(), self.assets)
        self.assertEqual(report["classification"], "total")
        self.assertFalse(report["committed"])
        self.assertEqual(report["rollback_target"], asset["version"])
        self.assertIsNone(report["candidate"])

    def test_total_regeneration_replans_with_harness(self):
        a = self.act()
        asset = generate_asset("blue square", a, self.assets)
        (self.folder / "cap.manifest.yaml").unlink()
        fixture(self.registry, "core.generative.vector_generation.replacement")
        b = self.act()
        class Agent:
            def complete_json(self, system, user):
                if "system analyst" in system.lower():
                    return json.dumps({"tasks": [{"task_id":"t1", "goal":"blue square",
                        "capability_id":"core.generative.vector_generation", "outputs":{"images":"image"}}]})
                return json.dumps({"ok": True, "issues": []})
        report = evolve_asset(asset, a, b, self.assets, backend=Agent())
        self.assertTrue(report["committed"], report["diagnostics"])
        self.assertEqual(report["classification"], "total")
        self.assertEqual(report["candidate"]["awg"]["nodes"][0]["capability_id"], "core.generative.vector_generation")
        self.assertEqual(report["abstract_change_plan"]["rebuild"], ["ATT","AWG","EWC"])

    def test_missing_new_parameter_rolls_back(self):
        a = self.act()
        asset = generate_asset("blue square", a, self.assets)
        manifest = yaml.safe_load((self.folder / "cap.manifest.yaml").read_text())
        manifest["inputs"].append({"name": "new_required", "type": "int", "required": True})
        manifest["inject"]["new_required"] = {"node_id": "1", "input_key": "width"}
        (self.folder / "cap.manifest.yaml").write_text(yaml.safe_dump(manifest))
        b = self.act()
        self.assertEqual(b["changes"][0]["type"], "interface_changed")
        report = evolve_asset(asset, a, b, self.assets)
        self.assertFalse(report["committed"])
        self.assertIn("new_required", str(report["diagnostics"]))

    def test_adapter_insertion_and_unchanged_subgraph(self):
        post = "aux.postprocess.denoise.image_denoise"
        fixture(self.registry, post, input_type="image_filenames")
        a = self.act()
        asset = generate_asset("edit and refine", a, self.assets)
        # Construct a two-node valid historical chain from explicit fixture contracts.
        plan = WorkflowPlan(nodes=[PlanNode(dag_id="a", atomic_id=T2I),
            PlanNode(dag_id="b", atomic_id=post, depends_on=["a"],
                     bindings={"source_image": {"from_node": "a", "output": "images"}})], global_inputs={"prompt": "blue"})
        from agentictree.ewc_build import materialize_ewc
        asset = {**asset, "status": "validated_offline", "awg": plan.model_dump(),
                 "ewc": materialize_ewc(plan, active_capabilities(a, self.root / "cache"))[0]}
        manifest = yaml.safe_load((self.folder / "cap.manifest.yaml").read_text())
        manifest["outputs"][0]["type"] = "image_path"
        (self.folder / "cap.manifest.yaml").write_text(yaml.safe_dump(manifest))
        fixture(self.registry, "aux.preprocess.data_conversion.image_path_to_filenames", input_type="image_path")
        report = evolve_asset(asset, a, self.act(), self.assets)
        self.assertTrue(report["committed"], report["diagnostics"])
        self.assertTrue(any(x["action"] == "insert_adapter" for x in report["actions"]))

    def test_partial_source_does_not_deprecate_missing_remote(self):
        source = {"version": "source-a", "fresh": True, "complete": True,
                  "provenance": {"models": {}, "templates": {}},
                  "models": [{"name": "One", "filename": "one.safetensors"}], "workflows": []}
        a = update_act(self.registry, self.store, source)
        model = next(v for k, v in a["records"].items() if k.startswith("remote:model:"))
        self.assertEqual(model["status"], "unusable_format")
        self.assertFalse(model["code_usable"])
        b = update_act(self.registry, self.store, {**source, "models": [], "complete": False})
        self.assertEqual(a, b)

    def test_comfyui_formatted_models_are_code_usable_without_runtime_checks(self):
        model = {"name": "Example", "type": "checkpoint", "save_path": "checkpoints",
                 "filename": "example.safetensors", "url": "https://example.com/example.safetensors",
                 "reference": "https://example.com", "base": "SDXL", "description": "Example",
                 "size": "1GB"}
        huggingface_model = {"name": "org/repo", "type": "checkpoint", "save_path": "repo",
                             "filename": "<huggingface>", "url": "org/repo",
                             "reference": "https://huggingface.co/org/repo", "base": "SDXL"}
        source = {"version": "source-review", "fresh": True, "complete": True,
                  "provenance": {"models": {"revision": "model-revision"}, "templates": {}},
                  "models": [{**model, "size": "628MB"}, {**model, "size": "1.26GB"},
                             huggingface_model], "workflows": []}
        snapshot = update_act(self.registry, self.store, source)
        records = usable_model_catalog(snapshot)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record["status"] == "available" for record in records.values()))
        self.assertTrue(all(record["code_usable"] for record in records.values()))
        self.assertTrue(all(not record["validation_errors"] for record in records.values()))
        self.assertTrue(all("six_tuple" in record for record in records.values()))
        model_ids = snapshot["tree"]["core"]["generative"]["text_to_image"]
        self.assertTrue(set(records).issubset(set(model_ids)))
        self.assertIn(T2I, model_ids)
        tree_markdown = render_tree_markdown(snapshot)
        self.assertIn("## core", tree_markdown)
        self.assertIn("Example", tree_markdown)

    def test_comfyui_tree_materialization_follows_four_thesis_levels(self):
        model = {"name": "Example", "type": "checkpoint", "save_path": "checkpoints",
                 "filename": "example.safetensors", "url": "https://example.com/example.safetensors",
                 "reference": "https://example.com", "base": "SDXL"}
        template_graph = {"nodes": [{"id": 1, "type": "EmptyImage", "inputs": [], "widgets_values": [512, 512, 1, 0]}], "links": []}
        source_graph = self.root / "editor.json"
        from agentictree.storage import write_json
        write_json(source_graph, template_graph)
        from agentictree.storage import digest
        template = {"name": "example_t2i", "path": str(source_graph), "sha256": digest(template_graph),
                    "url": "https://example.com/example.json", "format": "editor", "status": "pending_review",
                    "metadata": {"title": "Example T2I", "description": "Text to image", "io": {"outputs": [{"mediaType": "image"}]}}}
        source = {"version": "tree-source", "fresh": True, "complete": True,
                  "provenance": {"models": {}, "templates": {"revision": "templates-rev"}},
                  "models": [model], "workflows": [template], "api_examples": []}
        snapshot = update_act(self.registry, self.store, source)
        self.assertEqual(set(snapshot["tree"]["core"]["generative"]["text_to_image"]), {
            T2I,
            "remote:model:" + digest(["checkpoint", "checkpoints", "example.safetensors", "Example"])[:20],
            "remote:template:example_t2i",
        })
        template_record = snapshot["records"]["remote:template:example_t2i"]
        self.assertEqual(template_record["kind"], "comfyui_template")
        self.assertEqual(template_record["status"], "available")
        self.assertTrue(template_record["code_usable"])
        branch = self.registry / "core" / "generative" / "text_to_image"
        self.assertTrue(any(branch.glob("model_*/model.yaml")))
        self.assertTrue(any(branch.glob("comfyui_*/template.yaml")))
        self.assertTrue(any(branch.glob("comfyui_*/workflow.editor.json")))
        self.assertTrue(snapshot["validation"]["four_level_tree"])

    def test_catalog_search_returns_short_paths_without_graph_payloads(self):
        snapshot = self.act()
        result = search_catalog(snapshot, "text to image", kind="workflows")
        self.assertEqual(result[0]["id"], T2I)
        self.assertEqual(result[0]["atomic_workflow_path"], None)
        self.assertNotIn("graph", result[0])

    def test_official_examples_map_to_thesis_abstract_capabilities(self):
        def template(name, title, description, inputs=None, outputs=None):
            return {"name": name, "metadata": {"title": title, "description": description,
                    "io": {"inputs": inputs or [], "outputs": outputs or []}}}
        self.assertEqual(route_template(template("api_qwen_edit", "Qwen Image Edit", "Edit an image with instructions")),
                         ("core", "editing", "image_editing"))
        self.assertEqual(route_template(template("api_kling_t2v", "Kling: Text to Video", "Generate video from text")),
                         ("core", "generative", "text_to_video"))
        self.assertEqual(route_template(template("audio_t2m", "Text to Music", "Generate audio")),
                         ("core", "generative", "text_to_audio"))
        self.assertEqual(route_template(template("mesh_t2m", "Text to Model", "Generate a 3D model")),
                         ("core", "generative", "three_d"))

    def test_provenance_and_popularity_are_not_semantic_changes(self):
        old = {"x": {"status": "pending_review", "metadata": {"name": "a", "usage": 1}, "evidence": {"revision": "a"}}}
        new = {"x": {"status": "pending_review", "metadata": {"name": "a", "usage": 99}, "evidence": {"revision": "b"}}}
        self.assertEqual(diff_records(old, new), [])

    def test_bad_graphs(self):
        self.assertTrue(validate_api_graph({"1": {"class_type": "SaveImage", "inputs": {"images": ["missing", 0]}}}))
        self.assertTrue(validate_api_graph({"1": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}}}))
        self.assertTrue(structural_validate(WorkflowPlan(), {}))

    def test_command_harness_contract_and_failure(self):
        bridge = self.root / "bridge.py"
        bridge.write_text('import json,sys; r=json.load(sys.stdin); print(json.dumps({"received":r["system"]}))')
        adapter = CommandBackend({"command": [sys.executable, str(bridge)]})
        self.assertEqual(json.loads(adapter.complete_json("role", "data")), {"received": "role"})
        snapshot = self.act()
        roles = []
        class HostedAgent:
            def complete_json(self, system, user):
                roles.append(system)
                if "system analyst" in system.lower():
                    return json.dumps({"tasks":[{"task_id":"t1", "goal":"Create a blue square image then save it",
                        "capability_id":"core.generative.text_to_image", "outputs":{"images":"image"}}]})
                return json.dumps({"ok":True,"issues":[]})
        hosted = generate_asset("Create a blue square image then save it", snapshot, self.assets, backend=HostedAgent())
        self.assertEqual(hosted["status"], "validated_offline", hosted['generation'])
        self.assertEqual(hosted["generation_mode"], "agent_fallback")
        bridge.write_text('''import json,sys
r=json.load(sys.stdin)
if "system analyst" in r["system"].lower():
 out={"tasks":[{"task_id":"t1","goal":"Create a blue square image then save it","capability_id":"core.generative.text_to_image","outputs":{"images":"image"}}]}
else: out={"ok":True,"issues":[]}
print(json.dumps(out))
''')
        command_backend = make_backend({"provider":"command","command":[sys.executable,str(bridge)]})
        command_asset = generate_asset("Create a blue square image then save it", snapshot, self.assets, backend=command_backend)
        self.assertEqual(command_asset["status"], "validated_offline", command_asset['generation'])
        with self.assertRaises(ValueError):
            CommandBackend({"command": "echo unsafe"}).complete_json("", "")
        with self.assertRaises(RuntimeError):
            CommandBackend({"command": [sys.executable, "-c", "raise SystemExit(2)"]}).complete_json("", "")

    def test_api_protocol_with_local_http_server(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                payload = json.dumps({"id": "fixture", "object": "chat.completion", "created": 0, "model": "fixture",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"ok":true}'}, "finish_reason": "stop"}]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with patch.dict(os.environ, {"FIXTURE_KEY": "local-fixture-only"}):
                adapter = APIBackend({"model": "fixture", "api_key_env": "FIXTURE_KEY", "base_url": f"http://127.0.0.1:{server.server_port}/v1", "trust_env": False})
                self.assertEqual(json.loads(adapter.complete_json("role", "data")), {"ok": True})
            self.assertEqual(requests[0]["response_format"], {"type": "json_object"})
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_no_silent_heuristic_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                make_backend({"provider": "api", "model": "fixture"})
        with self.assertRaises(ValueError):
            json_object('[]')


if __name__ == "__main__":
    unittest.main()
