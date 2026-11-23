"""Deterministically convert ComfyUI editor workflows to API prompt graphs.

Conversion intentionally fails closed when widget values cannot be mapped to
input names. Pass ``/object_info`` data for legacy nodes whose editor JSON does
not contain enough widget-slot metadata.
"""
from __future__ import annotations

from typing import Any

from agentictree.graph_validate import validate_api_graph


_UI_ONLY_TYPES = {"MarkdownNote", "Note", "Reroute"}
_CORE_WIDGET_NAMES = {
    # Common ComfyUI core nodes whose widget values are present in editor JSON
    # but whose widget-only inputs are not serialized as input slots.
    "CheckpointLoaderSimple": ["ckpt_name"],
    "VAELoader": ["vae_name"],
    "CLIPLoader": ["clip_name", "type", "device"],
    "DualCLIPLoader": ["clip_name1", "clip_name2", "type"],
    "UNETLoader": ["unet_name", "weight_dtype"],
    "LoraLoader": ["lora_name", "strength_model", "strength_clip"],
    "LoraLoaderModelOnly": ["lora_name", "strength_model"],
    "ControlNetLoader": ["control_net_name"],
    "CLIPVisionLoader": ["clip_name"],
    "UpscaleModelLoader": ["model_name"],
    "CLIPTextEncode": ["text"],
    "KSampler": ["seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
    "KSamplerAdvanced": ["add_noise", "noise_seed", "steps", "cfg", "sampler_name", "scheduler",
                          "start_at_step", "end_at_step", "return_with_leftover_noise"],
    "EmptyLatentImage": ["width", "height", "batch_size"],
    "EmptySD3LatentImage": ["width", "height", "batch_size"],
    "EmptyImage": ["width", "height", "batch_size", "color"],
    "SaveImage": ["filename_prefix"],
    "SaveAnimatedWEBP": ["filename_prefix", "fps", "lossless", "quality", "method"],
    "LoadImage": ["image"],
    "LoadImageMask": ["image", "channel"],
    "ImageScale": ["upscale_method", "width", "height", "crop"],
    "ImageScaleBy": ["upscale_method", "scale_by"],
    "ImageCrop": ["width", "height", "x", "y"],
    "CLIPSetLastLayer": ["stop_at_clip_layer"],
}
_SEED_CONTROL_VALUES = {"fixed", "randomize", "increment", "decrement"}


def _class_type(node: dict[str, Any]) -> str | None:
    props = node.get("properties") or {}
    return props.get("Node name for S&R") or node.get("type")


def _object_info_widget_names(class_type: str, node: dict[str, Any], object_info: dict | None) -> list[str] | None:
    """Infer legacy widget names from ComfyUI /object_info, preserving order."""
    if object_info and class_type in object_info:
        spec = object_info[class_type].get("input", {})
        names = list(spec.get("required", {}).keys()) + list(spec.get("optional", {}).keys())
    else:
        names = _CORE_WIDGET_NAMES.get(class_type)
    if names is None:
        return None
    slots = node.get("inputs") or []
    slot_by_name = {slot.get("name"): slot for slot in slots if isinstance(slot, dict)}
    # Socket-only inputs appear as editor slots without widget metadata. A
    # widget slot remains a widget unless it is currently linked.
    return [name for name in names
            if name not in slot_by_name or (slot_by_name[name].get("widget") and slot_by_name[name].get("link") is None)]


def convert_editor_workflow(workflow: dict[str, Any], object_info: dict | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Return ``(api_graph, issues)``; an ambiguous graph is never emitted."""
    if not isinstance(workflow, dict) or not workflow:
        return None, ["Workflow must be a nonempty JSON object"]
    if "nodes" not in workflow:
        issues = validate_api_graph(workflow, object_info)
        return (workflow, []) if not issues else (None, issues)
    raw_nodes = workflow.get("nodes")
    if not isinstance(raw_nodes, list):
        return None, ["Editor workflow nodes must be an array"]

    nodes: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for node in raw_nodes:
        if not isinstance(node, dict) or node.get("id") is None:
            issues.append("Every editor node must be an object with an id")
            continue
        node_id = str(node["id"])
        if node_id in nodes:
            issues.append(f"Duplicate node id {node_id}")
        nodes[node_id] = node

    # Link IDs are used to validate the serialized input slots. ComfyUI's
    # standard editor format stores [id, origin, origin_slot, target, slot, type].
    links: dict[str, tuple[str, int, str, int]] = {}
    raw_links = workflow.get("links") or []
    if not isinstance(raw_links, list):
        issues.append("Editor workflow links must be an array")
        raw_links = []
    for link in raw_links:
        try:
            if isinstance(link, list) and len(link) >= 5:
                link_id, origin, origin_slot, target, target_slot = link[:5]
            elif isinstance(link, dict):
                link_id = link.get("id")
                origin = link.get("origin_id", link.get("origin"))
                origin_slot = link.get("origin_slot")
                target = link.get("target_id", link.get("target"))
                target_slot = link.get("target_slot")
            else:
                raise ValueError
            links[str(link_id)] = (str(origin), int(origin_slot), str(target), int(target_slot))
        except (TypeError, ValueError):
            issues.append(f"Invalid editor link: {link!r}")

    if issues:
        return None, issues

    api: dict[str, Any] = {}
    skipped = set()
    for node_id, node in nodes.items():
        mode = node.get("mode", 0)
        if mode == 2:  # LiteGraph NEVER / muted nodes are omitted by ComfyUI.
            skipped.add(node_id)
            continue
        if mode == 4:
            issues.append(f"{node_id}: bypass-mode nodes need graph rewiring and are not converted offline")
            continue
        class_type = _class_type(node)
        if class_type in _UI_ONLY_TYPES:
            skipped.add(node_id)
            continue
        if not isinstance(class_type, str) or not class_type:
            issues.append(f"{node_id}: cannot determine ComfyUI class_type")
            continue
        node_inputs: dict[str, Any] = {}
        slots = node.get("inputs") or []
        if not isinstance(slots, list):
            issues.append(f"{node_id}: inputs must be an array")
            continue
        linked_widget_names = set()
        for slot_index, slot in enumerate(slots):
            if not isinstance(slot, dict) or not slot.get("name"):
                issues.append(f"{node_id}: input slot {slot_index} has no name")
                continue
            name = str(slot["name"])
            link_id = slot.get("link")
            if link_id is None:
                continue
            edge = links.get(str(link_id))
            if edge is None:
                issues.append(f"{node_id}.{name}: references missing link {link_id}")
                continue
            origin, origin_slot, target, _ = edge
            if target != node_id:
                issues.append(f"{node_id}.{name}: link {link_id} targets {target}, not {node_id}")
                continue
            if origin in nodes and origin not in skipped:
                node_inputs[name] = [origin, origin_slot]
            if slot.get("widget"):
                linked_widget_names.add(name)

        values = list(node.get("widgets_values") or [])
        if not isinstance(values, list):
            issues.append(f"{node_id}: widgets_values must be an array")
            continue
        explicit_slots = [slot for slot in slots if isinstance(slot, dict) and slot.get("widget")
                          and slot.get("link") is None and slot.get("name") not in linked_widget_names]
        widget_names: list[str] | None
        if len(explicit_slots) == len(values):
            widget_names = [str(slot["name"]) for slot in explicit_slots]
        elif not values:
            widget_names = []
        else:
            widget_names = _object_info_widget_names(class_type, node, object_info)
        if widget_names is not None and len(values) == len(widget_names) + 1 and len(values) > 1 \
                and class_type in {"KSampler", "KSamplerAdvanced"} and values[1] in _SEED_CONTROL_VALUES:
            # Older editor formats save the frontend-only seed behavior widget;
            # executionUtil deliberately excludes it from API prompt inputs.
            del values[1]
        if widget_names is not None and len(widget_names) != len(values):
            # LoadImage's second serialized widget selects the upload control;
            # the executable input is only its selected image filename.
            if class_type in {"LoadImage", "LoadImageMask"} and len(values) == 2 and len(widget_names) == 1:
                values = values[:1]
            else:
                widget_names = None
        if widget_names is None:
            issues.append(f"{node_id} ({class_type}): {len(values)} widget values cannot be mapped unambiguously; provide /object_info or explicit widget slots")
            continue
        for name, value in zip(widget_names, values):
            # In API prompt JSON arrays are links, so the frontend wraps literal
            # array-valued widgets to preserve their meaning.
            node_inputs[name] = {"__value__": value} if isinstance(value, list) else value

        props = node.get("properties") or {}
        meta = {"title": node.get("title") or class_type}
        for source, target in (("cnr_id", "cnr_id"), ("aux_id", "aux_id"), ("ver", "ver")):
            if props.get(source):
                meta[target] = props[source]
        api[node_id] = {"inputs": node_inputs, "class_type": class_type, "_meta": meta}

    if issues:
        return None, issues
    # Links from nodes intentionally omitted by the frontend are omitted too.
    for node in api.values():
        for name, value in list(node["inputs"].items()):
            if isinstance(value, list) and len(value) == 2 and str(value[0]) not in api:
                del node["inputs"][name]
    issues.extend(validate_api_graph(api, object_info))
    return (api, []) if not issues else (None, issues)
