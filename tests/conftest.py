"""Shared fixtures. No test touches the network: supplier pages come from a mock transport."""

import httpx
import pytest

from competitive_intelligence.app import create_app
from competitive_intelligence.connectors import SupplierWebClient
from competitive_intelligence.registry import load_registry
from competitive_intelligence.services import load_demo_fixture
from competitive_intelligence.services.orchestrator import PipelineContext
from competitive_intelligence.services.stores import MemoryBaselineStore, MemorySnapshotStore
from competitive_intelligence.services.web_scout import WebScout

ACCESS_CODE = "test-only-access-code"
FILLER = "Dodáváme elektřinu domácnostem po celé republice a staráme se o pohodlný přechod. " * 5


def page_html(headline: str, extra: str = "") -> str:
    """A supplier page with enough readable text to count as usable content."""
    return (
        "<html><head><title>x</title><script>var token='" + extra + "';</script></head><body>"
        "<nav>Menu Kontakt</nav><div id='cookie-banner'>Souhlas s cookies " + extra + "</div>"
        f"<main><h1>{headline}</h1><p>{FILLER}</p></main></body></html>"
    )


class FakeWeb:
    """Mock transport: `pages[url]` is HTML, an int status, or an exception to raise."""

    def __init__(self, pages: dict | None = None, robots: dict[str, str] | None = None) -> None:
        self.pages = pages or {}
        self.robots = robots or {}
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if request.url.path == "/robots.txt":
            body = self.robots.get(request.url.host)
            return httpx.Response(200, text=body) if body is not None else httpx.Response(404)
        page = self.pages.get(url, 404)
        if isinstance(page, Exception):
            raise page
        if isinstance(page, httpx.Response):
            return page
        if isinstance(page, int):
            return httpx.Response(page)
        return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})

    def page_requests(self) -> list[str]:
        return [u for u in self.requests if not u.endswith("/robots.txt")]


def make_context(web: FakeWeb, snapshots=None, baselines=None, resolver=lambda host: True):
    registry = load_registry()
    client = SupplierWebClient(
        registry.allowed_urls, transport=httpx.MockTransport(web.handler), resolver=resolver
    )
    snapshot_store = MemorySnapshotStore(snapshots)
    scout = WebScout(client, snapshot_store, MemoryBaselineStore(baselines))
    ctx = PipelineContext(bundle=load_demo_fixture(), registry=registry, scout=scout)
    return ctx, snapshot_store


@pytest.fixture
def registry():
    return load_registry()


@pytest.fixture
def all_pages_ok(registry):
    return FakeWeb({url: page_html(f"Stránka {url}") for url in registry.allowed_urls})


@pytest.fixture
def ctx(all_pages_ok):
    return make_context(all_pages_ok)[0]


@pytest.fixture
def app(ctx):
    return create_app({"audit_access_code": ACCESS_CODE, "rate_limit_per_minute": 100}, context=ctx)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth():
    return {"X-Audit-Access-Code": ACCESS_CODE}


@pytest.fixture
def payload():
    return {
        "supplier_ids": ["supplier_demo_a", "supplier_demo_b", "supplier_demo_c"],
        "profile_ids": ["d02d_2mwh", "d02d_4mwh"],
        "commodity": "electricity",
        "mode": "fixture",
    }
