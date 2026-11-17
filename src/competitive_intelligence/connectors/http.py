"""Bezpečné stahování: jen schválené hostitele, omezený čas a velikost, záloha ze snapshotu.

Přesměrování se neprovádí automaticky: každý krok se zkontroluje (HTTPS, hostitel, port)
dřív, než se na něj odešle požadavek. Celý audit má jeden časový limit (`deadline`), který
platí pro všechna stahování v daném běhu, včetně vláken.

Limit jednoho stažení je tvrdý: stahování běží ve vlákně a čeká se na něj nejvýš zbývající
čas. Jednotlivé fáze (spojení, hlavičky, data) tak nemohou limit překročit součtem.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots"
ALLOWED_HOSTS = {
    "eru.gov.cz",
    "www.ote-cr.cz",
    "www.cez.cz",
    "www.centropol.cz",
    "www.eon.cz",
    "www.bezdodavatele.cz",
    "epet.cz",
    "1684053905.rsc.cdn77.org",  # CDN, ze kterého epet vydává své ceníky
    "www.pre.cz",
    "www.mnd.cz",
    "dobra-energie.eu",
    "www.spp.cz",
    "www.lamaenergy.cz",
    "www.armexenergy.cz",
}
ALLOWED_PORTS = {None, 443}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
TIMEOUT_S = 20.0
MAX_BYTES = 3_000_000
MAX_REDIRECTS = 3
MIN_REMAINING_S = 1.0  # s menším zbytkem času se nový požadavek už nezačíná

# Okamžik (time.monotonic), do kdy musí doběhnout všechna stahování aktuálního auditu.
DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("audit_deadline", default=None)
# Testy mohou podstrčit httpx transport (např. httpx.MockTransport).
TRANSPORT: httpx.BaseTransport | None = None


class FetchError(Exception):
    pass


class DeadlineExceeded(FetchError):
    pass


@dataclass
class Fetched:
    content: bytes
    url: str
    fetched_at: str  # ISO čas získání obsahu (u snapshotu čas jeho pořízení)
    origin: str  # "live" | "snapshot"
    warning: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    def meta(self) -> dict:
        return {
            "source_url": self.url,
            "fetched_at": self.fetched_at,
            "origin": self.origin,
            "warning": self.warning,
            "sha256": self.sha256[:16],
        }


def remaining() -> float | None:
    """Kolik sekund zbývá do konce auditu; None, když limit není nastaven."""
    deadline = DEADLINE.get()
    return None if deadline is None else deadline - time.monotonic()


def _check_host(url: str) -> None:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise FetchError(f"Neplatná adresa: {url}") from exc
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or port not in ALLOWED_PORTS:
        raise FetchError(f"URL není na seznamu schválených zdrojů: {url}")


def _budget() -> float:
    left = remaining()
    if left is None:
        return TIMEOUT_S
    if left < MIN_REMAINING_S:
        raise DeadlineExceeded("Vyčerpán časový limit auditu; další zdroj se už nestahuje.")
    return min(TIMEOUT_S, left)


def fetch_live(url: str) -> Fetched:
    """Stáhne URL nejvýš za zbývající čas auditu (tvrdý strop, ne součet časových limitů fází)."""
    _check_host(url)
    limit = _budget()
    stop_at = time.monotonic() + limit
    box: dict = {}
    context = contextvars.copy_context()

    def work():
        try:
            box["result"] = context.run(_fetch_live, url, stop_at)
        except BaseException as exc:  # předá se volajícímu
            box["error"] = exc

    worker = threading.Thread(target=work, name="fetch", daemon=True)
    worker.start()
    worker.join(limit)
    if worker.is_alive():
        # vlákno doběhne samo díky časovým limitům fází; výsledek se už nepoužije
        raise DeadlineExceeded(f"Zdroj neodpověděl v časovém limitu auditu ({limit:.1f} s): {url}")
    if "error" in box:
        raise box["error"]
    return box["result"]


def _phase_timeout(stop_at: float) -> float:
    left = stop_at - time.monotonic()
    if left <= 0:
        raise DeadlineExceeded("Vyčerpán časový limit stažení.")
    return min(TIMEOUT_S, left)


def _fetch_live(url: str, stop_at: float) -> Fetched:
    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=_phase_timeout(stop_at),
            headers={"User-Agent": USER_AGENT, "Accept-Language": "cs"},
            transport=TRANSPORT,
        ) as client:
            current = url
            for _ in range(MAX_REDIRECTS + 1):
                _check_host(current)  # kontrola před každým odesláním, i po přesměrování
                timeout = _phase_timeout(stop_at)
                with client.stream("GET", current, timeout=timeout) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            raise FetchError(f"Přesměrování bez cíle: {current}")
                        current = urljoin(current, location)
                        continue
                    resp.raise_for_status()
                    chunks, size = [], 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise FetchError(f"Odpověď je větší než {MAX_BYTES} bajtů: {url}")
                        if time.monotonic() >= stop_at:
                            raise DeadlineExceeded(f"Vyčerpán časový limit auditu během stahování: {url}")
                        chunks.append(chunk)
                    break
            else:
                raise FetchError(f"Příliš mnoho přesměrování: {url}")
    except httpx.HTTPError as exc:
        raise FetchError(f"Stažení selhalo ({type(exc).__name__}): {url}") from exc
    return Fetched(
        content=b"".join(chunks),
        url=url,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        origin="live",
    )


def save_snapshot(name: str, fetched: Fetched) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    (SNAPSHOT_DIR / name).write_bytes(fetched.content)
    (SNAPSHOT_DIR / f"{name}.meta.json").write_text(
        json.dumps({"url": fetched.url, "captured_at": fetched.fetched_at}, ensure_ascii=False),
        encoding="utf-8",
    )


def load_snapshot(name: str, reason: str) -> Fetched:
    path = SNAPSHOT_DIR / name
    meta_path = SNAPSHOT_DIR / f"{name}.meta.json"
    if not path.exists() or not meta_path.exists():
        raise FetchError(f"{reason} Uložený snapshot {name} neexistuje.")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    captured = meta["captured_at"]
    return Fetched(
        content=path.read_bytes(),
        url=meta["url"],
        fetched_at=captured,
        origin="snapshot",
        warning=f"Nejde o živá data: použit uložený snapshot z {captured}. ({reason})",
    )


def fetch(url: str, snapshot_name: str, live: bool = True) -> Fetched:
    """Zkusí živý zdroj; při selhání vrátí datovaný snapshot s varováním (nikdy ho nevydává za živý)."""
    if not live:
        return load_snapshot(snapshot_name, "Běh v režimu snapshot.")
    try:
        return fetch_live(url)
    except FetchError as exc:
        return load_snapshot(snapshot_name, str(exc))
