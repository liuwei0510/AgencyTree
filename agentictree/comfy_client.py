from __future__ import annotations

import json
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import httpx


class ComfyClient:
    def __init__(
        self,
        base_url: str,
        timeout_sec: float = 120.0,
        poll_interval_sec: float = 1.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.poll_interval_sec = poll_interval_sec
        self._client = httpx.Client(timeout=timeout_sec)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ComfyClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def queue_prompt(self, prompt_graph: dict[str, Any], client_id: str | None = None) -> str:
        cid = client_id or str(uuid.uuid4())
        body = {"prompt": prompt_graph, "client_id": cid}
        r = self._client.post(f"{self.base_url}/prompt", json=body)
        r.raise_for_status()
        data = r.json()
        pid = data.get("prompt_id")
        if not pid:
            raise RuntimeError(f"Unexpected /prompt response: {data}")
        return str(pid)

    def get_history(self) -> dict[str, Any]:
        r = self._client.get(f"{self.base_url}/history")
        r.raise_for_status()
        return r.json()

    def wait_for_prompt(self, prompt_id: str) -> dict[str, Any]:
        deadline = time.time() + self.timeout_sec
        while time.time() < deadline:
            hist = self.get_history()
            entry = hist.get(prompt_id) if isinstance(hist, dict) else None
            if entry and entry.get("outputs"):
                return entry
            if entry:
                st = entry.get("status") or {}
                if st.get("status_str") == "error":
                    raise RuntimeError(f"ComfyUI prompt failed: {entry}")
            time.sleep(self.poll_interval_sec)
        raise TimeoutError(f"Timed out waiting for prompt_id={prompt_id}")

    def upload_image(self, file_path: Path, overwrite: bool = True) -> str:
        """Upload to /upload/image; returns filename usable by LoadImage."""
        with file_path.open("rb") as f:
            files = {"image": (file_path.name, f, "application/octet-stream")}
            data = {"type": "input", "overwrite": "true" if overwrite else "false"}
            r = self._client.post(f"{self.base_url}/upload/image", files=files, data=data)
        r.raise_for_status()
        j = r.json()
        name = j.get("name") or file_path.name
        return str(name)

    def relay_output_to_input(self, file_meta: dict[str, Any]) -> str:
        """Download via /view from output (or temp/input), upload to input; returns LoadImage filename."""
        params = {
            "filename": str(file_meta["filename"]),
            "type": str(file_meta.get("type") or "output"),
            "subfolder": str(file_meta.get("subfolder") or ""),
        }
        r = self._client.get(f"{self.base_url}/view", params=params)
        r.raise_for_status()
        orig_name = str(file_meta["filename"])
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td) / orig_name
            tmp_path.write_bytes(r.content)
            return self.upload_image(tmp_path)

    def interrupt(self) -> None:
        r = self._client.post(f"{self.base_url}/interrupt")
        r.raise_for_status()


def extract_media_outputs(history_entry: dict[str, Any]) -> dict[str, list[Any]]:
    """Collect filenames from a ComfyUI history entry outputs."""
    bucket: dict[str, list[Any]] = {}
    outputs = history_entry.get("outputs") or {}
    for _nid, payload in outputs.items():
        if not isinstance(payload, dict):
            continue
        for key in ("images", "gifs", "audio", "video"):
            if key not in payload:
                continue
            for item in payload[key]:
                if isinstance(item, dict) and "filename" in item:
                    bucket.setdefault(key, []).append(
                        {
                            "filename": item["filename"],
                            "subfolder": item.get("subfolder", ""),
                            "type": item.get("type", "output"),
                        }
                    )
                else:
                    bucket.setdefault(key, []).append(item)
    if not bucket and outputs:
        bucket["raw"] = [json.dumps(outputs)[:2000]]
    return bucket
