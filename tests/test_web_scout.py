"""Web Scout: bounded fetch, canonical hashing and the live / baseline / snapshot branches."""

from datetime import UTC, datetime

import httpx
import pytest

from competitive_intelligence.connectors import SupplierWebClient
from competitive_intelligence.parsers import PARSER_VERSION, canonicalize, sha256_bytes, sha256_text
from competitive_intelligence.schemas import AuditCreateRequest, AuditMode, AuditStatus, Origin
from competitive_intelligence.services import InMemoryAuditRepository
from competitive_intelligence.services.stores import Baseline, FileSnapshotStore, Snapshot
from competitive_intelligence.services.web_scout import ScoutedSource, ScoutError
from competitive_intelligence.worker import process_next

from .conftest import FakeWeb, make_context, page_html

CEZ_HOME = "https://www.cez.cz/"
CEZ_PAGE = "https://www.cez.cz/cs/energie/elektrina"
OLD = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def cez_pages(registry):
    return registry.get("cez").pages


def snapshot_of(page, html: str, captured_at=OLD) -> Snapshot:
    text = canonicalize(html)
    return Snapshot(
        source_id=page.source_id,
        source_url=page.url,
        captured_at=captured_at,
        parser_version=PARSER_VERSION,
        content_sha256=sha256_bytes(html.encode()),
        canonical_hash=sha256_text(text),
        text=text,
    )


def run_live(ctx, supplier_ids=("cez",)):
    repo = InMemoryAuditRepository()
    audit_id = repo.create_audit(
        AuditCreateRequest(supplier_ids=list(supplier_ids), profile_ids=["d02d_2mwh"], mode=AuditMode.LIVE)
    )
    process_next(repo, ctx)
    return repo, audit_id, repo.get_result(audit_id)


# -- parser ---------------------------------------------------------------


def test_canonical_text_drops_scripts_navigation_and_cookie_layers():
    text = canonicalize(page_html("Máme jedny z nejnižších cen", extra="abc123"))
    assert "Máme jedny z nejnižších cen" in text
    for noise in ("token", "abc123", "Menu", "cookies"):
        assert noise not in text


def test_canonical_hash_ignores_rotating_tokens_but_sees_a_changed_claim():
    first = sha256_text(canonicalize(page_html("Nejlevnější elektřina", extra="session-1")))
    again = sha256_text(canonicalize(page_html("Nejlevnější elektřina", extra="session-2")))
    changed = sha256_text(canonicalize(page_html("Skoro nejlevnější elektřina", extra="session-1")))
    assert first == again
    assert first != changed


# -- client guards --------------------------------------------------------


def test_url_outside_the_registry_is_never_requested(registry):
    web = FakeWeb()
    client = SupplierWebClient(
        registry.allowed_urls, transport=httpx.MockTransport(web.handler), resolver=lambda h: True
    )
    result = client.fetch("https://www.cez.cz/some/other/page")
    assert not result.ok and web.requests == []
    assert not client.fetch("https://evil.example/").ok and web.requests == []


def test_robots_disallow_stops_the_fetch(registry):
    web = FakeWeb({CEZ_PAGE: page_html("x")}, robots={"www.cez.cz": "User-agent: *\nDisallow: /cs/energie/"})
    ctx, _ = make_context(web)
    outcome = ctx.scout.collect("cez", cez_pages(registry)[1])
    assert isinstance(outcome, ScoutError) and "robots" in outcome.reason
    assert web.page_requests() == []


def test_unreadable_robots_means_no_fetch(registry):
    web = FakeWeb({CEZ_HOME: page_html("x"), "https://www.cez.cz/robots.txt": 503})

    def handler(request):
        if request.url.path == "/robots.txt":
            web.requests.append(str(request.url))
            return httpx.Response(503)
        return web.handler(request)

    client = SupplierWebClient(
        registry.allowed_urls, transport=httpx.MockTransport(handler), resolver=lambda h: True
    )
    result = client.fetch(CEZ_HOME)
    assert not result.ok and "robots" in result.reason
    assert web.page_requests() == []


def test_redirect_to_a_foreign_host_is_refused(registry):
    redirect = httpx.Response(302, headers={"location": "https://tracker.example/landing"})
    web = FakeWeb({CEZ_HOME: redirect})
    client = SupplierWebClient(
        registry.allowed_urls, transport=httpx.MockTransport(web.handler), resolver=lambda h: True
    )
    result = client.fetch(CEZ_HOME)
    assert not result.ok and "schválených" in result.reason
    assert all("tracker.example" not in url for url in web.requests)


