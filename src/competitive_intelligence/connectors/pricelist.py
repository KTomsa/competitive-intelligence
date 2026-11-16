"""Ceníky dodavatelů (PDF).

Stáhne oficiální ceník z webu dodavatele, převede ho na text a umožní ověřit, že
obsahuje cenu a stálý plat zapsané v přípravě. Záloha je datovaný textový snapshot.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

from pypdf import PdfReader

from . import http
from .http import Fetched, FetchError, load_snapshot, save_snapshot
from .web import normalize


def pdf_to_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    return normalize(" ".join((page.extract_text() or "") for page in reader.pages))


def snapshot_name(offer: dict) -> str:
    return f"pricelist_{offer['id']}.txt"


def contains_all(text: str, markers: list[str]) -> bool:
    folded = normalize(text).casefold()
    return all(normalize(m).casefold() in folded for m in markers)


def load(offer: dict, live: bool = True) -> dict | None:
    """Vrátí text ceníku a metadata zdroje; None, když nabídka nemá stažitelný ceník."""
    price_list = offer.get("price_list") or {}
    url = price_list.get("url")
    if not url:
        return None
    name = snapshot_name(offer)
    fetched: Fetched
    if live:
        try:
            raw = http.fetch_live(url)
            text = pdf_to_text(raw.content)
            if not text:
                raise FetchError(f"Ceník neobsahuje čitelný text: {url}")
            fetched = Fetched(
                content=text.encode("utf-8"),
                url=url,
                fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                origin="live",
            )
        except FetchError as exc:
            fetched = load_snapshot(name, str(exc))
        except Exception as exc:  # poškozené nebo jinak nečitelné PDF
            fetched = load_snapshot(name, f"Ceník se nepodařilo přečíst ({type(exc).__name__}).")
    else:
        fetched = load_snapshot(name, "Běh v režimu snapshot.")
    source = fetched.meta()
    source.update({
        "text_sha256": fetched.sha256,
        "name": f"Ceník: {offer['product']}",
        "document": price_list.get("document"),
        "effective_from": price_list.get("effective_from"),
    })
    return {"text": fetched.content.decode("utf-8"), "source": source, "_fetched": fetched}


def save(offer: dict, result: dict) -> None:
    save_snapshot(snapshot_name(offer), result["_fetched"])
