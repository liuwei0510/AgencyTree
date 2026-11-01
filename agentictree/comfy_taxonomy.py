"""Map ComfyUI catalog items onto the thesis' four-level ACT taxonomy."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from agentictree.storage import digest, read_json, write_json
from agentictree.comfy_models import comfyui_model_issues


_ALLOWED_TASKS = {
    "core": {"generative", "editing", "perception"},
    "aux": {"preprocess", "postprocess", "conditional", "evaluate", "latent_process"},
}


def _text(item: dict) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else item
    values = [item.get("name", "")]
    values.extend(metadata.get(key, "") for key in ("name", "title", "description", "base", "type"))
    return " ".join(map(str, values)).casefold()


def _media(item: dict, direction: str) -> set[str]:
    io = (item.get("metadata") or {}).get("io") or {}
    return {str(value.get("mediaType", "")).casefold()
            for value in io.get(direction, []) if isinstance(value, dict) and value.get("mediaType")}


def route_template(template: dict) -> tuple[str, str, str]:
    """Classify a workflow example using its declared ComfyUI I/O contract."""
    text = _text(template)
    title = str((template.get('metadata') or {}).get('title', '')).casefold().replace('-', ' ')
    inputs, outputs = _media(template, "inputs"), _media(template, "outputs")
    # Declared operation and output modality outrank incidental words in prose.
    if 'text to image' in title:
        return 'core', 'generative', 'text_to_image'
    if 'image to video' in title:
        return 'core', 'editing', 'image_to_video'
    if 'text to video' in title:
        return 'core', 'generative', 'text_to_video'
    if 'upscale' in title or 'upscaling' in title:
        return 'aux', 'postprocess', 'video_upscaling' if 'video' in outputs or ('video' in title and 'image' not in outputs) else 'upscale'
    has_video = "video" in inputs or "video" in outputs or bool(re.search(r"video|i2v|t2v|r2v|flf2v", text))
    has_image = "image" in inputs or "image" in outputs or bool(re.search(r"\b(image|images|photo|photos|portrait|frame)\b", text))
    has_audio = ("audio" in inputs or "audio" in outputs or
                 any(word in text for word in ("audio", "speech", "voice", "music", "sfx", "sound effects")))
    if outputs & {"mesh", "3d", "model3d", "glb"} or any(x in text for x in (
        "hunyuan3d", "generate 3d model", "text to 3d", "image to 3d", "3d reconstruction", "photo to 3d", "scale model",
        "text to model", "text-to-model"
    )):
        return "core", "generative", "three_d"
    if any(word in text for word in ("upscale", "super-resolution", "super resolution")):
        return "aux", "postprocess", "video_upscaling" if has_video else "upscale"
    if any(word in text for word in ("speech to text", "transcription", "transcribe", "speech recognition", "asr")):
        return "core", "perception", "audio_recognition"
    if any(word in text for word in ("depth estimation", "depth map", "depth anything", "moge depth")):
        return "core", "perception", "depth_estimation"
    if any(word in text for word in ("segmentation", "segment the", "image segmentation", "video segmentation")):
        return "core", "perception", "image_segmentation"
    if any(word in text for word in ("object detection", "face detection", "detect objects", "pose detection")):
        return "core", "perception", "object_detection"
    if any(word in title for word in ("caption", "describe visual", "image understanding", "multimodal reasoning", "visual content")):
        return "core", "perception", "image_captioning" if has_image else "media_understanding"

    if has_audio and not has_video:
        if any(word in text for word in ("text to speech", "text-to-speech", "text to audio", "text-to-audio",
                                         "text to music", "text-to-music", "text to sfx", "dialogue", "voice clone",
                                         "text to song", "text-to-song", "t2a", "music generation", "sound effects")):
            return "core", "generative", "text_to_audio"
        if "audio" in inputs and any(word in text for word in ("separation", "isolation", "speech to speech", "voice conversion")):
            return "core", "editing", "audio_editing"
        if "audio" in outputs:
            return ("core", "editing", "audio_editing") if "audio" in inputs else ("core", "generative", "text_to_audio")
    if has_video:
        if "video" in inputs or any(word in text for word in ("video edit", "restyle", "frame interpolation", "video enhance")):
            return "core", "editing", "video_editing"
        if "image" in inputs or any(word in text for word in ("image to video", "image-to-video", "i2v", "reference to video", "r2v", "first-last frame")):
            return "core", "editing", "image_to_video"
        if "video" in outputs or any(word in text for word in (
            "text to video", "text-to-video", "t2v", "generate video", "create video", "generate videos",
            "storytelling videos", "video generation"
        )):
            return "core", "generative", "text_to_video"
    if has_image:
        if any(word in text for word in ("edit", "inpaint", "outpaint", "replace", "remove object", "eraser",
                                         "relight", "restyle", "virtual try-on", "try on", "layer separation")):
            return "core", "editing", "image_editing"
        if "image" in inputs:
            return "core", "editing", "image_editing"
        if "image" in outputs or any(word in text for word in (
            "text to image", "text-to-image", "t2i", "generate image", "image generation", "panorama", "skybox", "batch generation", "sdxl"
        )):
            return "core", "generative", "text_to_image"
    if any(word in text for word in ("text to svg", "text-to-svg", "text to vector", "vector generation")):
        return "core", "generative", "vector_generation"
    if any(word in text for word in ("image to svg", "image-to-svg", "vectorize", "trace a bitmap")):
        return "core", "editing", "image_vectorization"
    if outputs & {"text", "string", "json"} and inputs & {"image", "audio", "video"}:
        return "core", "perception", "media_understanding"
    if any(word in text for word in ("quality check", "quality assessment", "score output", "evaluate")):
        return "aux", "evaluate", "quality_gate"
    if inputs and outputs:
        return "aux", "preprocess", "data_conversion"
    if any(word in text for word in ("batch generation", "batch process text prompts", "prompt multi batch")):
        return "aux", "preprocess", "batch_processing"
    if any(word in text for word in ("datatype conversion", "data type conversion", "switch node")):
        return "aux", "preprocess", "data_conversion"
    if has_audio:
        if "audio" in inputs:
            return "core", "editing", "audio_editing"
        return "core", "generative", "text_to_audio"
    if any(word in text for word in ("claude", "gemini", "gpt", "llm", "question answering", "reasoning")):
        return "core", "perception", "media_understanding"
    # The official template index can omit IO metadata. Retain the example in
    # the tree with an explicit generic abstract capability instead of dropping it.
    return "core", "generative", "multimedia_generation"


def route_model(model: dict) -> tuple[str, str, str]:
    """Map a ComfyUI-Manager model type/base into the paper's L1-L3 path."""
    kind = str(model.get("type", "")).casefold().replace("_", "-")
    base = str(model.get("base", "")).casefold()
    text = _text(model)
    if kind in {'llm', 'ram', 'blip-model'}:
        return 'core', 'perception', 'media_understanding'
    if kind == 'embedding':
        return 'aux', 'conditional', 'text_conditioning'
    if 'motion lora' in kind:
        return 'aux', 'conditional', 'motion_conditioning'
    if kind in {'controlnet', 't2i-adapter'}:
        for word in ('canny','depth','pose','lineart','segmentation','inpaint','tile','normal','scribble','union'):
            if word in text:
                return 'aux', 'conditional', word+'_conditioning'
    if kind == 'lora' and any(w in text for w in ('lightning','distill','lcm','hyper-sd')):
        return 'aux', 'latent_process', 'sampling_acceleration'
    if any(word in kind for word in ("upscale", "face-restore", "gfpgan", "codeformer", "rgt", "deepbump")):
        return "aux", "postprocess", "upscale" if "upscale" in kind or "upscale" in text else "image_restoration"
    if any(word in kind for word in ("controlnet", "t2i-adapter", "ip-adapter", "instantid", "pulid", "gligen", "photomaker", "ic-light", "lora", "embedding", "model-patch")):
        return "aux", "conditional", "reference_conditioning" if "ip-adapter" in kind or "instantid" in kind or "pulid" in kind else "control_conditioning"
    if 'vae' in kind or 'taesd' in kind:
        return 'aux', 'latent_process', 'encode_decode'
    if 'clip-vision' in kind:
        return 'aux', 'preprocess', 'image_encoding'
    if kind == 'clip':
        return 'aux', 'preprocess', 'text_encoding'
    if any(word in kind for word in ("sam", "ultralytics", "groundingdino", "depthanything", "depth-pro", "moge", "ram", "blip", "insightface")):
        if "depth" in kind or "moge" in kind:
            capability = "depth_estimation"
        elif "sam" in kind:
            capability = "image_segmentation"
        elif "groundingdino" in kind or "ultralytics" in kind:
            capability = "object_detection"
        elif "insightface" in kind:
            capability = "face_analysis"
        else:
            capability = "image_understanding"
        return "core", "perception", capability
    if any(word in text for word in ("hunyuan3d", "zero123", "3d model", "mesh")) or "3d" in base:
        return "core", "generative", "three_d"
    if any(word in text for word in ("image-to-video", "image to video", "i2v")):
        return "core", "editing", "image_to_video"
    if 'animatediff' in kind or any(word in base for word in ("video", "wan", "ltx", "animatediff", "cosmos")) or any(word in text for word in ("text-to-video", "text to video", "t2v")):
        return "core", "generative", "text_to_video"
    if any(word in text for word in ("image edit", "inpaint", "outpaint", "kontext")) or "edit" in base:
        return "core", "editing", "image_editing"
    return "core", "generative", "text_to_image"


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._-")
    return value[:140] or "unnamed"