def test_host_resolving_to_a_private_address_is_refused(registry):
    web = FakeWeb({CEZ_HOME: page_html("x")})
    client = SupplierWebClient(
        registry.allowed_urls, transport=httpx.MockTransport(web.handler), resolver=lambda h: False
    )
    assert not client.fetch(CEZ_HOME).ok
    assert web.requests == []


def test_oversized_and_non_html_responses_are_refused(registry):
    big = httpx.Response(200, content=b"x" * 5000, headers={"content-type": "text/html"})
    pdf = httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    web = FakeWeb({CEZ_HOME: big, CEZ_PAGE: pdf})
    client = SupplierWebClient(
        registry.allowed_urls,
        transport=httpx.MockTransport(web.handler),
        resolver=lambda h: True,
        max_bytes=1000,
    )
    assert "velká" in client.fetch(CEZ_HOME).reason
    assert "typ obsahu" in client.fetch(CEZ_PAGE).reason


# -- three branches -------------------------------------------------------


def test_new_page_is_live_and_gets_a_snapshot(registry):
    page = cez_pages(registry)[0]
    ctx, snapshots = make_context(FakeWeb({page.url: page_html("Elektřina od ČEZ")}))
    outcome = ctx.scout.collect("cez", page)
    assert isinstance(outcome, ScoutedSource)
    assert outcome.source.origin is Origin.LIVE and outcome.source.fetch_was_live
    assert outcome.baseline_claims is None
    saved = snapshots.get(page.source_id)
    assert saved is not None and saved.canonical_hash == outcome.source.canonical_hash


def baseline_for(page, html: str, quote: str) -> Baseline:
    return Baseline(
        source_id=page.source_id,
        source_url=page.url,
        parser_version=PARSER_VERSION,
        canonical_hash=sha256_text(canonicalize(html)),
        approved_at=OLD,
        claims=[
            {
                "id": "claim_cez_01",
                "source_id": page.source_id,
                "quote": quote,
                "source_ref": "main > h1",
                "claim_type": "price",
                "rule": {"id": "price_rank", "params": {"scope": "cz_market"}},
            }
        ],
    )


def test_unchanged_page_reuses_the_approved_baseline(registry):
    page = cez_pages(registry)[0]
    html = page_html("Máme jedny z nejnižších cen na trhu")
    baseline = baseline_for(page, html, "Máme jedny z nejnižších cen na trhu")
    # Different rotating token, same meaningful content.
    web = FakeWeb({p.url: page_html("Máme jedny z nejnižších cen na trhu", extra="t2") for p in cez_pages(registry)})
    ctx, _ = make_context(web, baselines=[baseline])
    _repo, _audit_id, result = run_live(ctx)

    source = next(s for s in result.sources if s.source_id == page.source_id)
    assert source.origin is Origin.BASELINE and source.fetch_was_live
    assert source.baseline_approved_at == OLD
    assert [c.id for c in result.claims] == ["claim_cez_01"]
    claim = result.claims[0]
    assert claim.human_reviewed and claim.origin is Origin.BASELINE
    # No real reference data yet: the rule abstains instead of guessing.
    assert result.verdicts[0].verdict == "unverifiable"
    assert result.mode is AuditMode.LIVE


def test_changed_page_does_not_reuse_baseline_claims(registry):
    page = cez_pages(registry)[0]
    baseline = baseline_for(page, page_html("Stará věta"), "Stará věta")
    web = FakeWeb({p.url: page_html("Nová věta") for p in cez_pages(registry)})
    ctx, _ = make_context(web, baselines=[baseline])
    _repo, _audit_id, result = run_live(ctx)
    assert all(s.origin is Origin.LIVE for s in result.sources)
    assert result.claims == []
    assert any("Extrakce tvrzení" in w for w in result.warnings)


