"""Normalize provenance references in the vendored catalog for redistribution."""
from pathlib import Path
import json
import re

root = Path(__file__).resolve().parents[1] / "python"
prefix = re.compile(r"/Users/[^/]+/Library/Mobile Documents/com~apple~CloudDocs/agentictreeforworkflow/")
paths = [root / "data/act/latest.json", *root.glob("data/act/versions/*.json"),
         *root.glob("atomic_workflows/**/model.yaml"),
         *root.glob("atomic_workflows/**/template.yaml")]
for path in paths:
    text = path.read_text(encoding="utf-8")
    portable = prefix.sub("", text)
    if portable != text:
        path.write_text(portable, encoding="utf-8")
path = root / "data/benchmarks/requirements_220.json"
value = json.loads(path.read_text())
cloud = re.compile(r"/Users/[^/]+/Library/Mobile Documents/com~apple~CloudDocs/")
def replace(item):
    if isinstance(item, dict):
        return {key: replace(value) for key, value in item.items()}
    if isinstance(item, list):
        return [replace(value) for value in item]
    if isinstance(item, str):
        return cloud.sub("", item)
    return item
path.write_text(json.dumps(replace(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
