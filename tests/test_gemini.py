"""Native Gemini wire format, signed tool continuation and app wiring."""

import json

import httpx
import pytest

from competitive_intelligence.app import create_app
from competitive_intelligence.config import Settings
from competitive_intelligence.llm.gemini import GeminiGenerateContentProvider
from competitive_intelligence.llm.provider import ModelError
from competitive_intelligence.worker import build_context

MODEL = "gemini-3.5-flash-lite"


def response(parts, *, finish="STOP"):
    return httpx.Response(200, json={"candidates": [{"finishReason": finish,
        "content": {"role": "model", "parts": parts}}], "usageMetadata": {
        "promptTokenCount": 10, "candidatesTokenCount": 3, "thoughtsTokenCount": 2}})


def test_native_structured_output_authentication_and_thought_filtering():
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
        assert request.headers["x-goog-api-key"] == "test-secret"
        assert "test-secret" not in str(request.url) and not request.url.query
        payload = json.loads(request.content)
        assert payload["systemInstruction"]["parts"] == [{"text": "Extract"}]
        assert payload["contents"] == [{"role": "user", "parts": [{"text": "Public source"}]}]
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        assert payload["generationConfig"]["responseJsonSchema"] == {"type": "object"}
        assert payload["generationConfig"]["maxOutputTokens"] == 1000
        return response([{"text": "private reasoning", "thought": True},
                         {"text": '{"claims":[]}', "thoughtSignature": "opaque-state"}])

    provider = GeminiGenerateContentProvider("test-secret", transport=httpx.MockTransport(handler))
    reply = provider.generate(instructions="Extract", items=[{"role": "user", "content": "Public source"}],
                              schema={"type": "object"}, max_output_tokens=1000)
    assert reply.text == '{"claims":[]}' and reply.output_tokens == 5 and reply.input_tokens == 10
    assert "test-secret" not in repr(provider) and len(requests) == 1


@pytest.mark.parametrize("with_ids", [True, False])
def test_parallel_tool_results_preserve_signed_parts_and_match_names_and_ids(with_ids):
    parts = [{"functionCall": {"name": name, "args": {"supplier_id": "cez"},
              **({"id": f"id_{index}"} if with_ids else {})},
              "thoughtSignature": f"signed-{index}"}
             for index, name in enumerate(("get_fuel_mix", "get_guarantees_of_origin"))]
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == {"type": "object"}
        assert payload["toolConfig"]["functionCallingConfig"]["mode"] == "VALIDATED"
        if len(requests) == 1:
            return response(parts)
        assert payload["contents"][1] == {"role": "model", "parts": parts}
        results = payload["contents"][2]["parts"]
        assert len(results) == 2
        for index, item in enumerate(results):
            result = item["functionResponse"]
            assert result["name"] == parts[index]["functionCall"]["name"]
            assert result["response"] == {"unavailable": True}
            if with_ids:
                assert result["id"] == f"id_{index}"
            else:
                assert "id" not in result
        return response([{"text": "done"}])

    provider = GeminiGenerateContentProvider("test-key", transport=httpx.MockTransport(handler))
    tools = [{"name": part["functionCall"]["name"], "description": "Read evidence", "parameters": {"type": "object"}}
             for part in parts]
    items = [{"role": "user", "content": "Gather"}]
    first = provider.generate(instructions="Only approved tools", items=items, tools=tools)
    assert len(first.calls) == 2
    items.extend(first.output)
    items.extend({"type": "function_call_output", "call_id": call.call_id,
                  "output": json.dumps({"unavailable": True})} for call in first.calls)
    assert provider.generate(instructions="Only approved tools", items=items, tools=tools).text == "done"


@pytest.mark.parametrize("remote", [
    httpx.Response(302, headers={"location": "https://evil.example/"}),
    httpx.Response(403, text="test-secret private details"),
    httpx.Response(429, text="private quota details"),
    httpx.Response(200, text="not JSON"),
    httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
    response([{"text": "partial"}], finish="MAX_TOKENS"),
    response([{"text": "blocked"}], finish="SAFETY"),
    response([{"inlineData": {"mimeType": "image/png", "data": "bad"}}]),
])
def test_native_errors_are_redacted_and_do_not_redirect(remote):
    seen = []

    def handler(request):
        seen.append(request)
        return remote

    provider = GeminiGenerateContentProvider("test-secret", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as error:
        provider.generate(instructions="private prompt", items=[{"role": "user", "content": "x"}])
    assert "private" not in str(error.value) and "test-secret" not in str(error.value)
    assert len(seen) == 1


def test_unmatched_tool_result_is_rejected_before_any_request():
    provider = GeminiGenerateContentProvider("test-key", transport=httpx.MockTransport(
        lambda request: pytest.fail("Invalid history must not reach the provider")))
    with pytest.raises(ModelError, match="UNMATCHED"):
        provider.generate(instructions="Gather", items=[{"type": "function_call_output",
            "call_id": "unknown", "output": "{}"}])


@pytest.mark.parametrize("model", ["../other", "gemini?key=secret", "https://evil.example/", "models/test"])
def test_model_identifier_cannot_change_endpoint_or_add_credentials(model):
    with pytest.raises(ValueError):
        GeminiGenerateContentProvider("test-key", model)


def test_app_and_worker_use_exact_requested_gemini_model_without_startup_network():
    settings = Settings(llm_provider="gemini", llm_model=MODEL, llm_api_key="test-key")
    context = build_context(settings=settings)
    assert isinstance(context.model_provider, GeminiGenerateContentProvider)
    assert context.model_provider.model == MODEL
    app = create_app({"database_url": None, "seed_demo_audit": False,
                      "llm_provider": "gemini", "llm_model": MODEL, "llm_api_key": "test-key"})
    assert app.test_client().get("/").status_code == 200
    assert isinstance(app.extensions["fact_or_watt"].ctx.model_provider, GeminiGenerateContentProvider)
