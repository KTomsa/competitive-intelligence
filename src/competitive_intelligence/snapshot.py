"""Uloží aktuální živé zdroje jako datované snapshoty.

Spuštění: python -m competitive_intelligence.snapshot
"""
from __future__ import annotations

import json
from pathlib import Path

from .connectors import eru, eru_decisions, ote, pricelist, web
from .connectors.http import FetchError, save_snapshot

DATA = Path(__file__).resolve().parent / "data"


def main() -> None:
    suppliers = json.loads((DATA / "suppliers.json").read_text(encoding="utf-8"))["suppliers"]
    jobs = [
        (eru.SNAPSHOT, lambda: eru.load()),
        (ote.COMMODITIES["ele"]["snapshot"], lambda: ote.load()),
        (ote.COMMODITIES["gas"]["snapshot"], lambda: ote.load_gas()),
    ]
    jobs += [(f"web_{s['id']}.html", lambda s=s: web.load(s)) for s in suppliers]
    for name, job in jobs:
        try:
            fetched = job()["_fetched"]
        except FetchError as exc:
            print(f"CHYBA {name}: {exc}")
            continue
        if fetched.origin != "live":
            print(f"PRESKOCENO {name}: zive stazeni selhalo")
            continue
        save_snapshot(name, fetched)
        print(f"ulozeno {name} ({len(fetched.content)} B, {fetched.fetched_at})")

    for supplier in suppliers:
        for offer in supplier["offers"]:
            try:
                result = pricelist.load(offer)
            except FetchError as exc:
                print(f"CHYBA cenik {offer['id']}: {exc}")
                continue
            if result is None:
                continue  # ceník dodaný týmem, bez adresy ke stažení
            if result["_fetched"].origin != "live":
                print(f"PRESKOCENO cenik {offer['id']}: zive stazeni selhalo")
                continue
            ok = pricelist.contains_all(result["text"], (offer.get("price_list") or {}).get("markers") or [])
            pricelist.save(offer, result)
            print(f"ulozeno cenik {offer['id']} ({len(result['text'])} znaku) cena a plat v ceniku: {ok}")

    for supplier in suppliers:
        try:
            result = eru_decisions.load(supplier)
        except FetchError as exc:
            print(f"CHYBA rozhodnuti {supplier['id']}: {exc}")
            continue
        if result["_fetched"].origin != "live":
            print(f"PRESKOCENO rozhodnuti {supplier['id']}: zive stazeni selhalo")
            continue
        eru_decisions.save(supplier, result)
        kinds = [d["kind"] for d in result["decisions"]]
        print(f"ulozeno rozhodnuti {supplier['id']}: {len(kinds)} celkem, "
              f"{kinds.count('practice')} o obchodnich praktikach")


if __name__ == "__main__":
    main()
