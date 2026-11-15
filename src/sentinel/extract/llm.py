"""Chat completions from ASU Research Computing's gateway (Q7, ADR-053, ADR-054).

The gateway is OpenAI-compatible (docs.rc.asu.edu/ai/api). It is free and
runs every model on ASU hardware. Keys made since 2026-11-14 reach the
gateway the docs describe; older "Legacy" keys reached a LiteLLM proxy over
the same model servers (ADR-053). Each completion records what answered:
- `system_fingerprint`, the model server's build, from both gateways;
- `x-obleth-model-variant`, the member of a model family, on the current
  gateway (absent for a model that isn't a family);
- `x-litellm-model-id`, the deployment, on the legacy one.

Add-ons ("boons", such as a draft model answering first) are turned off with
`x-obleth-boons: off`, as the docs advise for reproducible research.

Calls go through `RateLimitedHTTP`, so 429, 5xx and dropped connections are
retried (ADR-042). The gateway sends 503 with Retry-After when a model is
busy. Other 4xx responses raise `GatewayError`. The key is sent only in the
Authorization header and is never logged.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
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
    deployment: str | None  # which variant or deployment answered (see the module docstring)
    fingerprint: str | None  # system_fingerprint: the model server's build
    seconds: float
    energy_wh: float = 0.0  # the current gateway's own estimate, in usage
    co2_g: float = 0.0


def request_body(*, model: str, messages: list[dict], schema: dict, schema_name: str,
                 max_tokens: int) -> dict:
    """A chat completion at temperature 0, constrained to `schema`. Shared by
    direct calls and batch lines, so both ask exactly the same thing."""
    return {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
    }


def parse_completion(data: dict, headers: Mapping[str, str] | None = None,
                     seconds: float = 0.0) -> Completion:
    """A chat completion response body, from a direct call or a batch line."""
    headers = headers or {}
    choice = data["choices"][0]
    usage = data.get("usage") or {}
    return Completion(
        content=choice["message"].get("content") or "",
        finish_reason=choice.get("finish_reason"),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        deployment=headers.get("x-obleth-model-variant") or headers.get("x-litellm-model-id"),
        fingerprint=data.get("system_fingerprint"),
        seconds=seconds,
        energy_wh=float(usage.get("energy_wh") or 0.0),
        co2_g=float(usage.get("co2_g") or 0.0),
    )


class ChatClient:
    def __init__(self, http: RateLimitedHTTP, *, base_url: str, api_key: str) -> None:
        if not api_key:
            raise ValueError("SENTINEL_LLM_API_KEY is empty: create a key in the Voyager portal "
                             "(LLM Access tab) and put it in .env")
        self.http = http
        self.url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}", "x-obleth-boons": "off"}

    def complete(self, *, model: str, messages: list[dict], schema: dict, schema_name: str,
                 max_tokens: int) -> Completion:
        body = request_body(model=model, messages=messages, schema=schema,
                            schema_name=schema_name, max_tokens=max_tokens)
        started = time.monotonic()
        response = self.http.post(self.url, json=body, headers=self._headers)
        seconds = time.monotonic() - started
        if response.status_code != 200:
            raise GatewayError(response.status_code, _error_message(response))
        return parse_completion(response.json(), response.headers, seconds)


class BatchClient:
    """The gateway's batch API (docs.rc.asu.edu/ai/api/batches): upload a
    JSONL file of requests, create a batch on it, poll it, read the output
    and error files. Batches run only on capacity interactive users aren't
    using, so a busy model may take hours.

    Uploading and creating are POSTs, retried on 5xx like the rest (ADR-042).
    A retried create could leave a duplicate batch on the gateway; only the
    batch whose ID comes back is read. Request lines carry no headers, so the
    add-on switch can't be sent per line; no model used so far has add-ons.
    Output lines carry the response body but no headers, and a batch's status
    names only the model, so a model family's variant isn't recorded in batch
    mode; the server build (`system_fingerprint`) is. gemma4-31b-it isn't a
    family, so it has no variant either way."""

    def __init__(self, http: RateLimitedHTTP, *, base_url: str, api_key: str) -> None:
        if not api_key:
            raise ValueError("SENTINEL_LLM_API_KEY is empty: create a key in the Voyager portal "
                             "(LLM Access tab) and put it in .env")
        self.http = http
        self.base = base_url.rstrip("/")
        self._auth = {"Authorization": f"Bearer {api_key}"}

    def submit(self, lines: list[dict], *, name: str, window: str) -> str:
        """Upload the requests and start a batch on them. Returns its ID."""
        payload = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines).encode("utf-8")
        uploaded = self.http.post(f"{self.base}/files", headers=self._auth, data={"purpose": "batch"},
                                  files={"file": (f"{name}.jsonl", payload, "application/jsonl")})
        if uploaded.status_code != 200:
            raise GatewayError(uploaded.status_code, _error_message(uploaded))
        created = self.http.post(f"{self.base}/batches", headers=self._auth, json={
            "input_file_id": uploaded.json()["id"], "endpoint": "/v1/chat/completions",
            "completion_window": window, "metadata": {"name": name}})
        if created.status_code != 200:
            raise GatewayError(created.status_code, _error_message(created))
        return created.json()["id"]

    def status(self, batch_id: str) -> dict:
        response = self.http.get(f"{self.base}/batches/{batch_id}", headers=self._auth)
        if response.status_code != 200:
            raise GatewayError(response.status_code, _error_message(response))
        return response.json()

    def result_lines(self, status: dict) -> list[dict]:
        """Every line of a finished batch's output and error files, in no
        particular order: match them by `custom_id`."""
        lines: list[dict] = []
        for key in ("output_file_id", "error_file_id"):
            if not status.get(key):
                continue
            response = self.http.get(f"{self.base}/files/{status[key]}/content", headers=self._auth)
            if response.status_code != 200:
                raise GatewayError(response.status_code, _error_message(response))
            lines.extend(json.loads(line) for line in response.text.splitlines() if line.strip())
        return lines

    def cancel(self, batch_id: str) -> None:
        """Stop the requests that haven't started. Finished ones stay in the output."""
        self.http.post(f"{self.base}/batches/{batch_id}/cancel", headers=self._auth)


def _error_message(response) -> str:
    try:
        error = response.json().get("error") or {}
        return str(error.get("message") or error)[:300]
    except ValueError:
        return response.text[:300]
