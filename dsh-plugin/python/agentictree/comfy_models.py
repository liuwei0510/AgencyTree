"""ComfyUI model-catalog records usable by workflow-building code."""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit


def comfyui_model_issues(metadata: dict) -> list[str]:
    """Check the minimal ComfyUI-Manager fields needed to reference a model."""
    issues = []
    for field in ("name", "type", "save_path", "filename", "url"):
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"Missing ComfyUI model field: {field}")

    save_path = metadata.get("save_path")
    if isinstance(save_path, str) and save_path:
        path = PurePosixPath(save_path)
        if path.is_absolute() or ".." in path.parts or "\\" in save_path:
            issues.append("ComfyUI model save_path must be relative to the models directory")

    filename = metadata.get("filename")
    if isinstance(filename, str) and filename and filename != "<huggingface>":
        if "/" in filename or "\\" in filename or filename in (".", ".."):
            issues.append("ComfyUI model filename must be a single file name")

    locator = metadata.get("url")
    if isinstance(locator, str) and locator:
        parsed = urlsplit(locator)
        direct_url = parsed.scheme == "https" and bool(parsed.netloc)
        huggingface_repo = (
            filename == "<huggingface>"
            and re.fullmatch(r"[^/\s]+/[^/\s]+", locator) is not None
            and urlsplit(str(metadata.get("reference", ""))).hostname == "huggingface.co"
        )
        if not (direct_url or huggingface_repo):
            issues.append("Unsupported ComfyUI model download locator")
    return issues


def usable_model_catalog(snapshot: dict) -> dict[str, dict]:
    """Return model records that code may bind into ComfyUI workflows."""
    return {
        key: record
        for key, record in snapshot.get("records", {}).items()
        if key.startswith("remote:model:")
        and record.get("kind") == "comfyui_model"
        and record.get("code_usable") is True
        and record.get("status") == "available"
    }
