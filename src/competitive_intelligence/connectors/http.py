"""Bezpečné stahování: jen schválené hostitele, omezený čas a velikost, záloha ze snapshotu."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

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
}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
TIMEOUT_S = 20.0
MAX_BYTES = 3_000_000


class FetchError(Exception):
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


def _check_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise FetchError(f"URL není na seznamu schválených zdrojů: {url}")


def fetch_live(url: str) -> Fetched:
    _check_host(url)
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=TIMEOUT_S,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "cs"},
        ) as client:
            with client.stream("GET", url) as resp:
                _check_host(str(resp.url))  # i cíl přesměrování musí být schválený
                resp.raise_for_status()
                chunks, size = [], 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise FetchError(f"Odpověď je větší než {MAX_BYTES} bajtů: {url}")
                    chunks.append(chunk)
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
