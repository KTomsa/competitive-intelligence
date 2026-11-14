"""Preview smoke checks are bounded and never launch an audit."""

import httpx
import pytest

from competitive_intelligence.release import check_preview, main


def test_preview_smoke_checks_real_flask_routes(client):
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path, request.content))
        response = client.open(request.url.path, method=request.method)
        # Offline Flask deliberately advertises demonstration storage. This
        # ensures a pretty demo cannot pass durable production acceptance.
        return httpx.Response(response.status_code, headers=dict(response.headers), content=response.data)

    report = check_preview("https://preview.example.org", transport=httpx.MockTransport(handler), require_live=False)
    checks = {item["check"]: item["passed"] for item in report["checks"]}
    assert checks["homepage"] and checks["liveness"] and checks["launch_authentication"]
    assert checks["latest_published"] and checks["persisted_status"]
    assert checks["durable_readiness"] is False and report["passed"] is False
    assert [item for item in requests if item[0] == "POST"] == [("POST", "/api/v1/audits", b"")]


def test_redirects_are_not_followed():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/health/live":
            return httpx.Response(302, headers={"location": "https://evil.example"}, json={})
        return httpx.Response(404, json={})

    report = check_preview("https://preview.example.org", transport=httpx.MockTransport(handler))
    assert report["passed"] is False
    assert all(request.url.host == "preview.example.org" for request in requests)


@pytest.mark.parametrize("url", ["http://example.org", "https://user:secret@example.org", "https://example.org/path", "https://example.org?token=secret"])
def test_preview_rejects_credential_bearing_or_non_origin_urls(url):
    with pytest.raises(ValueError):
        check_preview(url)


def test_cli_error_does_not_echo_url_credentials(capsys):
    assert main(["https://user:secret@example.org"]) == 2
    assert "secret" not in capsys.readouterr().err
