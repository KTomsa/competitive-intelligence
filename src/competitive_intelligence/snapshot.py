"""Uloží aktuální živé zdroje jako datované snapshoty.

Spuštění: python -m competitive_intelligence.snapshot
"""
from __future__ import annotations

import json
from pathlib import Path

from .connectors import eru, ote, web
from .connectors.http import save_snapshot

DATA = Path(__file__).resolve().parent / "data"


def main() -> None:
    suppliers = json.loads((DATA / "suppliers.json").read_text(encoding="utf-8"))["suppliers"]
    jobs = [(eru.SNAPSHOT, eru.load()), (ote.SNAPSHOT, ote.load())]
    jobs += [(f"web_{s['id']}.html", web.load(s)) for s in suppliers]
    for name, result in jobs:
        fetched = result["_fetched"]
        if fetched.origin != "live":
            print(f"PRESKOCENO {name}: zive stazeni selhalo")
            continue
        save_snapshot(name, fetched)
        print(f"ulozeno {name} ({len(fetched.content)} B, {fetched.fetched_at})")


if __name__ == "__main__":
    main()
