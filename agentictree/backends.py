"""One JSON contract for API models and external agent harnesses.

Harness commands are argument arrays, never shell fragments. They receive a JSON
request on stdin and must return one JSON object on stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable
from pathlib import Path
from typing import Protocol


class Backend(Protocol):
    def complete_json(self, system: str, user: str) -> str: ...


def json_object(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Agent response must be one JSON object")
    return json.dumps(value, ensure_ascii=False)


class APIBackend:
    def __init__(self, cfg: dict):
        from openai import OpenAI
        key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
        key = os.environ.get(key_env)
        if not key:
            raise ValueError(f"Missing {key_env}; configure a command harness or explicitly use --no-llm")
        self.model = cfg.get("model")
        if not self.model:
            raise ValueError("API backend requires an explicit model")
        options = {"api_key": key, "base_url": cfg.get("base_url"),
                   "timeout": float(cfg.get("timeout_sec", 180)), "max_retries": 1}
        if cfg.get("trust_env") is False:
            import httpx
            options["http_client"] = httpx.Client(trust_env=False)
        self.client = OpenAI(**options)

    def complete_json(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return json_object(response.choices[0].message.content or "")


class CommandBackend:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def complete_json(self, system: str, user: str) -> str:
        command = self.cfg.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise ValueError("Harness command must be a nonempty argument array")
        payload = json.dumps({"system": system, "user": user, "response_format": "json_object"}, ensure_ascii=False)
        result = subprocess.run(command, input=payload, text=True, capture_output=True,
                                cwd=self.cfg.get("cwd"), timeout=float(self.cfg.get("timeout_sec", 180)))
        if result.returncode:
            # stderr may contain credentials or prompt text: leave it out of saved assets.
            raise RuntimeError(f"Harness exited with code {result.returncode}")
        return json_object(result.stdout)


class HostBackend:
    """Call an agent already hosted by the embedding application or harness."""
    def __init__(self, handler: Callable[[str, str], dict | str]):
        self.handler = handler

    def complete_json(self, system: str, user: str) -> str:
        result = self.handler(system, user)
        return json_object(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))


def make_backend(cfg: dict | None) -> Backend | None:
    cfg = cfg or {}
    provider = cfg.get("provider", "api")
    if provider == "heuristic":
        return None
    if provider == "api":
        return APIBackend(cfg)
    if provider == "command":
        return CommandBackend(cfg)
    raise ValueError(f"Unknown agent provider: {provider}")
