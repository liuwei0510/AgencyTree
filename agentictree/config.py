from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    cfg_path = path or (root / "config.yaml")
    if not cfg_path.is_file():
        return {
            "comfy": {"base_url": "http://127.0.0.1:8188", "poll_interval_sec": 1.0, "timeout_sec": 900.0},
            "atomic_workflows_root": str(root / "atomic_workflows"),
            "local_comfy_root": None,
            "openai": {"model": "gpt-4o-mini", "base_url": None},
        }
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
