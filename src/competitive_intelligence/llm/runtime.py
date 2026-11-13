"""Per-audit budgets and private, content-free agent/tool traces."""

import hashlib
import json
from collections.abc import Callable
from typing import Any, Literal

from pydantic import Field

from ..schemas import StrictModel
from .provider import ModelError, ModelProvider, ModelReply


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


class AgentStep(StrictModel):
    role: Literal["claim_extractor", "price_agent", "green_agent"]
    kind: Literal["model", "tool"]
    name: str = Field(min_length=1, max_length=100)
    status: Literal["started", "completed", "failed", "rejected"]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class AuditModelSession:
    def __init__(self, provider: ModelProvider, record: Callable[[AgentStep], None], *,
                 max_requests=32, max_input_chars=240000, max_output_tokens=2000):
        self.provider = provider
        self.record = record
        self.remaining_requests = max_requests
        self.remaining_input_chars = max_input_chars
        self.max_output_tokens = max_output_tokens

    def generate(self, role, *, instructions: str, items: list[dict[str, Any]],
                 schema=None, tools=None) -> ModelReply:
        request = {"instructions": instructions, "input": items, "schema": schema, "tools": tools or []}
        size = len(json.dumps(request, ensure_ascii=False))
        if self.remaining_requests < 1 or size > self.remaining_input_chars:
            self.record(AgentStep(role=role, kind="model", name=self.provider.model,
                                  status="rejected", input_hash=digest(request)))
            raise ModelError("AUDIT_MODEL_BUDGET_EXHAUSTED")
        self.remaining_requests -= 1
        self.remaining_input_chars -= size
        step = AgentStep(role=role, kind="model", name=self.provider.model,
                         status="started", input_hash=digest(request))
        # A durable repository fences the lease here, before any paid model request.
        self.record(step)
        try:
            reply = self.provider.generate(instructions=instructions, items=items, schema=schema,
                                           tools=tools or [], max_output_tokens=self.max_output_tokens)
        except ModelError:
            self.record(step.model_copy(update={"status": "failed"}))
            raise
        self.record(step.model_copy(update={"status": "completed", "output_hash": digest({
                                           "text": reply.text, "output": reply.output,
                                           "calls": [call.model_dump() for call in reply.calls]}),
                                           "input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens}))
        return reply
