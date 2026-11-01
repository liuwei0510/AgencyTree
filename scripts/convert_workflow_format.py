#!/usr/bin/env python3
"""Convert ComfyUI editor workflow JSON files to API prompt graphs offline."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentictree.editor_to_api import convert_editor_workflow
from agentictree.storage import digest, read_json, write_json


def load_object_info(path: Path | None, comfy_url: str | None) -> dict | None:
    if path:
        return read_json(path)
    if comfy_url:
        response = httpx.get(comfy_url.rstrip("/") + "/object_info", timeout=45, follow_redirects=True)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("/object_info did not return a JSON object")
        return value
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="An editor workflow JSON file or a directory of workflow JSONs")
    parser.add_argument("--output", type=Path, help="Output JSON path, or output directory for a directory source")
    parser.add_argument("--object-info", type=Path, help="Saved ComfyUI /object_info response for legacy widget mapping")
    parser.add_argument("--comfy-url", help="Fetch /object_info from a reachable ComfyUI server")
    args = parser.parse_args()

    try:
        object_info = load_object_info(args.object_info, args.comfy_url)
        source = args.source.expanduser().resolve()
        if source.is_file():
            files = [source]
            out_root = None
        elif source.is_dir():
            files = sorted(p for p in source.rglob("*.json") if not p.name.endswith(".api.json")
                           and p.name != "conversion-report.json")
            out_root = (args.output or (source / "api_format")).expanduser().resolve()
        else:
            raise FileNotFoundError(source)

        report = {"source": str(source), "object_info_supplied": object_info is not None,
                  "converted": 0, "failed": 0, "items": []}
        for file in files:
            try:
                editor = read_json(file)
                api, issues = convert_editor_workflow(editor, object_info)
                if api is None:
                    report["failed"] += 1
                    report["items"].append({"source": str(file), "status": "needs_metadata_or_review", "issues": issues})
                    continue
                if out_root is not None:
                    target = out_root / file.relative_to(source).with_suffix(".json")
                else:
                    target = (args.output.expanduser().resolve() if args.output else
                              file.with_name(file.stem + ".api.json"))
                write_json(target, api)
                report["converted"] += 1
                report["items"].append({"source": str(file), "output": str(target),
                                         "status": "converted", "sha256": digest(api)})
            except (OSError, ValueError, TypeError) as error:
                report["failed"] += 1
                report["items"].append({"source": str(file), "status": "error", "issues": [str(error)]})

        if out_root is not None:
            write_json(out_root / "conversion-report.json", report)
        print(json.dumps({k: v for k, v in report.items() if k != "items"}, ensure_ascii=False, indent=2))
        return int(report["failed"] > 0)
    except (OSError, ValueError, httpx.HTTPError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
