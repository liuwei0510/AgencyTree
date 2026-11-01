#!/usr/bin/env python3
"""One-off: copy model_apis/*.json into atomic_workflows with manifests."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "model_apis"
DST_ROOT = ROOT / "atomic_workflows"


def slugify(stem: str) -> str:
    s = stem.strip().lower().replace(" ", "_")
    s = re.sub(r"[^\w.\-]+", "_", s, flags=re.ASCII)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "workflow"


def route(stem: str) -> tuple[str, str, str]:
    """Return (area, domain, task) — three segments under atomic_workflows."""
    n = stem.lower()
    if n.startswith("3d_") or "hunyuan3d" in n:
        return "core", "generative", "three_d"
    if n.startswith("upscale"):
        return "aux", "postprocess", "upscale"
    if "txt_to_image_to_video" in n or "text_to_image_to_video" in n:
        return "core", "generative", "text_to_video"
    if "image_to_video" in n or "_i2v" in n or n.endswith("i2v") or "i2v" in n:
        return "core", "editing", "image_to_video"
    if (
        "_t2v" in n
        or "text_to_video" in n
        or "hunyuan_video_text_to_video" in n
        or (n.startswith("video_") and "t2v" in n)
    ):
        return "core", "generative", "text_to_video"
    if any(
        x in n
        for x in (
            "fun_inpaint",
            "fun_control",
            "fun_camera",
            "flf2v",
            "video_humo",
        )
    ):
        return "core", "editing", "video_editing"
    if any(
        x in n
        for x in (
            "edit",
            "inpaint",
            "outpaint",
            "chrono_edit",
            "kontext",
            "layered",
        )
    ):
        return "core", "editing", "image_editing"
    if n == "default":
        return "core", "generative", "text_to_image"
    return "core", "generative", "text_to_image"


def node_sort_key(nid: str) -> tuple:
    parts = str(nid).split(":")
    out: list = []
    for p in parts:
        out.append(int(p) if p.isdigit() else p)
    return tuple(out)


def infer_inject(graph: dict) -> tuple[dict, list[dict], list[dict]]:
    """Return inject dict, inputs list, outputs list for manifest."""
    inject: dict = {}
    nodes = {k: v for k, v in graph.items() if not str(k).startswith("__") and isinstance(v, dict)}

    clip_enc = sorted(
        [nid for nid, n in nodes.items() if n.get("class_type") == "CLIPTextEncode"],
        key=node_sort_key,
    )
    if len(clip_enc) >= 1:
        inject["prompt"] = {"node_id": str(clip_enc[0]), "input_key": "text"}
    if len(clip_enc) >= 2:
        inject["negative_prompt"] = {"node_id": str(clip_enc[1]), "input_key": "text"}

    ks = [
        nid
        for nid, n in nodes.items()
        if n.get("class_type") in ("KSampler", "SamplerCustomAdvanced")
    ]
    ks_sorted = sorted(ks, key=node_sort_key)
    if ks_sorted:
        kid = str(ks_sorted[0])
        inputs = nodes[kid].get("inputs") or {}
        if "seed" in inputs:
            inject["seed"] = {"node_id": kid, "input_key": "seed"}

    latent_classes = (
        "EmptyLatentImage",
        "EmptySD3LatentImage",
        "EmptyFlux2LatentImage",
        "EmptyLatentHunyuan3Dv2",
    )
    lat = sorted(
        [nid for nid, n in nodes.items() if n.get("class_type") in latent_classes],
        key=node_sort_key,
    )
    if lat:
        lid = str(lat[0])
        inp = nodes[lid].get("inputs") or {}
        if "width" in inp:
            inject["width"] = {"node_id": lid, "input_key": "width"}
        if "height" in inp:
            inject["height"] = {"node_id": lid, "input_key": "height"}

    manifest_inputs: list[dict] = []
    if "prompt" in inject:
        manifest_inputs.append(
            {
                "name": "prompt",
                "type": "string",
                "required": True,
            }
        )
    if "negative_prompt" in inject:
        manifest_inputs.append(
            {
                "name": "negative_prompt",
                "type": "string",
                "required": False,
                "default": "",
            }
        )
    if "width" in inject:
        manifest_inputs.append(
            {"name": "width", "type": "int", "required": False, "default": 1024}
        )
    if "height" in inject:
        manifest_inputs.append(
            {"name": "height", "type": "int", "required": False, "default": 1024}
        )
    if "seed" in inject:
        manifest_inputs.append({"name": "seed", "type": "int", "required": False})

    modality: list[str] = []
    for _nid, n in nodes.items():
        ct = n.get("class_type")
        if ct == "SaveVideo":
            modality = ["video"]
            break
        if ct == "SaveGLB":
            modality = ["mesh"]
            break
        if ct == "SaveImage":
            modality.append("image")
    if not modality:
        modality = ["image"]

    outputs = [{"name": "images", "type": "image_filenames", "description": "ComfyUI media outputs"}]
    if "video" in modality:
        outputs = [{"name": "video", "type": "image_filenames", "description": "ComfyUI SaveVideo outputs"}]
    elif "mesh" in modality:
        outputs = [{"name": "mesh", "type": "string", "description": "Exported 3D file"}]

    return inject, manifest_inputs, outputs


def modality_tags(area: str, domain: str, task: str) -> tuple[list[str], list[str]]:
    tags: list[str] = [task.replace("_", "-")]
    if task == "text_to_image":
        modality: list[str] = ["image"]
        tags += ["t2i", "generative"]
    elif task == "text_to_video":
        modality = ["video"]
        tags += ["t2v", "generative"]
    elif task == "image_to_video":
        modality = ["video"]
        tags += ["i2v", "editing"]
    elif task == "image_editing":
        modality = ["image"]
        tags += ["edit", "inpaint"]
    elif task == "video_editing":
        modality = ["video"]
        tags += ["video-edit"]
    elif task == "three_d":
        modality = ["mesh"]
        tags += ["3d", "generative"]
    elif task == "upscale":
        modality = ["image"]
        tags += ["upscale", "postprocess"]
    else:
        modality = ["image"]
    return modality, tags


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"Missing source dir: {SRC}")

    used_dirs: dict[str, int] = {}
    for path in sorted(SRC.glob("*.json")):
        stem = path.stem
        slug_base = slugify(stem)
        area, domain, task = route(stem)
        dir_key = f"{area}/{domain}/{task}/{slug_base}"
        if dir_key in used_dirs:
            used_dirs[dir_key] += 1
            slug = f"{slug_base}_{used_dirs[dir_key]}"
            dir_key = f"{area}/{domain}/{task}/{slug}"
        else:
            used_dirs[dir_key] = 0
            slug = slug_base

        dest_dir = DST_ROOT / area / domain / task / slug
        dest_dir.mkdir(parents=True, exist_ok=True)
        wf_name = f"{slug}.workflow.json"
        dest_wf = dest_dir / wf_name
        shutil.copy2(path, dest_wf)

        graph = json.loads(path.read_text(encoding="utf-8"))
        inject, inputs_manifest, outputs_manifest = infer_inject(graph)
        mod, tags = modality_tags(area, domain, task)
        nodes = {k: v for k, v in graph.items() if not str(k).startswith("__") and isinstance(v, dict)}
        if any(n.get("class_type") == "SaveVideo" for n in nodes.values()):
            mod = ["video"]
        elif any(n.get("class_type") == "SaveGLB" for n in nodes.values()):
            mod = ["mesh"]

        cap_id = f"{area}.{domain}.{task}.{slug.replace('-', '_')}"

        inject_yaml: dict[str, dict[str, str]] = {
            k: {"node_id": str(v["node_id"]), "input_key": str(v["input_key"])} for k, v in inject.items()
        }
        manifest_doc: dict = {
            "id": cap_id,
            "title": stem,
            "modality": mod,
            "description": f"ComfyUI API workflow (imported from model_apis/{path.name}).",
            "tags": tags,
            "workflow_file": wf_name,
            "inject": inject_yaml,
            "inputs": inputs_manifest,
            "outputs": outputs_manifest,
        }
        man_path = dest_dir / f"{slug}.manifest.yaml"
        man_path.write_text(
            yaml.dump(manifest_doc, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        print(dest_dir)


if __name__ == "__main__":
    main()
