"""Chat completions from ASU Research Computing's gateway (Q7, ADR-053).

The gateway is OpenAI-compatible (docs.rc.asu.edu/ai/api). It is free and
runs every model on ASU hardware. As measured on 2026-11-12, it answers
through a LiteLLM proxy over vLLM, not the gateway the docs describe: the
model list has no capability fields and there are no `x-obleth-*` headers.
So the deployment ID (`x-litellm-model-id`) and the server build
(`system_fingerprint`) are recorded instead, to show what answered.

Calls go through `RateLimitedHTTP`, so 429, 5xx and dropped connections are
retried (ADR-042). The gateway sends 503 with Retry-After when a model is
busy. Other 4xx responses raise `GatewayError`. The key is sent only in the
Authorization header and is never logged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sentinel.ingest.http import RateLimitedHTTP


class GatewayError(Exception):
    """A 4xx response, which a retry would only repeat."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"gateway answered {status}: {message}")
        self.status = status


@dataclass(frozen=True)
class Completion:
    content: str
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    deployment: str | None  # x-litellm-model-id: which deployment of the model answered
    fingerprint: str | None  # system_fingerprint: the model server's build
    seconds: float


class ChatClient:
    def __init__(self, http: RateLimitedHTTP, *, base_url: str, api_key: str) -> None:
        if not api_key:
            raise ValueError("SENTINEL_LLM_API_KEY is empty: create a key in the Voyager portal "
                             "(LLM Access tab) and put it in .env")
        self.http = http
        self.url = base_url.rstrip("/") + "/chat/completions"
        self._auth = {"Authorization": f"Bearer {api_key}"}

    def complete(self, *, model: str, messages: list[dict], schema: dict, schema_name: str,
                 max_tokens: int) -> Completion:
        """One completion at temperature 0, constrained to `schema`."""
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
        }
        started = time.monotonic()
        response = self.http.post(self.url, json=body, headers=self._auth)
        seconds = time.monotonic() - started
        if response.status_code != 200:
            raise GatewayError(response.status_code, _error_message(response))
        data = response.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        return Completion(
            content=choice["message"].get("content") or "",
            finish_reason=choice.get("finish_reason"),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            deployment=response.headers.get("x-litellm-model-id"),
            fingerprint=data.get("system_fingerprint"),
            seconds=seconds,
        )


def _error_message(response) -> str:
    try:
        error = response.json().get("error") or {}
        return str(error.get("message") or error)[:300]
    except ValueError:
        return response.text[:300]
