"""Testy k opakované revizi (17. 11. 2026): rozsah tvrzení, společné datum srovnání,
časová rezerva a nulový výchozí stav. Každý test popisuje případ, který revize reprodukovala.
Běží bez sítě."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from competitive_intelligence import evidence as ev
from competitive_intelligence import insights, rules, workflow
from competitive_intelligence.app import create_app
from competitive_intelligence.connectors import http

from test_rules import claim, make_ctx, months, offer, ote_table, supplier


def scoped(**scope):
    return {"quote": "", "params": {"scope": scope}}


# ---------- QUAL-001: každé pravidlo respektuje rozsah tvrzení ----------

def test_gas_growth_claim_is_judged_on_gas_not_electricity():
    # revize: elektřina 100 → 200, plyn 400 → 100; tvrzení o růstu plynu nesmí být „podloženo“ elektřinou
    s = supplier("a")
    ctx = make_ctx([s], ele=ote_table({"a": months(100, 200)}), gas=ote_table({"a": months(400, 100)}))
    out = rules.customer_growth(scoped(metric="opm", commodities=["plyn"]), s, ctx)
    assert out["verdict"] == rules.CONTRADICTED
    assert out["evidence"][0]["source"] == "ote_gas"


def test_gas_growth_claim_abstains_without_gas_data():
    s = supplier("a")
    ctx = make_ctx([s], ele=ote_table({"a": months(100, 200)}), gas=None)
    out = rules.customer_growth(scoped(metric="opm", commodities=["plyn"]), s, ctx)
    assert out["verdict"] == rules.UNVERIFIABLE


def test_growth_claim_for_other_market_abstains():
    s = supplier("a")
    ctx = make_ctx([s], ele=ote_table({"a": months(100, 200)}))
    out = rules.customer_growth(scoped(metric="opm", market="multi"), s, ctx)
    assert out["verdict"] == rules.UNVERIFIABLE and "jiných trhů" in out["finding"]


def test_size_claim_respects_market_and_period():
    s = supplier("velky")
    ctx = make_ctx([s])  # tabulka OTE za 2026-01, „velky“ je první
    assert rules.market_size(scoped(metric="opm"), s, ctx)["verdict"] == rules.SUPPORTED
    other_market = rules.market_size(scoped(metric="opm", market="multi"), s, ctx)
    assert other_market["verdict"] == rules.UNVERIFIABLE
    other_period = rules.market_size(scoped(metric="opm", period="2025-12"), s, ctx)
    assert other_period["verdict"] == rules.UNVERIFIABLE and "2025-12" in other_period["finding"]


def test_gas_only_size_claim_ignores_electricity_rank():
    # první v elektřině, druhý v plynu: tvrzení jen o plynu je v rozporu, ne „částečně“
    s = supplier("a")
    ctx = make_ctx(
        [s],
        ele=ote_table({"a": months(1000), "b": months(500)}),
        gas=ote_table({"a": months(100), "b": months(900)}),
    )
    out = rules.market_size(scoped(metric="opm", commodities=["plyn"]), s, ctx)
    assert out["verdict"] == rules.CONTRADICTED


# ---------- QUAL-004: dvě nabídky se srovnávají k jednomu datu ----------

def _pair(rival_offer):
    mine = supplier("a", [offer("a1", 3850.0, fee=150.0)])
    rival = supplier("b", [rival_offer])
    return mine, rival, make_ctx([mine, rival])


def test_cheaper_offer_valid_today_contradicts_best_price():
    # kontrola, že níže testovaný případ by bez rozdílu dat opravdu vedl k rozporu
    mine, rival, ctx = _pair(offer("b1", 3000.0, fee=100.0))
    assert rules.price_rank(claim(kind="superlative"), mine, ctx)["verdict"] == rules.CONTRADICTED


def test_expired_cheaper_offer_does_not_contradict_current_claim():
    # revize: nabídka platná v listopadu 2026 proti levnější nabídce, která skončila v prosinci 2025
    expired = offer("b1", 3000.0, fee=100.0, confirmed_live=False, captured_at="2025-12-01",
                    valid_until="2025-12-31")
    mine, rival, ctx = _pair(expired)
    assert rules.price_rank(claim(kind="superlative"), mine, ctx)["verdict"] != rules.CONTRADICTED
    for s in (mine, rival):  # tabulka srovnání vypisuje i způsob potvrzení nabídky
        s["offers"][0]["confirmation"] = "test"
    rows, excluded = workflow._comparison([mine, rival], ctx)
    assert all(r["supplier_id"] != "b" for group in rows for r in group["rows"])
    assert any("K datu srovnání" in x["reason"] for x in excluded)


def test_old_unconfirmed_offer_is_not_used_in_pairs():
    old = offer("b1", 3000.0, fee=100.0, confirmed_live=False, captured_at="2026-01-10")
    mine, rival, ctx = _pair(old)
    assert rules.price_rank(claim(kind="superlative"), mine, ctx)["verdict"] != rules.CONTRADICTED
    assert not ev.offer_current(old, ctx).ok


def test_own_offer_outside_comparison_date_is_explained():
    mine = supplier("a", [offer("a1", 3850.0, fee=150.0, confirmed_live=False, captured_at="2025-06-01")])
    rival = supplier("b", [offer("b1", 3000.0, fee=100.0)])
    out = rules.price_rank(claim(kind="superlative"), mine, make_ctx([mine, rival]))
    assert out["verdict"] == rules.UNVERIFIABLE and "k datu srovnání" in out["finding"]


# ---------- QUAL-005: nulový výchozí stav ----------

ZERO_START = {"velky": months(1000, 1010), "novy": months(0, 20), "stredni": months(500, 490)}


def test_growth_from_zero_keeps_absolute_change_without_percent():
    s = supplier("novy")
    out = rules.customer_growth(scoped(metric="opm"), s, make_ctx([s], ele=ote_table(ZERO_START)))
    assert out["verdict"] == rules.SUPPORTED
    assert "+20" in out["evidence"][0]["value"] and "procento nelze určit" in out["evidence"][0]["value"]


def test_market_overview_sorts_zero_start_without_crash():
    suppliers = [supplier(n) for n in ZERO_START]
    ctx = make_ctx(suppliers, ele=ote_table(ZERO_START))
    overview = insights.market_overview(suppliers, ctx, [])
    order = [r["id"] for r in overview["rows"]]
    assert order[-1] == "novy"  # známá procenta první, růst z nuly až za nimi
    assert insights.pct_text(None) == "procento nelze určit"


# ---------- OPS-002: tvrdý strop stažení a rezerva na odpověď ----------

class _MultiPhaseHandler(BaseHTTPRequestHandler):
    """Každá fáze se vejde do svého limitu, jejich součet ne (případ z revize)."""

    def do_GET(self):  # noqa: N802 - jméno dané knihovnou
        time.sleep(1.2)
        self.send_response(200)
        self.end_headers()
        for _ in range(3):
            time.sleep(1.2)
            try:
                self.wfile.write(b"x" * 10)
                self.wfile.flush()
            except OSError:
                return

    def log_message(self, *args):
        pass


@pytest.fixture
def multi_phase_server(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MultiPhaseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(http, "_check_host", lambda url: None)  # povolit lokální server
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


def test_fetch_is_hard_capped_across_phases(multi_phase_server):
    token = http.DEADLINE.set(time.monotonic() + 1.5)
    try:
        started = time.monotonic()
        with pytest.raises(http.DeadlineExceeded):
            http.fetch_live(multi_phase_server)
        assert time.monotonic() - started < 1.8
    finally:
        http.DEADLINE.reset(token)


def test_network_stops_before_the_audit_budget(monkeypatch):
    monkeypatch.setenv("AUDIT_DEADLINE_S", "10")
    seen = []

    def record(url):
        seen.append(http.remaining())
        raise http.FetchError("síť je v testech vypnutá")

    monkeypatch.setattr(http, "fetch_live", record)
    report = workflow.run(["cez"], live=True, use_llm=False)
    assert seen and max(seen) <= 10 - workflow.network_reserve(10) + 0.01
    assert report["deadline_s"] == 10


# ---------- verze nasazení ----------

def test_version_endpoint_reports_build(monkeypatch):
    monkeypatch.setenv("APP_BUILD", "9dbbb5d055355fd982f9")
    client = create_app().test_client()
    assert client.get("/api/v1/version").get_json() == {"build": "9dbbb5d05535"}
    assert "Verze 9dbbb5d05535" in client.get("/").get_data(as_text=True)
