"""Three suppliers and both evidence agents, with synthetic reviewed inputs only."""

import json
from types import SimpleNamespace

from competitive_intelligence.llm.provider import ModelReply
from competitive_intelligence.schemas import AuditCreateRequest
from competitive_intelligence.services import InMemoryAuditRepository
from competitive_intelligence.services.reference_bundle import ReviewedReferenceBundle
from competitive_intelligence.worker import process_next

from .conftest import FakeWeb, make_context, page_html
from .test_live_agents import NOW, answer, calls, extraction, reference_data


def test_three_supplier_price_green_journey_has_evidence_traces_and_explicit_publication(registry):
    price_quote, green_quote = "Nejlevnější elektřina v ČR", "100 % zelená elektřina"
    supplier_ids = ["cez", "eon", "innogy"]
    pages = [registry.get(identifier).pages[0] for identifier in supplier_ids]
    ctx, _ = make_context(FakeWeb({page.url: page_html(price_quote + ". " + green_quote) for page in pages}))
    ctx.scout._now = lambda: NOW
    data = reference_data()
    provenance = {key: data["offers"][0][key] for key in (
        "source_url", "source_ref", "captured_at", "valid_as_of", "valid_from", "valid_to", "source_content_hash")}
    data["offers"].append({**provenance, "offer_id": "offer_innogy", "supplier_id": "innogy",
                           "product_id": "product_innogy", "monthly_fee": "100", "price_mwh": "3500"})
    data["fuel_mix"] = [{**provenance, "supplier_id": supplier, "product_id": "product_" + supplier,
                          "renewable_share": "100", "product_specific": True} for supplier in supplier_ids]
    data["guarantees"] = [{**provenance, "supplier_id": supplier, "product_id": "product_" + supplier}
                           for supplier in supplier_ids]
    data["product_bindings"] = []
    for supplier, page in zip(supplier_ids, pages):
        captured = ctx.scout.collect(supplier, page)
        for quotation in (price_quote, green_quote):
            data["product_bindings"].append({"supplier_id": supplier, "source_url": page.url,
                "canonical_hash": captured.source.canonical_hash, "quote": quotation,
                "product_id": "product_" + supplier, "valid_from": "2026-11-01", "valid_to": "2026-11-30"})
    ctx.references = ReviewedReferenceBundle.model_validate(data)
    ctx.llm_max_requests = 32
    ctx.llm_max_input_chars = 1000000

    class Model:
        model = "synthetic-release-test"

        def generate(self, *, instructions, items, **kwargs):
            if instructions.startswith("Extract literal"):
                return answer([extraction(), extraction(green_quote, "green_share", "green", {"share_pct": "100"})])
            request = json.loads(items[0]["content"])
            claim = request["claim"]
            count = sum(item.get("type") == "function_call_output" for item in items)
            common = {"supplier_id": claim["supplier_id"], "product_id": claim["product_id"],
                      "as_of_date": request["as_of_date"]}
            if claim["claim_type"] == "green":
                if count == 0:
                    return calls(("mix", "get_fuel_mix", common), ("go", "get_guarantees_of_origin", common))
            elif count == 0:
                return calls(("offers", "get_price_offers", {"supplier_ids": supplier_ids,
                    "profile_id": "d02d_2mwh", "as_of_date": request["as_of_date"]}))
            elif count == 1:
                return calls(*[("cost-" + supplier, "calculate_annual_cost", {"offer_id": "offer_" + supplier,
                    "profile_id": "d02d_2mwh", "component": "annual_trade_cost"}) for supplier in supplier_ids])
            return ModelReply(text="done")

    ctx.model_provider = Model()
    repo = InMemoryAuditRepository()
    audit_id = repo.create_audit(AuditCreateRequest(supplier_ids=supplier_ids, profile_ids=["d02d_2mwh"], mode="live"))
    assert process_next(repo, ctx) == audit_id
    result = repo.get_result(audit_id)
    assert result is not None and len(result.claims) == 6
    assert all(claim.product_binding_ref and not claim.human_reviewed for claim in result.claims)
    by_claim = {claim.id: claim for claim in result.claims}
    for verdict in result.verdicts:
        current = by_claim[verdict.claim_id]
        expected = "supported" if current.claim_type == "green" else "unverifiable" if current.supplier_id == "eon" else "contradicted"
        assert verdict.verdict == expected
    assert all(item.facts for item in result.evidence)
    assert {step.role for step in repo._agent_steps[audit_id]} == {"claim_extractor", "price_agent", "green_agent"}
    assert repo.get_latest_published() is None
    public = repo.publish(audit_id, approved_by="synthetic-test-operator")
    assert public.published_at and repo.get_latest_published().audit_id == audit_id
    assert "synthetic-release-test" not in public.model_dump_json()


def test_worker_sigterm_finishes_delivery_then_stops(monkeypatch):
    import competitive_intelligence.worker as module

    disposed, processed, handlers = [], [], {}
    engine = SimpleNamespace(dispose=lambda: disposed.append(True))
    repo = SimpleNamespace(sessions=SimpleNamespace(kw={"bind": engine}))
    monkeypatch.setenv("DATABASE_URL", "configured-test-only")
    monkeypatch.setattr(module, "build_repository", lambda _: repo)
    monkeypatch.setattr(module, "check_schema", lambda _: None)
    monkeypatch.setattr(module, "build_context", lambda **_: object())
    prior = object()

    def signal_handler(number, handler):
        handlers[number] = handler
        return prior

    def process(_repo, _context):
        processed.append(True)
        handlers[module.signal.SIGTERM]()
        return None

    monkeypatch.setattr(module.signal, "signal", signal_handler)
    monkeypatch.setattr(module, "process_next", process)
    assert module.main(["run", "--poll-seconds", "0.01"]) == 0
    assert processed == [True] and disposed == [True]
    assert all(handler is prior for handler in handlers.values())