def model_leaf_name(model: dict) -> str:
    identity = [model.get("type"), model.get("save_path"), model.get("filename"), model.get("name")]
    return "model_" + _slug(model.get("name") or model.get("filename") or "unnamed") + "_" + digest(identity)[:8]


def template_leaf_name(name: str) -> str:
    return "comfyui_" + _slug(name or "example") + "_" + digest(name)[:8]


def api_example_leaf_name(name: str) -> str:
    return "comfyui_api_" + template_leaf_name(name).removeprefix("comfyui_")


def model_io_types(branch: tuple[str, str, str], model: dict) -> tuple[list[str], list[str]]:
    domain, task, capability = branch
    if capability=='text_encoding':
        return ['text'], ['conditioning']
    if capability=='image_encoding':
        return ['image'], ['image embedding']
    if capability=='encode_decode':
        return ['image or latent'], ['latent or image']
    if capability == "text_to_image":
        return ["text"], ["image"]
    if capability == "text_to_video":
        return ["text"], ["video"]
    if capability == "image_to_video":
        return ["image", "text"], ["video"]
    if capability in {"image_editing", "image_restoration", "upscale"}:
        return ["image"], ["image"]
    if capability == "three_d":
        return ["text or image"], ["mesh"]
    if domain == "core" and task == "perception":
        return ["image or video"], ["structured media analysis"]
    if task == "conditional":
        return ["image or text conditioning"], ["conditioning/model component"]
    return ["ComfyUI workflow/model input"], ["ComfyUI model component"]


