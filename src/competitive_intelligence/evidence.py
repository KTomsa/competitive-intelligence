"""Společná brána „sedí důkaz na tvrzení?“.

Jedno místo, které rozhoduje o tom, zda je podklad použitelný: rozsah tvrzení (metrika,
komodity, subjekty, období, srovnání), oprávněnost nabídky (sazba, území, fixace, platnost,
stav dokumentu) a srovnatelné období v tabulkách OTE. Pravidla, srovnání nabídek i pohled
firmy používají tytéž funkce, takže stejný podklad nemůže být v jednom místě platný
a v jiném neplatný. Každé „ne“ vrací důvod, ne jen False.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .connectors import ote as ote_connector

METRICS = ("opm", "customers", "unspecified")
COMPARATORS = ("at_least", "at_most", "approx", "exactly")
ELECTRICITY, GAS = "elektřina", "plyn"
COMMODITIES = (ELECTRICITY, GAS)
# Nepotvrzená nabídka starší než tohle se k datu srovnání nepovažuje za platnou.
MAX_UNCONFIRMED_AGE_DAYS = 62


@dataclass(frozen=True)
class Check:
    ok: bool
    reason: str | None = None


OK = Check(True)


# ---------- rozsah tvrzení ----------

@dataclass(frozen=True)
class ClaimScope:
    """Co přesně tvrzení tvrdí. Vyplňuje tým v suppliers.json, výchozí hodnoty jsou opatrné."""

    metric: str = "unspecified"  # opm | customers | unspecified
    commodities: tuple[str, ...] = (ELECTRICITY,)
    entities: tuple[str, ...] = ()  # právnické osoby podle OTE (u sloučených firem více)
    period: str | None = None  # RRRR-MM, ke kterému tvrzení míří; None = tvrzení datum neuvádí
    comparator: str = "approx"
    market: str = "CZ"
    qualifier: str | None = None  # „český“, „alternativní“ …

    @property
    def label(self) -> str:
        metric = {"opm": "odběrná místa", "customers": "zákazníci", "unspecified": "metrika neuvedena"}[self.metric]
        return f"{metric}; {', '.join(self.commodities)}; {', '.join(self.entities)}"


def claim_scope(claim: dict, supplier: dict) -> ClaimScope:
    params = claim.get("params") or {}
    scope = params.get("scope") if isinstance(params.get("scope"), dict) else {}
    merged = tuple(params.get("merged_with") or scope.get("merged_with") or ())
    metric = scope.get("metric") or params.get("metric") or "unspecified"
    commodities = tuple(scope.get("commodities") or params.get("commodities") or (ELECTRICITY,))
    comparator = scope.get("comparator") or params.get("comparator") or "approx"
    if metric not in METRICS:
        metric = "unspecified"
    if comparator not in COMPARATORS:
        comparator = "approx"
    market = scope.get("market") or ("multi" if params.get("includes_other_markets") else "CZ")
    return ClaimScope(
        metric=metric,
        commodities=commodities,
        entities=(supplier.get("ote_name") or "",) + merged,
        period=scope.get("period") or params.get("period"),
        comparator=comparator,
        market=market,
        qualifier=scope.get("qualifier") or params.get("qualifier"),
    )


def ote_scope_gate(scope: ClaimScope, ctx: dict) -> Check:
    """Smí se tvrzení o velikosti nebo růstu posoudit daty OTE? Nepodporovaný rozsah = zdržet se.

    OTE pokrývá jen český trh, elektřinu a plyn a jedno zveřejněné období. Tvrzení o jiném trhu,
    jiné komoditě nebo jiném období se daty OTE nehodnotí, i kdyby čísla vypadala podobně.
    """
    if scope.market != "CZ":
        return Check(False, "Tvrzení se týká i jiných trhů než Česka; OTE pokrývá jen český trh.")
    unknown = [c for c in scope.commodities if c not in COMMODITIES]
    if unknown or not scope.commodities:
        return Check(False, f"Komoditu „{', '.join(unknown) or 'neuvedena'}“ data OTE nepokrývají.")
    for commodity in scope.commodities:
        data = ctx.get("ote") if commodity == ELECTRICITY else ctx.get("ote_gas")
        if not data:
            return Check(False, f"Tabulka OTE pro komoditu „{commodity}“ není k dispozici.")
        period = table_period(data)
        if scope.period and period != scope.period:
            return Check(False, f"Tvrzení se týká období {scope.period}, data OTE ({commodity}) jsou za {period}.")
    return OK


# ---------- OTE: období a součty ----------

def _row(table_data: dict | None, entity: str) -> list[int | None] | None:
    if not table_data:
        return None
    for name, values in table_data["table"]["suppliers"].items():
        if ote_connector.same_supplier(name, entity):
            return values
    return None


def table_period(table_data: dict | None) -> str | None:
    if not table_data:
        return None
    table = table_data["table"]
    return f"{table.get('year') or '????'}-{table['latest_month']:02d}"


@dataclass(frozen=True)
class Measure:
    """Součet odběrných míst pro subjekty a komodity v jednom měsíci; total None = nelze určit."""

    total: int | None
    period: str | None
    parts: tuple[tuple[str, str, int | None], ...] = ()  # (subjekt, komodita, počet)
    reason: str | None = None

    def breakdown(self) -> str:
        return "; ".join(
            f"{entity} – {commodity}: {'chybí' if value is None else format(value, ',').replace(',', ' ')}"
            for entity, commodity, value in self.parts
        )


def opm_measure(ctx: dict, entities: tuple[str, ...], commodities: tuple[str, ...]) -> Measure:
    """Součet OPM. Chybějící údaj není nula a komodity z různých období se nesčítají."""
    tables = {ELECTRICITY: ctx.get("ote"), GAS: ctx.get("ote_gas")}
    periods = set()
    for commodity in commodities:
        data = tables.get(commodity)
        if not data:
            return Measure(None, None, reason=f"Tabulka OTE pro komoditu „{commodity}“ není k dispozici.")
        periods.add(table_period(data))
    if len(periods) != 1:
        return Measure(None, None, reason="Tabulky OTE pro elektřinu a plyn mají jiné období; nesčítají se.")
    period = periods.pop()
    parts, total, missing = [], 0, []
    for entity in entities:
        for commodity in commodities:
            row = _row(tables[commodity], entity)
            idx = tables[commodity]["table"]["latest_month"] - 1
            value = row[idx] if row else None
            parts.append((entity, commodity, value))
            if value is None:
                missing.append(f"{entity} ({commodity})")
            else:
                total += value
    if missing:
        return Measure(
            None, period, tuple(parts),
            reason="Chybí údaj OTE za " + ", ".join(missing) + f" v období {period}; chybějící údaj není nula.",
        )
    return Measure(total, period, tuple(parts))


def compare(observed: int, claimed: int, comparator: str) -> bool | None:
    """True/False, když údaj tvrzení potvrzuje/vyvrací; None, když je výsledek nejednoznačný."""
    if comparator == "at_least":
        return observed >= claimed
    if comparator == "at_most":
        return observed <= claimed
    if comparator == "exactly":
        return observed == claimed
    deviation = abs(observed - claimed) / claimed if claimed else 1
    if deviation <= 0.05:
        return True
    if deviation > 0.15:
        return False
    return None


@dataclass(frozen=True)
class Window:
    """Společné období pro změny počtu OPM: první a poslední měsíc vyplněný u většiny dodavatelů."""

    start: int  # index 0–11
    end: int
    year: str

    @property
    def label(self) -> str:
        return f"{self.year}-{self.start + 1:02d} až {self.year}-{self.end + 1:02d}"


def common_window(table_data: dict | None) -> Window | None:
    if not table_data:
        return None
    table = table_data["table"]
    suppliers = table["suppliers"].values()
    filled = [i for i in range(12) if sum(v[i] is not None for v in suppliers) > len(suppliers) / 2]
    if len(filled) < 2:
        return None
    return Window(filled[0], filled[-1], str(table.get("year") or "????"))


@dataclass(frozen=True)
class Change:
    first: int | None
    last: int | None
    window: Window | None
    reason: str | None = None

    @property
    def abs(self) -> int | None:
        return None if self.first is None or self.last is None else self.last - self.first

    @property
    def pct(self) -> float | None:
        """Procentní změna; None, když chybí údaj nebo je výchozí stav nula (procento není definováno)."""
        if self.abs is None or not self.first:
            return None
        return round(self.abs / self.first * 100, 1)


def pct_label(pct: float | None) -> str:
    """Procento pro text; u nulového výchozího stavu napíše, že ho nelze určit."""
    if pct is None:
        return "procento nelze určit"
    return f"{pct:+.1f}".replace(".", ",") + " %"


def opm_change_scope(ctx: dict, entities: tuple[str, ...], commodities: tuple[str, ...]) -> Change:
    """Změna OPM pro subjekty a komodity z rozsahu tvrzení.

    Každá komodita se čte ze své tabulky; tabulky musí mít stejné společné období, jinak se
    změny nesčítají. Chybějící údaj u kteréhokoli subjektu nebo komodity změnu zastaví.
    """
    tables = {ELECTRICITY: ctx.get("ote"), GAS: ctx.get("ote_gas")}
    windows, first, last = set(), 0, 0
    window = None
    for commodity in commodities:
        data = tables.get(commodity)
        if not data:
            return Change(None, None, None, f"Tabulka OTE pro komoditu „{commodity}“ není k dispozici.")
        for entity in entities:
            change = opm_change(data, entity)
            if change.abs is None:
                return Change(None, None, change.window, f"{commodity}: {change.reason}")
            windows.add(change.window.label)
            window = change.window
            first += change.first
            last += change.last
    if len(windows) != 1:
        return Change(None, None, None, "Tabulky OTE pro elektřinu a plyn mají jiné období; změny se nesčítají.")
    return Change(first, last, window)


def opm_change(table_data: dict | None, entity: str) -> Change:
    """Změna OPM jednoho subjektu ve společném období; chybějící krajní měsíc změnu zastaví."""
    window = common_window(table_data)
    if window is None:
        return Change(None, None, None, "Tabulka OTE nemá dvě srovnatelná období.")
    row = _row(table_data, entity)
    if row is None:
        return Change(None, None, window, f"Subjekt {entity} není v tabulce OTE.")
    first, last = row[window.start], row[window.end]
    if first is None or last is None:
        return Change(first, last, window, f"Chybí údaj na začátku nebo na konci období {window.label}.")
    return Change(first, last, window)


@dataclass(frozen=True)
class MarketChange:
    pct: float | None
    window: Window | None
    cohort: int = 0
    excluded: int = 0


def market_change(table_data: dict | None) -> MarketChange:
    """Změna trhu jen za dodavatele, kteří mají údaj na začátku i na konci společného období."""
    window = common_window(table_data)
    if window is None:
        return MarketChange(None, None)
    first = last = cohort = excluded = 0
    for values in table_data["table"]["suppliers"].values():
        a, b = values[window.start], values[window.end]
        if a is None or b is None:
            excluded += 1
            continue
        first, last, cohort = first + a, last + b, cohort + 1
    pct = round((last - first) / first * 100, 1) if first else None
    return MarketChange(pct, window, cohort, excluded)


# ---------- nabídky ----------

def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def offer_valid_on(offer: dict, day: date) -> Check:
    price_list = offer.get("price_list") or {}
    start = _parse_date(offer.get("valid_from") or price_list.get("effective_from"))
    end = _parse_date(offer.get("valid_until") or price_list.get("valid_until"))
    if start and day < start:
        return Check(False, f"Ceník platí až od {start.isoformat()}.")
    if end and day > end:
        return Check(False, f"Ceník platil jen do {end.isoformat()}.")
    return OK


def comparison_date(ctx: dict) -> date:
    """Jedno datum, ke kterému se srovnávají nabídky ve verdiktu, tabulce i doporučení."""
    return ctx.get("comparison_date") or ctx["audit_date"]


def offer_current(offer: dict, ctx: dict) -> Check:
    """Platí nabídka k datu srovnání? Bez toho nelze dvě nabídky postavit vedle sebe."""
    as_of = comparison_date(ctx)
    valid = offer_valid_on(offer, as_of)
    if not valid.ok:
        return Check(False, f"K datu srovnání {as_of.isoformat()} nabídka neplatí. {valid.reason}")
    if offer.get("confirmed_live"):
        return OK
    captured = _parse_date(offer.get("captured_at"))
    if captured is None:
        return Check(False, "Nepotvrzená nabídka nemá datum zachycení; k datu srovnání ji nelze použít.")
    if captured > as_of:
        return Check(False, f"Nabídka byla zachycena až po datu srovnání {as_of.isoformat()}.")
    if (as_of - captured).days > MAX_UNCONFIRMED_AGE_DAYS:
        return Check(
            False,
            f"Nabídka není živě potvrzena a byla zachycena {captured.isoformat()}, víc než "
            f"{MAX_UNCONFIRMED_AGE_DAYS} dní před datem srovnání {as_of.isoformat()}.",
        )
    return OK


@dataclass(frozen=True)
class OfferCheck:
    ok: bool
    reason: str | None = None
    period: str | None = None  # RRRR-MM, ke kterému se nabídka srovnává s pásmem ERÚ
    notes: tuple[str, ...] = field(default_factory=tuple)


def offer_eligibility(
    offer: dict, ctx: dict, *, need_fees: bool = False, audience: str | None = None, current: bool = False
) -> OfferCheck:
    """Jediné pravidlo, zda nabídka smí vstoupit do verdiktu, žebříčku nebo doporučení.

    `current=True` se používá všude, kde stojí dvě nabídky vedle sebe (srovnání, žebříček,
    rozdíl pro nové a stávající zákazníky): obě musí platit k jednomu datu srovnání.
    """
    profile = ctx["profile"]
    if offer.get("stale"):
        return OfferCheck(False, "Živý zdroj už cenu z přípravy neobsahuje; nabídku je potřeba znovu zachytit.")
    if offer.get("stepped"):
        return OfferCheck(False, "Produkt s cenou měnící se v čase nelze srovnávat jednou cenou.")
    if offer.get("tariff") != profile["tariff"]:
        return OfferCheck(False, f"Nabídka není pro srovnávací sazbu {profile['tariff']}.")
    if profile.get("area") and offer.get("area") != profile["area"]:
        return OfferCheck(False, f"Nabídka není pro srovnávací distribuční území {profile['area']}.")
    if offer.get("fixation_months") is None:
        return OfferCheck(False, "Produkt nemá fixaci; srovnávají se jen fixované produkty.")
    if offer.get("price_mwh_excl_vat") is None or offer.get("price_mwh_incl_vat") is None:
        return OfferCheck(False, "Nabídka nemá cenu s DPH i bez DPH.")
    if need_fees and offer.get("monthly_fee_incl_vat") is None:
        return OfferCheck(False, "Nabídka nemá stálý měsíční plat, roční náklad nelze spočítat.")
    if audience and offer.get("audience", "new") not in (audience, "all"):
        return OfferCheck(False, "Nabídka je určena jiné skupině zákazníků.")
    if current:
        now = offer_current(offer, ctx)
        if not now.ok:
            return OfferCheck(False, now.reason)
    today = ctx["audit_date"]
    captured = _parse_date(offer.get("captured_at")) or today
    notes = []
    if offer.get("confirmed_live") and offer_valid_on(offer, today).ok:
        period = today.strftime("%Y-%m")
    else:
        valid = offer_valid_on(offer, captured)
        if not valid.ok:
            return OfferCheck(False, f"Nabídka v době zachycení neplatila: {valid.reason}")
        period = captured.strftime("%Y-%m")
        notes.append(f"Nabídka není živě potvrzena; srovnává se k období zachycení {period}.")
    if offer.get("document_status") == "changed":
        notes.append("Ceník se od kontroly změnil; hodnoty jsou z ručně zkontrolované verze.")
    return OfferCheck(True, None, period, tuple(notes))
