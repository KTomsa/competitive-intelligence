"""ERÚ – indikativní pásma cen elektřiny (https://eru.gov.cz/ipnc).

Stahuje zveřejněný CSV soubor, ne interaktivní graf. Hodnoty jsou cena komodity
v Kč/MWh; soubor neobsahuje stálý měsíční plat.
"""
from __future__ import annotations

import csv
import io
import math
import re
from urllib.parse import urljoin

from .http import FetchError, fetch

PAGE_URL = "https://eru.gov.cz/ipnc"
METHODOLOGY_URL = "https://eru.gov.cz/metodika-stanoveni-ipnc"
FALLBACK_CSV_URL = "https://eru.gov.cz/sites/default/files/obsah/prilohy/elektrina_6.csv"
SNAPSHOT = "eru_elektrina.csv"

COL_MONTH = "Měsíc"
COL_TARIFF = "Distribuční sazba"
COL_FIX = "Délka fixace"
COL_LO_EX = "Dolní hranice InPásma (bez DPH)"
COL_HI_EX = "Horní hranice InPásma (bez DPH)"
COL_LO_IN = "Dolní hranice InPásma (s DPH)"
COL_HI_IN = "Horní hranice InPásma (s DPH)"
REQUIRED = [COL_MONTH, COL_TARIFF, COL_FIX, COL_LO_EX, COL_HI_EX, COL_LO_IN, COL_HI_IN]
VALUE_KEYS = ("low_excl_vat", "high_excl_vat", "low_incl_vat", "high_incl_vat")


class EruFormatError(Exception):
    pass


def resolve_csv_url(page_html: str) -> str:
    match = re.search(r'href="([^"]*elektrina[^"]*\.csv)"', page_html, re.I)
    if not match:
        raise EruFormatError("Na stránce ERÚ nebyl nalezen odkaz na CSV s elektřinou.")
    return urljoin(PAGE_URL, match.group(1))


def parse_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise EruFormatError("V CSV ERÚ chybí sloupce: " + ", ".join(missing))
    rows = []
    for raw in reader:
        try:
            row = {
                "month": raw[COL_MONTH][:7],  # RRRR-MM
                "tariff": raw[COL_TARIFF].strip(),
                "fixation_months": int(raw[COL_FIX]),
                "low_excl_vat": float(raw[COL_LO_EX]),
                "high_excl_vat": float(raw[COL_HI_EX]),
                "low_incl_vat": float(raw[COL_LO_IN]),
                "high_incl_vat": float(raw[COL_HI_IN]),
            }
        except (ValueError, TypeError) as exc:
            raise EruFormatError(f"Neplatný řádek v CSV ERÚ: {raw}") from exc
        numbers_ok = all(math.isfinite(row[k]) and row[k] > 0 for k in VALUE_KEYS)
        if not numbers_ok or not re.fullmatch(r"\d{4}-\d{2}", row["month"]):
            raise EruFormatError(f"Neplatná hodnota v CSV ERÚ: {raw}")
        rows.append(row)
    if not rows:
        raise EruFormatError("CSV ERÚ neobsahuje žádné řádky.")
    return rows


def select_band(rows: list[dict], tariff: str, fixation_months: int, month: str) -> dict | None:
    """Vrátí právě jeden řádek pro sazbu, fixaci a měsíc; jinak None (žádná náhrada jiným obdobím)."""
    matches = [
        r for r in rows
        if r["tariff"] == tariff and r["fixation_months"] == fixation_months and r["month"] == month
    ]
    if len(matches) > 1:
        raise EruFormatError(f"Duplicitní řádky ERÚ pro {tariff}/{fixation_months}/{month}.")
    return matches[0] if matches else None


def load(live: bool = True) -> dict:
    """Vrátí řádky pásem a metadata zdroje; při selhání živého zdroje datovaný snapshot."""
    csv_url = FALLBACK_CSV_URL
    if live:
        try:
            page = fetch(PAGE_URL, "eru_ipnc.html", live=True)
            csv_url = resolve_csv_url(page.content.decode("utf-8", "replace"))
        except (FetchError, EruFormatError):
            csv_url = FALLBACK_CSV_URL
    fetched = fetch(csv_url, SNAPSHOT, live=live)
    rows = parse_csv(fetched.content)
    months = sorted({r["month"] for r in rows})
    source = fetched.meta()
    source.update({
        "name": "ERÚ – indikativní pásma cen elektřiny",
        "page_url": PAGE_URL,
        "methodology_url": METHODOLOGY_URL,
        "data_period": f"{months[0]} až {months[-1]}",
        "unit": "Kč/MWh (cena komodity, bez stálého měsíčního platu)",
    })
    return {"rows": rows, "source": source, "months": months, "_fetched": fetched}