def materialize_catalog(sources: dict, atomic_root: Path) -> dict:
    """Place the current model and official example leaves under atomic_workflows.

    The catalog files are deliberately distinct from ``*.manifest.yaml`` so
    editor-format examples do not enter the API-only execution planner.
    """
    if not sources.get("complete"):
        return {"models": 0, "templates": 0, "skipped": "source snapshot is incomplete"}
    atomic_root = atomic_root.resolve()
    models_written = templates_written = 0
    current_model_paths: set[Path] = set()
    current_template_paths: set[Path] = set()
    model_evidence = sources.get("provenance", {}).get("models", {})
    template_evidence = sources.get("provenance", {}).get("templates", {})

    for model in sources.get("models", []):
        branch = route_model(model)
        if branch[0] not in _ALLOWED_TASKS or branch[1] not in _ALLOWED_TASKS[branch[0]]:
            continue
        slug = model_leaf_name(model)
        folder = atomic_root.joinpath(*branch, slug)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "model.yaml"
        input_types, output_types = model_io_types(branch, model)
        name = model.get("name") or model.get("filename") or slug
        payload = {
            "id": "remote.model." + digest([model.get("type"), model.get("save_path"), model.get("filename"), name])[:20],
            "kind": "comfyui_model", "title": name, "tree_branch": list(branch),
            "six_tuple": {
                "name": name, "input_types": input_types, "output_types": output_types,
                "capability": "/".join(branch),
                "parameters": [{"name": "filename", "value": model.get("filename")},
                               {"name": "base", "value": model.get("base")}],
                "invocation": {"platform": "ComfyUI", "interface": "load from model catalog using a compatible ComfyUI node",
                               "model_type": model.get("type"), "save_path": model.get("save_path"),
                               "filename": model.get("filename"), "download_locator": model.get("url")},
            },
            "code_usable": not comfyui_model_issues(model),
            "status": "available" if not comfyui_model_issues(model) else "unusable_format", "metadata": model,
            "evidence": model_evidence,
        }
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        current_model_paths.add(path)
        models_written += 1

    for template in sources.get("workflows", []):
        if template.get("status") == "fetch_failed" or not template.get("path"):
            continue
        source_path = Path(template["path"]).resolve()
        try:
            graph = read_json(source_path)
        except (OSError, ValueError):
            continue
        if not isinstance(graph, dict) or digest(graph) != template.get("sha256"):
            continue
        branch = route_template(template)
        if branch[0] not in _ALLOWED_TASKS or branch[1] not in _ALLOWED_TASKS[branch[0]]:
            continue
        slug = template_leaf_name(template.get("name", "example"))
        folder = atomic_root.joinpath(*branch, slug)
        folder.mkdir(parents=True, exist_ok=True)
        workflow_name = "workflow.editor.json" if template.get("format") == "editor" else "workflow.api.json"
        dest_graph = folder / workflow_name
        write_json(dest_graph, graph)
        metadata = template.get("metadata") or {}
        io = metadata.get("io") or {}
        io_inputs = io.get("inputs") or []
        io_outputs = io.get("outputs") or []
        manifest_path = folder / "template.yaml"
        payload = {
            "id": "remote.template." + str(template.get("name", "example")),
            "kind": "comfyui_template", "title": metadata.get("title") or template.get("name"),
            "tree_branch": list(branch), "workflow_file": workflow_name,
            "six_tuple": {
                "name": metadata.get("title") or template.get("name"),
                "input_types": [item.get("mediaType") for item in io_inputs if item.get("mediaType")],
                "output_types": [item.get("mediaType") for item in io_outputs if item.get("mediaType")],
                "capability": metadata.get("description") or "/".join(branch),
                "parameters": [],
                "invocation": {"platform": "ComfyUI", "format": template.get("format"),
                               "workflow_file": workflow_name,
                               "interaction": "import the editor workflow into ComfyUI and queue it"},
            },
            "format": template.get("format"), "code_usable": True, "status": "available",
            "source": {"url": template.get("url"), "sha256": template.get("sha256"),
                       "revision": template_evidence.get("revision")},
            "metadata": metadata,
        }
        manifest_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        current_template_paths.add(manifest_path)
        templates_written += 1

    for example in sources.get("api_examples", []):
        if example.get("status") == "fetch_failed" or not example.get("path"):
            continue
        source_path = Path(example["path"]).resolve()
        try:
            graph = read_json(source_path)
        except (OSError, ValueError):
            continue
        if not isinstance(graph, dict) or digest(graph) != example.get("sha256"):
            continue
        branch = ("core", "generative", "text_to_image")
        slug = api_example_leaf_name(example.get("name", "example"))
        folder = atomic_root.joinpath(*branch, slug)
        folder.mkdir(parents=True, exist_ok=True)
        workflow_name = "workflow.api.json"
        write_json(folder / workflow_name, graph)
        metadata = example.get("metadata") or {}
        manifest_path = folder / "template.yaml"
        payload = {
            "id": "remote.api_example." + str(example.get("name", "example")),
            "kind": "comfyui_template", "title": metadata.get("title") or example.get("name"),
            "tree_branch": list(branch), "workflow_file": workflow_name,
            "six_tuple": {"name": metadata.get("title") or example.get("name"),
                          "input_types": ["text"], "output_types": ["image"],
                          "capability": "/".join(branch), "parameters": [],
                          "invocation": {"platform": "ComfyUI", "format": "api",
                                         "workflow_file": workflow_name,
                                         "interaction": "submit the API prompt graph to ComfyUI /prompt"}},
            "format": "api", "code_usable": True, "status": "available",
            "source": {"url": example.get("url"), "sha256": example.get("sha256")},
            "metadata": metadata,
        }
        manifest_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        current_template_paths.add(manifest_path)
        templates_written += 1

    # Preserve removed catalog leaves as explicit tombstones for traceability.
    for path in atomic_root.rglob("model.yaml"):
        if path not in current_model_paths:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if data.get("kind") == "comfyui_model" and data.get("status") != "deprecated":
                    data["status"] = "deprecated"
                    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            except (OSError, yaml.YAMLError):
                pass
    for path in atomic_root.rglob("template.yaml"):
        if path not in current_template_paths:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if data.get("kind") == "comfyui_template" and data.get("status") != "deprecated":
                    data["status"] = "deprecated"
                    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            except (OSError, yaml.YAMLError):
                pass
    return {"models": models_written, "templates": templates_written}
