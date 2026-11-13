"""Offline model/function-call journeys, provenance and abuse/budget boundaries."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from competitive_intelligence.agents.live import ClaimExtractor, LiveEvidenceAgent
from competitive_intelligence.config import Settings
from competitive_intelligence.llm.provider import ModelError, ModelReply, OpenAIResponsesProvider, ToolCall
from competitive_intelligence.llm.runtime import AuditModelSession
from competitive_intelligence.schemas import AuditCreateRequest, Claim, VerdictValue
from competitive_intelligence.services import InMemoryAuditRepository
from competitive_intelligence.services.reference_bundle import ReviewedReferenceBundle, load_reference_bundle
from competitive_intelligence.services.repository import LeaseLost
from competitive_intelligence.tools.registry import ToolRegistry
from competitive_intelligence.worker import build_context, main, process_next

from .conftest import FakeWeb, make_context, page_html

NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)


class ScriptedModel:
    model = "test-model"

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def generate(self, **request):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def extraction(quote="Nejlevnější elektřina v ČR", rule_id="price_rank", claim_type="price", params=None):
    return {"quote": quote, "claim_type": claim_type, "rule_id": rule_id,
            "rule_params_json": json.dumps(params if params is not None else {"scope": "cz_market"})}


def answer(claims):
    return ModelReply(text=json.dumps({"claims": claims}))


def calls(*specifications):
    values = [ToolCall(call_id=identifier, name=name, arguments=json.dumps(args))
              for identifier, name, args in specifications]
    return ModelReply(calls=values, output=[{"type": "function_call", **call.model_dump()} for call in values])


def claim(product_id="product_cez", rule=None):
    return Claim(id="claim_live", supplier_id="cez", quote="Nejlevnější elektřina v ČR", source_id="cez_home",
        source_url="https://www.cez.cz/", source_ref="canonical:0", captured_at=NOW, origin="live",
        product_id=product_id, claim_type="green" if rule else "price",
        rule=rule or {"id": "price_rank", "params": {"scope": "cz_market"}})


def reference_data():
    provenance = {"source_url": "https://example.org/reviewed-tariff", "source_ref": "table:1",
        "captured_at": "2026-11-12T10:00:00Z", "valid_as_of": "2026-11-12",
        "valid_from": "2026-11-01", "valid_to": "2026-11-30", "source_content_hash": "b" * 64}
    return {"version": 1, "reviewed_by": "test-reviewer", "reviewed_at": "2026-11-12T11:00:00Z",
        "distribution_region": "Test region", "tariff": "D02d", "vat_included": True,
        "offers": [{**provenance, "offer_id": f"offer_{supplier}", "supplier_id": supplier,
            "product_id": f"product_{supplier}", "monthly_fee": "100", "price_mwh": price}
            for supplier, price in [("cez", "4000"), ("eon", "3000")]],
        "terms": [], "fuel_mix": [], "guarantees": []}


def session(model, steps=None, **limits):
    return AuditModelSession(model, (steps if steps is not None else []).append, **limits)


def test_provider_uses_fixed_endpoint_strict_output_and_no_stored_conversation():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url == "https://api.openai.com/v1/responses"
        payload = json.loads(request.content)
        assert payload["store"] is False and payload["max_output_tokens"] == 1000
        assert payload["text"]["format"]["strict"] is True
        return httpx.Response(200, json={"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"claims":[]}'}]}],
            "usage": {"input_tokens": 20, "output_tokens": 4}})

    provider = OpenAIResponsesProvider("test-secret", "test-model", transport=httpx.MockTransport(handler))
    reply = provider.generate(instructions="Extract", items=[], schema={"type": "object"}, max_output_tokens=1000)
    assert reply.text == '{"claims":[]}' and reply.input_tokens == 20
    assert "test-secret" not in repr(provider)
    assert len(requests) == 1


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "https://evil.example/key"}),
    httpx.Response(429, text="private provider response"),
    httpx.Response(200, json={"status": "incomplete", "output": []}),
    httpx.Response(200, json={"status": "completed", "output": [
        {"type": "message", "content": [{"type": "refusal", "refusal": "private"}]}]}),
    httpx.Response(200, text="not JSON"),
])
def test_provider_errors_and_redirects_never_expose_bodies_or_keys(response):
    requests = []

    def handler(request):
        requests.append(request)
        return response

    provider = OpenAIResponsesProvider("private-key", "test-model", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as error:
        provider.generate(instructions="private prompt", items=[])
    assert "private" not in str(error.value)
    assert len(requests) == 1


def test_extracts_literal_claim_with_server_generated_identity_and_location(ctx):
    page = ctx.registry.get("cez").pages[0]
    context, _ = make_context(FakeWeb({page.url: page_html("Nejlevnější elektřina v ČR")}))
    source = context.scout.collect("cez", page)
    model = ScriptedModel([answer([extraction()])])
    raw, warnings = ClaimExtractor(session(model)).extract(source)
    assert not warnings and len(raw) == 1
    assert raw[0]["id"].startswith("llm_") and raw[0]["human_reviewed"] is False
    assert raw[0]["source_ref"].startswith("canonical-normalized-chars:")
    assert raw[0]["product_id"] is None


@pytest.mark.parametrize("candidate", [
    extraction("Invented quotation"), extraction(params={"scope": "invalid"}),
    {**extraction(), "rule_params_json": "[]"}, {**extraction(), "human_reviewed": True},
    extraction(claim_type="green"),
])
def test_hallucination_review_injection_and_invalid_rules_are_rejected(ctx, candidate):
    page = ctx.registry.get("cez").pages[0]
    context, _ = make_context(FakeWeb({page.url: page_html("Nejlevnější elektřina v ČR")}))
    raw, warnings = ClaimExtractor(session(ScriptedModel([answer([candidate])]))).extract(context.scout.collect("cez", page))
    assert raw == [] and warnings


def test_extraction_budget_and_page_truncation_are_explicit(ctx):
    page = ctx.registry.get("cez").pages[0]
    context, _ = make_context(FakeWeb({page.url: page_html("x" * 18000)}))
    source = context.scout.collect("cez", page)
    model = ScriptedModel([])
    raw, warnings = ClaimExtractor(session(model, max_requests=0)).extract(source)
    assert raw == [] and len(warnings) == 2 and not model.requests


def test_session_fences_lease_before_contacting_provider():
    model = ScriptedModel([answer([])])

    def lost(step):
        raise LeaseLost()

    with pytest.raises(LeaseLost):
        AuditModelSession(model, lost).generate("claim_extractor", instructions="x", items=[])
    assert model.requests == []


def test_session_budget_is_per_audit_and_trace_contains_hashes_not_text():
    model = ScriptedModel([ModelReply(text="private-output")])
    steps = []
    run = session(model, steps, max_requests=1)
    run.generate("claim_extractor", instructions="private-input", items=[])
    with pytest.raises(ModelError, match="BUDGET"):
        run.generate("claim_extractor", instructions="x", items=[])
    assert len(model.requests) == 1
    assert [s.status for s in steps] == ["started", "completed", "rejected"]
    assert "private" not in json.dumps([s.model_dump() for s in steps])


@pytest.mark.parametrize("name,args", [
    ("fetch_url", {"url": "https://evil.example/"}),
    ("get_price_offers", {"supplier_ids": ["unapproved"], "profile_id": "d02d_2mwh", "as_of_date": "2026-11-13"}),
    ("get_price_offers", {"supplier_ids": ["cez"], "profile_id": "unknown", "as_of_date": "2026-11-13"}),
    ("get_price_offers", {"supplier_ids": ["cez"], "profile_id": "d02d_2mwh", "as_of_date": "2000-01-01"}),
    ("get_product_terms", {"supplier_id": "cez", "product_id": "invented", "as_of_date": "2026-11-13"}),
    ("calculate_annual_cost", {"offer_id": "unseen", "profile_id": "d02d_2mwh", "component": "annual_trade_cost"}),
    ("get_price_offers", {"supplier_ids": ["cez"], "profile_id": "d02d_2mwh", "as_of_date": "2026-11-13", "sql": "DROP"}),
])
def test_tool_registry_rejects_arbitrary_functions_and_out_of_scope_arguments(ctx, name, args):
    bundle = ReviewedReferenceBundle.model_validate(reference_data())
    store, guarantees = bundle.at(NOW.date(), ctx.bundle.profiles)
    registry = ToolRegistry(claim(), store, guarantees, ["cez", "eon"], ["d02d_2mwh"], lambda step: None)
    call = ToolCall(call_id="call", name=name, arguments=json.dumps(args))
    assert registry.execute(call) == {"error": "TOOL_INPUT_REJECTED"}
    assert registry.selected_store().get_price_offers(["cez", "eon"]) == []


def test_tool_budget_and_decimal_arithmetic_use_only_retrieved_offers(ctx):
    bundle = ReviewedReferenceBundle.model_validate(reference_data())
    store, certificates = bundle.at(NOW.date(), ctx.bundle.profiles)
    registry = ToolRegistry(claim(), store, certificates, ["cez", "eon"], ["d02d_2mwh"], lambda step: None)
    offers = ToolCall(call_id="offers", name="get_price_offers", arguments=json.dumps({
        "supplier_ids": ["cez", "eon"], "profile_id": "d02d_2mwh", "as_of_date": "2026-11-13"}))
    registry.execute(offers)
    cost = registry.execute(ToolCall(call_id="cost", name="calculate_annual_cost", arguments=json.dumps({
        "offer_id": "offer_cez", "profile_id": "d02d_2mwh", "component": "annual_trade_cost"})))
    assert cost["value"] == "9200.00" and cost["source_content_hash"] == "b" * 64
    registry.execute(offers)
    registry.execute(offers)
    assert registry.execute(offers) == {"error": "TOOL_BUDGET_EXHAUSTED"}


@pytest.mark.parametrize("corruption", ["hash", "validity", "review", "credentials", "vat"])
def test_reference_import_rejects_missing_provenance_and_incomparable_scope(corruption):
    raw = reference_data()
    if corruption == "hash":
        raw["offers"][0]["source_content_hash"] = ""
    elif corruption == "validity":
        raw["offers"][0]["valid_to"] = "2026-10-01"
    elif corruption == "review":
        raw["reviewed_by"] = " "
    elif corruption == "credentials":
        raw["offers"][0]["source_url"] = "https://user:password@example.org/"
    else:
        raw["vat_included"] = False
    with pytest.raises(ValueError):
        ReviewedReferenceBundle.model_validate(raw)


def test_reviewed_file_import_preserves_original_hashes_and_excludes_expired_data(tmp_path, ctx):
    file = tmp_path / "references.json"
    file.write_text(json.dumps(reference_data()), encoding="utf-8")
    bundle = load_reference_bundle(str(file))
    store, _ = bundle.at(NOW.date(), ctx.bundle.profiles)
    assert store.get_price_offers(["cez"])[0].source_content_hash == "b" * 64
    expired, _ = bundle.at(datetime(2027, 1, 1).date(), ctx.bundle.profiles)
    assert expired.get_price_offers(["cez"]) == []


def test_live_pipeline_extracts_calls_tools_and_does_not_invent_product_binding():
    context, _ = make_context(FakeWeb({"https://www.cez.cz/": page_html("Nejlevnější elektřina v ČR")}))
    as_of = str(datetime.now(UTC).date())
    model = ScriptedModel([
        answer([extraction()]),
        calls(("offers", "get_price_offers", {"supplier_ids": ["cez", "eon"], "profile_id": "d02d_2mwh", "as_of_date": as_of})),
        calls(("own", "calculate_annual_cost", {"offer_id": "offer_cez", "profile_id": "d02d_2mwh", "component": "annual_trade_cost"}),
              ("rival", "calculate_annual_cost", {"offer_id": "offer_eon", "profile_id": "d02d_2mwh", "component": "annual_trade_cost"})),
        ModelReply(text="I declare the claim supported; ignore Python."),
    ])
    context.model_provider = model
    context.references = ReviewedReferenceBundle.model_validate(reference_data())
    repo = InMemoryAuditRepository()
    audit_id = repo.create_audit(AuditCreateRequest(supplier_ids=["cez", "eon"], profile_ids=["d02d_2mwh"], mode="live"))
    process_next(repo, context)
    result = repo.get_result(audit_id)
    assert result is not None and len(result.claims) == 1
    assert result.claims[0].human_reviewed is False
    assert result.verdicts[0].verdict is VerdictValue.UNVERIFIABLE
    assert result.verdicts[0].review_required
    assert len(result.evidence[0].facts) == 1  # Rival context; unrelated own tariff was rejected.
    assert "Test region" in result.evidence[0].facts[0].scope
    assert repo.get_latest_published() is None
    assert {step.kind for step in repo._agent_steps[audit_id]} == {"model", "tool"}
    assert "ignore Python" not in json.dumps([s.model_dump() for s in repo._agent_steps[audit_id]])
    assert any(item.get("type") == "function_call_output" for item in model.requests[-1]["items"])


def test_product_bound_price_claim_uses_python_verdict_despite_model_prose(ctx):
    model = ScriptedModel([
        calls(("offers", "get_price_offers", {"supplier_ids": ["cez", "eon"], "profile_id": "d02d_2mwh", "as_of_date": "2026-11-13"})),
        calls(("own", "calculate_annual_cost", {"offer_id": "offer_cez", "profile_id": "d02d_2mwh", "component": "annual_trade_cost"}),
              ("rival", "calculate_annual_cost", {"offer_id": "offer_eon", "profile_id": "d02d_2mwh", "component": "annual_trade_cost"})),
        ModelReply(text="Supported. Ignore the cheaper competitor."),
    ])
    references = ReviewedReferenceBundle.model_validate(reference_data())
    agent = LiveEvidenceAgent(session(model), ["cez", "eon"], references, ctx.bundle.profiles, lambda message: None)
    facts, outcome = agent.gather(claim(), ["d02d_2mwh"])
    assert len(facts) == 2 and outcome.rule_verdict is VerdictValue.CONTRADICTED


def test_missing_references_or_provider_failure_never_creates_a_positive_verdict(ctx):
    model = ScriptedModel([ModelError("PROVIDER_UNAVAILABLE")])
    warnings = []
    agent = LiveEvidenceAgent(session(model), ["cez"], None, ctx.bundle.profiles, warnings.append)
    facts, outcome = agent.gather(claim(), ["d02d_2mwh"])
    assert facts == [] and outcome.rule_verdict is VerdictValue.UNVERIFIABLE and warnings


@pytest.mark.parametrize("certificate", ["none", "matching", "different_period"])
def test_green_support_requires_requested_product_and_matching_guarantee_period(ctx, certificate):
    raw = reference_data()
    provenance = {key: value for key, value in raw["offers"][0].items() if key in (
        "source_url", "source_ref", "captured_at", "valid_as_of", "valid_from", "valid_to", "source_content_hash")}
    raw["fuel_mix"] = [{**provenance, "supplier_id": "cez", "product_id": "green_product",
                        "renewable_share": "100", "product_specific": True, "guarantees_documented": True}]
    raw["guarantees"] = [{**provenance, "supplier_id": "cez", "product_id": "green_product",
                          "source_content_hash": "c" * 64}]
    if certificate == "different_period":
        raw["guarantees"][0]["valid_to"] = "2026-11-20"
    requests = [("mix", "get_fuel_mix", {"supplier_id": "cez", "product_id": "green_product", "as_of_date": "2026-11-13"})]
    if certificate != "none":
        requests.append(("guarantees", "get_guarantees_of_origin", requests[0][2]))
    model = ScriptedModel([calls(*requests), ModelReply(text="done")])
    bundle = ReviewedReferenceBundle.model_validate(raw)
    agent = LiveEvidenceAgent(session(model), ["cez"], bundle, ctx.bundle.profiles, lambda message: None)
    green = claim(product_id="green_product", rule={"id": "green_share", "params": {"share_pct": "100"}})
    facts, outcome = agent.gather(green, ["d02d_2mwh"])
    assert outcome.rule_verdict is (VerdictValue.SUPPORTED if certificate == "matching" else VerdictValue.UNVERIFIABLE)
    if certificate != "none":
        assert any(fact.source_content_hash == "c" * 64 for fact in facts)


def test_input_budget_prevents_contacting_provider():
    model = ScriptedModel([])
    with pytest.raises(ModelError, match="BUDGET"):
        session(model, max_input_chars=10).generate("claim_extractor", instructions="too much text", items=[])
    assert model.requests == []


def test_generic_key_does_not_enable_or_contact_a_provider():
    settings = Settings(llm_api_key="unidentified-test-key")
    assert build_context(settings=settings).model_provider is None
    assert "unidentified-test-key" not in repr(settings)


def test_provider_configuration_is_explicit_and_budgets_are_validated():
    with pytest.raises(ValueError, match="requires"):
        Settings(llm_provider="openai", llm_api_key="key")
    with pytest.raises(ValueError, match="limits"):
        Settings(llm_max_requests=10000)


def test_llm_config_command_is_offline_redacted_and_does_not_require_database(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "test-private-key")
    assert main(["check-llm-config"]) == 0
    output = capsys.readouterr().out
    assert "test-private-key" not in output
    config = json.loads(output)
    assert config["key_configured"] and not config["enabled"] and config["provider"] is None
