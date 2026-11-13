"""Native Gemini generateContent adapter, including signed function-call history."""

import json
import re
from copy import deepcopy
from uuid import uuid4

import httpx

from .provider import ModelError, ModelReply, ToolCall


class GeminiGenerateContentProvider:
    def __init__(self, api_key: str, model: str = "gemini-3.5-flash-lite", *, timeout: float = 30,
                 transport: httpx.BaseTransport | None = None) -> None:
        if re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model) is None:
            raise ValueError("Invalid Gemini model identifier")
        self._key = api_key
        self.model = model
        self._timeout = timeout
        self._transport = transport

    @staticmethod
    def _contents(items):
        contents, known_calls, pending = [], {}, set()
        for item in items:
            if item.get("type") == "gemini_content":
                if pending:
                    raise ModelError("GEMINI_MISSING_TOOL_RESULTS")
                content = deepcopy(item["content"])
                calls = [part["functionCall"] for part in content["parts"] if "functionCall" in part]
                ids = item["call_ids"]
                if content.get("role") != "model" or len(ids) != len(calls):
                    raise ModelError("GEMINI_INVALID_HISTORY")
                for identifier, call in zip(ids, calls, strict=True):
                    if identifier in known_calls:
                        raise ModelError("GEMINI_DUPLICATE_CALL_ID")
                    known_calls[identifier] = call
                    pending.add(identifier)
                # Keep parts/thoughtSignature unchanged; signatures are opaque provider state.
                contents.append(content)
            elif item.get("type") == "function_call_output":
                identifier = item["call_id"]
                if identifier not in pending:
                    raise ModelError("GEMINI_UNMATCHED_TOOL_RESULT")
                call = known_calls[identifier]
                result = json.loads(item["output"])
                if not isinstance(result, dict):
                    raise ModelError("GEMINI_INVALID_TOOL_RESULT")
                response = {"name": call["name"], "response": result}
                if call.get("id"):
                    response["id"] = call["id"]
                part = {"functionResponse": response}
                if contents and contents[-1]["role"] == "user" and all("functionResponse" in p for p in contents[-1]["parts"]):
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
                pending.remove(identifier)
            elif item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str):
                if pending:
                    raise ModelError("GEMINI_MISSING_TOOL_RESULTS")
                contents.append({"role": "user" if item["role"] == "user" else "model",
                                 "parts": [{"text": item["content"]}]})
            else:
                raise ModelError("GEMINI_INVALID_HISTORY")
        if pending:
            raise ModelError("GEMINI_MISSING_TOOL_RESULTS")
        return contents

    def generate(self, *, instructions, items, schema=None, tools=None, max_output_tokens=2000) -> ModelReply:
        try:
            payload = {"systemInstruction": {"parts": [{"text": instructions}]},
                       "contents": self._contents(items),
                       "generationConfig": {"maxOutputTokens": max_output_tokens, "candidateCount": 1}}
            if schema is not None:
                payload["generationConfig"].update(responseMimeType="application/json", responseJsonSchema=schema)
            if tools:
                payload["tools"] = [{"functionDeclarations": [
                    {"name": tool["name"], "description": tool["description"],
                     "parametersJsonSchema": tool["parameters"]} for tool in tools]}]
                payload["toolConfig"] = {"functionCallingConfig": {"mode": "VALIDATED",
                                          "allowedFunctionNames": [tool["name"] for tool in tools]}}
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            with httpx.Client(transport=self._transport, timeout=self._timeout,
                              follow_redirects=False, trust_env=False) as client:
                # Header authentication keeps credentials out of request URLs and access logs.
                with client.stream("POST", url, headers={"x-goog-api-key": self._key}, json=payload) as response:
                    if response.status_code != 200:
                        raise ModelError(f"PROVIDER_HTTP_{response.status_code}")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            raise ModelError("PROVIDER_RESPONSE_TOO_LARGE")
            data = json.loads(body)
            if data.get("promptFeedback", {}).get("blockReason"):
                raise ModelError("PROVIDER_REFUSED")
            candidates = data.get("candidates", [])
            if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
                raise ModelError("PROVIDER_INCOMPLETE_OR_REFUSED")
            content = candidates[0]["content"]
            parts = content["parts"]
            if content.get("role") != "model" or not isinstance(parts, list) or len(parts) > 100:
                raise ModelError("PROVIDER_INVALID_OUTPUT")
            texts, calls = [], []
            for part in parts:
                if "functionCall" in part:
                    call = part["functionCall"]
                    arguments = call.get("args", {})
                    if not isinstance(arguments, dict):
                        raise ModelError("PROVIDER_INVALID_ARGUMENTS")
                    calls.append(ToolCall(call_id=call.get("id") or f"gemini_{uuid4().hex}",
                        name=call["name"], arguments=json.dumps(arguments, ensure_ascii=False)))
                elif "text" in part:
                    if not part.get("thought", False):
                        if not isinstance(part["text"], str):
                            raise ModelError("PROVIDER_INVALID_TEXT")
                        texts.append(part["text"])
                elif not part.get("thoughtSignature"):
                    raise ModelError("PROVIDER_UNEXPECTED_OUTPUT")
            if not texts and not calls:
                raise ModelError("PROVIDER_EMPTY_OUTPUT")
            usage = data.get("usageMetadata") or {}
            return ModelReply(text="".join(texts), calls=calls,
                output=[{"type": "gemini_content", "content": deepcopy(content),
                         "call_ids": [call.call_id for call in calls]}],
                input_tokens=max(0, int(usage.get("promptTokenCount", 0))),
                output_tokens=max(0, int(usage.get("candidatesTokenCount", 0)))
                              + max(0, int(usage.get("thoughtsTokenCount", 0))))
        except ModelError:
            raise
        except Exception:
            raise ModelError("PROVIDER_UNAVAILABLE_OR_INVALID") from None
