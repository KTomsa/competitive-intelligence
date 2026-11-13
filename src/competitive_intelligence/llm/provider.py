"""Responses API adapter. Only declared function tools can reach application code."""

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from pydantic import Field

from ..schemas import StrictModel


class ModelError(RuntimeError):
    """Provider failure with a safe code; never include headers, prompts or response bodies."""


class ToolCall(StrictModel):
    call_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=80)
    arguments: str = Field(max_length=16000)


@dataclass
class ModelReply:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    output: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class ModelProvider(Protocol):
    model: str

    def generate(self, *, instructions: str, items: list[dict[str, Any]],
                 schema: dict[str, Any] | None, tools: list[dict[str, Any]],
                 max_output_tokens: int) -> ModelReply: ...


class OpenAIResponsesProvider:
    """Fixed OpenAI endpoint: credentials cannot be redirected by source/model text."""

    def __init__(self, api_key: str, model: str, *, timeout: float = 30,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._key = api_key
        self.model = model
        self._timeout = timeout
        self._transport = transport

    def generate(self, *, instructions, items, schema=None, tools=None, max_output_tokens=2000) -> ModelReply:
        payload = {"model": self.model, "instructions": instructions, "input": items,
                   "max_output_tokens": max_output_tokens, "store": False}
        if schema is not None:
            payload["text"] = {"format": {"type": "json_schema", "name": "claims",
                                          "schema": schema, "strict": True}}
        if tools:
            payload.update(tools=tools, parallel_tool_calls=False)
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout,
                              follow_redirects=False, trust_env=False) as client:
                with client.stream("POST", "https://api.openai.com/v1/responses",
                                   headers={"Authorization": f"Bearer {self._key}"}, json=payload) as response:
                    if response.status_code != 200:
                        raise ModelError(f"PROVIDER_HTTP_{response.status_code}")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            raise ModelError("PROVIDER_RESPONSE_TOO_LARGE")
            import json

            data = json.loads(body)
            if data.get("status") != "completed":
                raise ModelError("PROVIDER_INCOMPLETE")
            output = data["output"]
            calls, texts = [], []
            if not isinstance(output, list) or len(output) > 100:
                raise ModelError("PROVIDER_INVALID_OUTPUT")
            for item in output:
                match item.get("type"):
                    case "function_call":
                        calls.append(ToolCall.model_validate({name: item[name]
                                                              for name in ("call_id", "name", "arguments")}))
                    case "message":
                        for content in item["content"]:
                            if content["type"] == "refusal":
                                raise ModelError("PROVIDER_REFUSED")
                            if content["type"] == "output_text":
                                texts.append(content["text"])
                    case "reasoning":
                        pass  # Retained for stateless Responses continuation; never persisted in traces.
                    case _:
                        raise ModelError("PROVIDER_UNEXPECTED_OUTPUT")
            usage = data.get("usage") or {}
            return ModelReply(text="".join(texts), calls=calls, output=output,
                              input_tokens=max(0, int(usage.get("input_tokens", 0))),
                              output_tokens=max(0, int(usage.get("output_tokens", 0))))
        except ModelError:
            raise
        except Exception:
            # HTTP/JSON/validation exceptions can contain raw input or Authorization.
            raise ModelError("PROVIDER_UNAVAILABLE_OR_INVALID") from None
