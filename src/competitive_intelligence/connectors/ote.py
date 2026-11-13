"""OTE – měsíční počty odběrných a předacích míst (OPM) dodavatelů elektřiny.

OPM nejsou zákazníci: jeden zákazník může mít více OPM. Prázdná buňka znamená
chybějící údaj, ne nulu.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .http import fetch

PAGE_URL = "https://www.ote-cr.cz/cs/statistika/mesicni-zprava-elektrina/pocty-opm-dodavatelu-v-cs-ote"
SNAPSHOT = "ote_opm.html"


class OteFormatError(Exception):
    pass


def parse_table(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="report_table")
    if table is None or table.find("tbody") is None:
        raise OteFormatError("Na stránce OTE nebyla nalezena tabulka počtů OPM.")
    year_match = re.search(r"Pocet_OPM_dodavatele_(\d{4})", html)
    year = int(year_match.group(1)) if year_match else None
    suppliers: dict[str, list[int | None]] = {}
    for tr in table.find("tbody").find_all("tr"):
        name_cell = tr.find("th")
        cells = tr.find_all("td")
        if name_cell is None or len(cells) != 12:
            continue
        values: list[int | None] = []
        for td in cells:
            digits = re.sub(r"\D", "", td.get_text())
            values.append(int(digits) if digits else None)
        suppliers[name_cell.get_text(strip=True)] = values
    if len(suppliers) < 10:
        raise OteFormatError("Tabulka OTE má neočekávaný formát (příliš málo dodavatelů).")
    filled = [
        i for i in range(12)
        if sum(v[i] is not None for v in suppliers.values()) > len(suppliers) / 2
    ]
    if not filled:
        raise OteFormatError("Tabulka OTE neobsahuje žádný vyplněný měsíc.")
    return {"year": year, "latest_month": filled[-1] + 1, "suppliers": suppliers}


def ranking(table: dict) -> list[tuple[str, int]]:
    idx = table["latest_month"] - 1
    pairs = [(n, v[idx]) for n, v in table["suppliers"].items() if v[idx] is not None]
    return sorted(pairs, key=lambda p: -p[1])


def load(live: bool = True) -> dict:
    fetched = fetch(PAGE_URL, SNAPSHOT, live=live)
    table = parse_table(fetched.content.decode("utf-8", "replace"))
    year = table["year"] or "rok neuveden"
    source = fetched.meta()
    source.update({
        "name": "OTE – počty OPM dodavatelů elektřiny",
        "page_url": PAGE_URL,
        "data_period": f"{year}-{table['latest_month']:02d}",
        "unit": "počet odběrných a předacích míst (OPM), ne zákazníků",
    })
    return {"table": table, "ranking": ranking(table), "source": source, "_fetched": fetched}
