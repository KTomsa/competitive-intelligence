"""End-to-end fixture pipeline: offline, no network, no keys, no database."""

import socket

import pytest

from competitive_intelligence.schemas import AuditCreateRequest, AuditStatus, VerdictValue
from competitive_intelligence.services import InMemoryAuditRepository, load_demo_fixture
from competitive_intelligence.services.orchestrator import validate_claims
from competitive_intelligence.worker import process_next


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("fixture audit must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def finished(no_network, ctx):
    bundle = load_demo_fixture()
    repo = InMemoryAuditRepository()
    audit_id = repo.create_audit(
        AuditCreateRequest(supplier_ids=bundle.supplier_ids, profile_ids=list(bundle.profiles))
    )
    assert process_next(repo, ctx) == audit_id
    return repo, audit_id, repo.get_result(audit_id)


def test_fixture_audit_completes_offline(finished):
    repo, audit_id, result = finished
    status = repo.get_status(audit_id)
    assert status.status is AuditStatus.COMPLETED and status.progress_percent == 100
    assert [e.event_id for e in status.events] == list(range(1, len(status.events) + 1))
    assert result.mode == "fixture" and "FIKTIVNÍ" in result.scope_note


def test_expected_verdicts(finished):
    verdicts = {v.claim_id: v for v in finished[2].verdicts}
    assert verdicts["claim_demo_a_01"].verdict is VerdictValue.CONTRADICTED
    assert verdicts["claim_demo_a_02"].verdict is VerdictValue.SUPPORTED
    assert verdicts["claim_demo_a_03"].verdict is VerdictValue.SUPPORTED
    # Cheapest of three suppliers does not confirm "cheapest on the market".
    assert verdicts["claim_demo_b_01"].verdict is VerdictValue.UNVERIFIABLE
    # OPM evidence cannot confirm a customer count.
    assert verdicts["claim_demo_b_03"].verdict is VerdictValue.UNVERIFIABLE
    # National residual mix cannot decide a product claim.
    assert verdicts["claim_demo_c_02"].verdict is VerdictValue.UNVERIFIABLE


def test_judge_downgrade_is_recorded(finished):
    verdict = next(v for v in finished[2].verdicts if v.claim_id == "claim_demo_c_01")
    assert verdict.rule_verdict is VerdictValue.SUPPORTED
    assert verdict.verdict is VerdictValue.UNVERIFIABLE
    assert verdict.judge_changed and verdict.judge_reason and verdict.review_required


def test_regulatory_risk_is_separate_from_verdict(finished):
    verdict = next(v for v in finished[2].verdicts if v.claim_id == "claim_demo_b_02")
    assert verdict.verdict is VerdictValue.UNVERIFIABLE
    assert verdict.possible_regulatory_risk and verdict.risk_reason


def test_claim_without_literal_quote_is_dropped_with_warning(finished):
    result = finished[2]
    assert "claim_demo_a_04" not in {c.id for c in result.claims}
    assert any("claim_demo_a_04" in w for w in result.warnings)


def test_every_decisive_verdict_has_traceable_evidence(finished):
    result = finished[2]
    evidence = {e.claim_id: e for e in result.evidence}
    for verdict in result.verdicts:
        if verdict.rule_verdict in (VerdictValue.SUPPORTED, VerdictValue.CONTRADICTED):
            facts = evidence[verdict.claim_id].facts
            assert facts, verdict.claim_id
            assert set(verdict.evidence_ids) == {f.evidence_id for f in facts}
            for fact in facts:
                assert fact.source_url.startswith("https://") and fact.source_ref
                assert fact.valid_as_of and len(fact.source_content_hash) == 64


def test_provenance_survives_for_all_three_origins(finished):
    result = finished[2]
    origins = {s.supplier_id: s for s in result.sources}
    assert origins["supplier_demo_a"].origin == "live" and origins["supplier_demo_a"].fetch_was_live
    assert origins["supplier_demo_b"].origin == "baseline" and origins["supplier_demo_b"].baseline_approved_at
    snapshot = origins["supplier_demo_c"]
    assert snapshot.origin == "snapshot" and not snapshot.fetch_was_live and snapshot.fallback_reason
    for claim in result.claims:
        assert claim.origin == origins[claim.supplier_id].origin


def test_briefing_cites_claims_and_has_no_supplier_score(finished):
    result = finished[2]
    claim_ids = {c.id for c in result.claims}
    assert result.briefing.items and all(i.claim_id in claim_ids for i in result.briefing.items)
    assert sum(sum(row.values()) for row in result.briefing.matrix.values()) == len(result.verdicts)
    assert "score" not in result.model_dump_json()


def test_audit_limited_to_requested_suppliers(no_network, ctx):
    bundle = load_demo_fixture()
    repo = InMemoryAuditRepository()
    audit_id = repo.create_audit(
        AuditCreateRequest(supplier_ids=["supplier_demo_b"], profile_ids=["d02d_2mwh"])
    )
    process_next(repo, ctx)
    result = repo.get_result(audit_id)
    assert {c.supplier_id for c in result.claims} == {"supplier_demo_b"}
    assert {s.supplier_id for s in result.sources} == {"supplier_demo_b"}


def test_extractor_coerces_unknown_rule_and_drops_bad_params():
    bundle = load_demo_fixture()
    source = bundle.sources[0]
    template = {
        "source_id": source.source_id,
        "quote": "Nejlevnější elektřina v ČR",
        "source_ref": "h1",
        "claim_type": "price",
    }
    raw = [
        {**template, "id": "unknown_rule", "rule": {"id": "made_up", "params": {}}},
        {**template, "id": "bad_params", "rule": {"id": "price_rank", "params": {"scope": "moon"}}},
    ]
    claims, dropped = validate_claims(raw, [source], bundle.canonical_texts)
    assert [(c.id, c.rule.id) for c in claims] == [("unknown_rule", "other")]
    assert len(dropped) == 1 and "bad_params" in dropped[0]


def test_job_is_claimed_only_once(no_network, ctx):
    bundle = load_demo_fixture()
    repo = InMemoryAuditRepository()
    repo.create_audit(AuditCreateRequest(supplier_ids=["supplier_demo_a"], profile_ids=["d02d_2mwh"]))
    assert process_next(repo, ctx) is not None
    assert process_next(repo, ctx) is None
