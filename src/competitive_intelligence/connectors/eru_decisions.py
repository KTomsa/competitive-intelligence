"""ERÚ – pravomocná rozhodnutí (https://eru.gov.cz/pravomocna-rozhodnuti).

Seznam lze filtrovat podle účastníka řízení. Konektor čte jen název, datum zveřejnění
a štítky rozhodnutí; samotná rozhodnutí (PDF) neotevírá.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from . import http
from .http import Fetched, FetchError, load_snapshot, save_snapshot

LIST_URL = "https://eru.gov.cz/pravomocna-rozhodnuti"
PAGE_SIZE = 10
MAX_PAGES = 3
KIND_LABEL = {
    "practice": "přestupek: obchodní praktiky nebo nepravdivé informace",
    "offence": "přestupek: jiné porušení povinností",
    "dispute": "rozhodnutí ve sporu",
    "discontinued": "zastavené řízení",
    "other": "jiné rozhodnutí",
}


class DecisionsFormatError(Exception):
    pass


def classify(title: str, tags: list[str]) -> str:
    low = title.casefold()
    if "zastaven" in low:
        return "discontinued"  # zastavené řízení neznamená, že se přestupek stal
    if any(t.casefold() == "přestupky" for t in tags):
        return "practice" if re.search(r"nekal|agresivn|nepravdiv|klamav", low) else "offence"
    return "dispute" if "spor" in low else "other"


def parse_list(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    if soup.find("input", attrs={"name": "ucastnik_rizeni"}) is None:
        raise DecisionsFormatError("Stránka rozhodnutí ERÚ nemá očekávaný filtr podle účastníka řízení.")
    items = []
    for row in soup.select("div.view-content > div.views-row"):
        link = row.select_one(".views-field-title a")
        date = row.select_one(".views-field-field-aktualita-datum-aktuality .field-content")
        if link is None:
            continue
        title = re.sub(r"\s+", " ", link.get_text()).strip()
        tags = [li.get_text(strip=True) for li in row.select("li")]
        date_text = date.get_text(strip=True) if date else ""
        match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", date_text)
        items.append({
            "title": title,
            "url": urljoin(LIST_URL, link.get("href", "")),
            "date": date_text,
            "date_iso": f"{match.group(3)}-{match.group(2)}-{match.group(1)}" if match else "",
            "tags": tags,
            "kind": classify(title, tags),
        })
    return items


def snapshot_name(supplier: dict) -> str:
    return f"eru_decisions_{supplier['id']}.json"


def search_name(supplier: dict) -> str:
    return supplier.get("eru_name") or re.sub(r",? (a\. ?s\.|s\.r\.o\.|k\.s\.)$", "", supplier.get("ote_name", ""))


def load(supplier: dict, live: bool = True) -> dict:
    """Rozhodnutí, ve kterých je dodavatel účastníkem řízení, od nejnovějšího."""
    name = search_name(supplier)
    query_url = f"{LIST_URL}?{urlencode({'ucastnik_rizeni': name})}"
    fetched: Fetched
    if live:
        try:
            items: list[dict] = []
            truncated = False
            for page in range(MAX_PAGES):
                raw = http.fetch_live(f"{query_url}&page={page}")
                found = parse_list(raw.content.decode("utf-8", "replace"))
                items += [i for i in found if i not in items]
                if len(found) < PAGE_SIZE:
                    break
            else:
                truncated = True  # načteny jen první stránky výsledků
            items = {"items": items, "truncated": truncated}
            fetched = Fetched(
                content=json.dumps(items, ensure_ascii=False).encode("utf-8"),
                url=query_url,
                fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                origin="live",
            )
        except (FetchError, DecisionsFormatError) as exc:
            fetched = load_snapshot(snapshot_name(supplier), str(exc))
    else:
        fetched = load_snapshot(snapshot_name(supplier), "Běh v režimu snapshot.")
    payload = json.loads(fetched.content.decode("utf-8"))
    if isinstance(payload, list):  # starší formát snapshotu
        payload = {"items": payload, "truncated": False}
    decisions = [
        d for d in payload["items"]
        if name.casefold() in d["title"].casefold()  # filtr ERÚ hledá i v dalších polích
    ]
    for decision in decisions:  # snapshot může být starší než pravidla třídění
        decision["kind"] = classify(decision["title"], decision.get("tags", []))
    decisions.sort(key=lambda d: d["date_iso"], reverse=True)
    source = fetched.meta()
    source.update({"name": f"ERÚ – pravomocná rozhodnutí: {supplier['name']}", "page_url": query_url})
    return {"decisions": decisions, "truncated": payload["truncated"], "source": source, "_fetched": fetched}


def save(supplier: dict, result: dict) -> None:
    save_snapshot(snapshot_name(supplier), result["_fetched"])