def test_baseline_claim_must_still_be_a_literal_quote(registry):
    page = cez_pages(registry)[0]
    html = page_html("Elektřina od ČEZ")
    baseline = baseline_for(page, html, "Věta, která na stránce není")
    ctx, _ = make_context(FakeWeb({p.url: html for p in cez_pages(registry)}), baselines=[baseline])
    _repo, _audit_id, result = run_live(ctx)
    assert result.claims == []
    assert any("claim_cez_01" in w and "citace" in w for w in result.warnings)


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout("slow"), 503, "<html><body><div id='app'></div></body></html>"],
    ids=["timeout", "http-503", "empty-js-shell"],
)
def test_failed_fetch_falls_back_to_the_dated_snapshot(registry, failure):
    page = cez_pages(registry)[0]
    snapshot = snapshot_of(page, page_html("Elektřina od ČEZ"))
    ctx, _ = make_context(FakeWeb({page.url: failure}), snapshots=[snapshot])
    outcome = ctx.scout.collect("cez", page)
    assert isinstance(outcome, ScoutedSource)
    source = outcome.source
    assert source.origin is Origin.SNAPSHOT and not source.fetch_was_live
    assert source.captured_at == OLD and source.checked_at > OLD  # real age is preserved
    assert source.fallback_reason
    assert outcome.text == snapshot.text


def test_failed_fetch_without_snapshot_is_a_source_error(registry):
    page = cez_pages(registry)[0]
    ctx, _ = make_context(FakeWeb({page.url: httpx.ConnectError("down")}))
    outcome = ctx.scout.collect("cez", page)
    assert isinstance(outcome, ScoutError) and outcome.source_id == page.source_id


def test_snapshot_from_another_parser_version_is_not_used(registry):
    page = cez_pages(registry)[0]
    stale = snapshot_of(page, page_html("x")).model_copy(update={"parser_version": "html-v0"})
    ctx, _ = make_context(FakeWeb({page.url: 500}), snapshots=[stale])
    assert isinstance(ctx.scout.collect("cez", page), ScoutError)


def test_snapshot_fallback_is_visible_in_the_audit(registry):
    home, product = cez_pages(registry)
    web = FakeWeb({home.url: page_html("Elektřina od ČEZ"), product.url: httpx.ReadTimeout("slow")})
    ctx, _ = make_context(web, snapshots=[snapshot_of(product, page_html("Starší obsah"))])
    repo, audit_id, result = run_live(ctx)
    origins = {s.source_id: s.origin for s in result.sources}
    assert origins == {home.source_id: Origin.LIVE, product.source_id: Origin.SNAPSHOT}
    status = repo.get_status(audit_id)
    assert any(e.origin is Origin.SNAPSHOT and e.level == "warning" for e in status.events)
    assert any("snapshot z 2026-11-09" in w for w in result.warnings)


def test_partial_source_failure_keeps_the_rest_and_reports_it(registry):
    home, product = cez_pages(registry)
    ctx, _ = make_context(FakeWeb({home.url: page_html("Elektřina od ČEZ"), product.url: 500}))
    repo, audit_id, result = run_live(ctx)
    assert repo.get_status(audit_id).status is AuditStatus.COMPLETED
    assert [s.source_id for s in result.sources] == [home.source_id]
    assert any(product.source_id in w and "nebyl posouzen" in w for w in result.warnings)


def test_audit_fails_when_no_source_is_available(registry):
    ctx, _ = make_context(FakeWeb({}))
    repo, audit_id, result = run_live(ctx)
    status = repo.get_status(audit_id)
    assert result is None
    assert status.status is AuditStatus.FAILED and status.error.code == "SOURCE_UNAVAILABLE"


def test_live_audit_fetches_only_requested_suppliers(registry, all_pages_ok):
    ctx, _ = make_context(all_pages_ok)
    run_live(ctx, supplier_ids=("eon",))
    assert all_pages_ok.page_requests()
    assert all("eon.cz" in url for url in all_pages_ok.page_requests())


# -- file store -----------------------------------------------------------


def test_file_snapshot_store_roundtrip_and_no_rewrite_when_unchanged(tmp_path, registry):
    page = cez_pages(registry)[0]
    store = FileSnapshotStore(tmp_path / "snapshots")
    first = snapshot_of(page, page_html("Elektřina od ČEZ"))
    assert store.get(page.source_id) is None
    assert store.save(first)
    assert store.get(page.source_id) == first
    # Same content captured later keeps the original file and its original date.
    later = first.model_copy(update={"captured_at": datetime(2026, 11, 13, tzinfo=UTC)})
    assert store.save(later)
    assert store.get(page.source_id).captured_at == OLD


def test_file_snapshot_store_reports_failure_on_unwritable_location(tmp_path, registry):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    store = FileSnapshotStore(blocker / "snapshots")
    assert store.save(snapshot_of(cez_pages(registry)[0], page_html("x"))) is False
