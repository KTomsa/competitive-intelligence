"""Bounded, allowlisted fetch of supplier pages.

This is deliberately not a crawler. It fetches only exact URLs from the
supplier registry, honours robots.txt, follows a redirect only to an
allowlisted host, refuses non-public addresses and caps time and size. When a
site says no, the answer is a failed fetch; nothing here tries to get around it.
"""

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "FactOrWattBot/0.1 (student research project; respects robots.txt)"
ROBOTS_TOKEN = "FactOrWattBot"
MAX_REDIRECTS = 3


@dataclass(frozen=True)
class FetchResult:
    url: str
    ok: bool
    content: bytes | None = None
    reason: str | None = None


def host_is_public(host: str) -> bool:
    """Resolve the host and require every address to be globally routable (SSRF guard)."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


class SupplierWebClient:
    def __init__(
        self,
        allowed_urls: set[str],
        *,
        timeout: float = 15.0,
        max_bytes: int = 2_000_000,
        transport: httpx.BaseTransport | None = None,
        resolver: Callable[[str], bool] = host_is_public,
    ) -> None:
        self._allowed_urls = allowed_urls
        self._allowed_hosts = {urlsplit(u).hostname for u in allowed_urls}
        self._max_bytes = max_bytes
        self._resolver = resolver
        self._robots: dict[str, RobotFileParser | None] = {}
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Language": "cs"},
        )

    def close(self) -> None:
        self._client.close()

    # -- checks -----------------------------------------------------------

    def _target_problem(self, url: str) -> str | None:
        parts = urlsplit(url)
        if parts.scheme != "https":
            return "adresa není https"
        if parts.hostname not in self._allowed_hosts:
            return "host není na seznamu schválených zdrojů"
        if not self._resolver(parts.hostname or ""):
            return "host se nepřekládá na veřejnou adresu"
        return None

    def _robots_for(self, host: str) -> RobotFileParser | None:
        """Parsed robots.txt, or None when it cannot be read and we must not fetch."""
        if host not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                response = self._client.get(f"https://{host}/robots.txt")
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                elif 400 <= response.status_code < 500:
                    parser.parse([])  # no robots.txt: nothing is disallowed
                else:
                    parser = None
            except httpx.HTTPError:
                parser = None
            self._robots[host] = parser
        return self._robots[host]

    # -- fetch ------------------------------------------------------------

    def fetch(self, url: str) -> FetchResult:
        if url not in self._allowed_urls:
            return FetchResult(url, False, reason="adresa není na seznamu schválených zdrojů")
        current = url
        try:
            for _ in range(MAX_REDIRECTS + 1):
                problem = self._target_problem(current)
                if problem:
                    return FetchResult(url, False, reason=problem)
                robots = self._robots_for(urlsplit(current).hostname or "")
                if robots is None:
                    return FetchResult(url, False, reason="robots.txt se nepodařilo načíst")
                if not robots.can_fetch(ROBOTS_TOKEN, current):
                    return FetchResult(url, False, reason="přístup zakázán v robots.txt")
                with self._client.stream("GET", current) as response:
                    if response.is_redirect:
                        current = urljoin(current, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200:
                        return FetchResult(url, False, reason=f"HTTP {response.status_code}")
                    content_type = response.headers.get("content-type", "")
                    if "html" not in content_type:
                        return FetchResult(url, False, reason=f"neočekávaný typ obsahu ({content_type})")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self._max_bytes:
                            return FetchResult(url, False, reason="odpověď je příliš velká")
                        chunks.append(chunk)
                    return FetchResult(url, True, content=b"".join(chunks))
            return FetchResult(url, False, reason="příliš mnoho přesměrování")
        except httpx.TimeoutException:
            return FetchResult(url, False, reason="HTTP timeout")
        except httpx.HTTPError as exc:
            return FetchResult(url, False, reason=f"chyba spojení ({type(exc).__name__})")
