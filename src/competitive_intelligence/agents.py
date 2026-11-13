"""Logické role (ne samostatné služby): Claim Extractor a Strategist.

Obě role umí běžet s jazykovým modelem (Gemini, strukturovaný výstup kontrolovaný kódem)
i bez něj (klíčová slova a šablona). Model nikdy nepočítá čísla ani verdikty.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Literal

from pydantic import BaseModel

from .connectors.web import contains, normalize
from .rules import CONTRADICTED, PARTIAL, RULE_LABELS, SUPPORTED, UNVERIFIABLE

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
LLM_TIMEOUT_MS = 15_000
MAX_PAGE_CHARS = 60_000
MAX_NEW_CLAIMS = 3

RuleId = Literal[
    "price_favorable", "price_rank", "price_pct_cheaper", "price_guarantee", "market_size",
    "customer_count", "customer_growth", "green_share", "carbon_neutral", "green_generic",
    "soft_trust", "other",
]


def llm_available() -> bool:
    # Model se zapíná výslovně (CI_USE_LLM=1), aby aplikace nepoužila cizí klíč z prostředí.
    if os.environ.get("CI_USE_LLM") != "1":
        return False
    return bool(os.environ.get("GEMINI_API_KEY"))


class LlmUnavailable(Exception):
    pass


def _parse(system: str, user: str, schema: type[BaseModel]):
    """Jedno volání Gemini se strukturovaným výstupem; při chybě vyhodí LlmUnavailable."""
    from google import genai
    from google.genai import types
    from google.genai.errors import APIError
    from pydantic import ValidationError

    try:
        client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options=types.HttpOptions(timeout=LLM_TIMEOUT_MS),
        )
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
        )
        try:
            response = client.models.generate_content(model=MODEL, contents=user, config=config)
        except APIError as exc:
            if exc.code not in (403, 429, 500, 503):
                raise
            time.sleep(2)  # přetížení bývá krátké: jeden opakovaný pokus
            response = client.models.generate_content(model=MODEL, contents=user, config=config)
    except APIError as exc:
        raise LlmUnavailable(f"Model vrátil chybu {exc.code}.") from exc
    except Exception as exc:  # síť, časový limit
        raise LlmUnavailable(f"Model není dostupný ({type(exc).__name__}).") from exc
    if not response.text:
        raise LlmUnavailable("Model vrátil prázdnou odpověď.")
    try:
        return schema.model_validate_json(response.text)
    except ValidationError as exc:
        raise LlmUnavailable("Model nevrátil platný strukturovaný výstup.") from exc


# ---------- Claim Extractor ----------

class ExtractedClaim(BaseModel):
    quote: str
    rule_id: RuleId
    supplier_id: str = ""


class ExtractedClaims(BaseModel):
    claims: list[ExtractedClaim]


EXTRACTOR_SYSTEM = (
    "Jsi Claim Extractor v nástroji pro konkurenční zpravodajství o dodavatelích elektřiny. "
    "Z textu stránky vyber marketingová tvrzení o ceně, velikosti, zákaznících, zeleném původu "
    "nebo důvěryhodnosti dodavatele. Citát musí být doslovný, souvislý úsek textu stránky, bez úprav. "
    "Ke každému přiřaď právě jedno pravidlo: price_favorable (výhodná cena, úspora), price_rank "
    "(nejnižší nebo nejlepší cena), price_guarantee (garance nebo fixace ceny), market_size (největší "
    "dodavatel), customer_count (počet zákazníků), customer_growth (růst počtu zákazníků), "
    "price_pct_cheaper (o kolik procent levnější než jmenovaný konkurent), carbon_neutral "
    "(uhlíková neutralita, kompenzace emisí), green_share (podíl zelené elektřiny v procentech), "
    "green_generic (obecně zelené), soft_trust (férový, spolehlivý), other. Neposuzuj pravdivost. "
    "Vynech návody, recenze zákazníků, plyn a nabídky zařízení. Vrať nejvýše 6 nejvýraznějších tvrzení "
    "na jednoho dodavatele."
)

KEYWORD_RULES: list[tuple[str, str]] = [
    ("price_rank", r"nejlevn|nejnižší cen|nejlepší cen|bezkonkurenčn"),
    ("customer_count", r"\d[\d ,]*\s*(milion|mil\.|tis\.|tisíc)\w*\s+zákazník|\d[\d ]*\s*odběrných míst"),
    ("green_share", r"100\s*%.{0,60}(zelen|obnoviteln)"),
    ("market_size", r"\bnejvětší\w*.{0,40}dodavatel"),
    ("price_pct_cheaper", r"o \d+\s*% levněj"),
    ("carbon_neutral", r"uhlíkově neutráln|emise CO.{0,30}kompenz"),
    ("customer_growth", r"nejrychleji rost|\brosteme\b"),
    ("price_guarantee", r"garanc\w*.{0,30}cen|fixac\w*.{0,20}cen"),
    ("green_generic", r"zelen\w+ (elektřin|energi)|obnovitelných zdroj"),
    ("soft_trust", r"férov|spolehliv|důvěryhodn"),
    ("price_favorable", r"výhodn\w*.{0,25}(cen|elektřin)|ušetř\w*.{0,30}(elektřin|energi)"),
]


def _keyword_extract(lines: list[str]) -> list[dict]:
    found = []
    for line in lines:
        if not 25 <= len(line) <= 220 or len(line.split()) < 4 or line.endswith("?"):
            continue
        low = line.casefold()
        if "plyn" in low and "elektřin" not in low:
            continue
        if re.search(r"solár|fotovolt|čerpadl|kalkulač|najdete", low):
            continue
        for rule_id, pattern in KEYWORD_RULES:
            if re.search(pattern, line, re.I):
                found.append({"quote": line, "rule_id": rule_id})
                break
    return found


def _stems(text: str) -> set[str]:
    """Hrubé kmeny slov (prvních pět písmen), aby „Garance“ a „Garanci“ byly totéž."""
    return {w[:5] for w in re.findall(r"\w{4,}", text.casefold())}


def _checked_rule(rule_id: str, quote: str) -> str:
    """Pravidlo navržené modelem platí jen tehdy, když mu citát odpovídá i podle klíčových slov.

    Jinak se tvrzení zařadí jako `other` (bez verdiktu), aby chybná klasifikace modelu
    nevedla k verdiktu „podloženo“ nebo „v rozporu“ u tvrzení, které o tom vůbec není.
    """
    if rule_id == "other":
        return rule_id
    pattern = dict(KEYWORD_RULES).get(rule_id)
    return rule_id if pattern and re.search(pattern, quote, re.I) else "other"


def _accept(candidates: list[dict], page: dict, known_quotes: list[str]) -> list[dict]:
    """Ponechá jen citáty, které doslova existují ve staženém textu a nejsou duplicitní."""
    claims, seen = [], [normalize(q).casefold() for q in known_quotes]
    seen_stems = [_stems(q) for q in seen]
    for cand in candidates:
        quote = normalize(cand["quote"])
        key = quote.casefold()
        if not contains(page["text"], quote):
            continue
        if "plyn" in key and "elektřin" not in key:
            continue  # tvrzení jen o plynu nelze posuzovat cenami a daty o elektřině
        if any(key in s or s in key for s in seen):
            continue
        stems = _stems(key)
        if stems and any(stems <= known or (known and known <= stems) for known in seen_stems):
            continue  # stejné tvrzení v jiném tvaru nebo jeho zkrácená verze
        seen.append(key)
        seen_stems.append(stems)
        claims.append({"quote": quote, "rule_id": _checked_rule(cand["rule_id"], quote), "params": {}})
        if len(claims) >= MAX_NEW_CLAIMS:
            break
    return claims


def extract_claims(page: dict, known_quotes: list[str], use_llm: bool) -> tuple[list[dict], dict]:
    """Najde na jedné stránce další tvrzení mimo připravená. Vrací (tvrzení, informace o způsobu)."""
    result = extract_claims_batch({"x": page}, {"x": known_quotes}, use_llm)
    return result["x"]


def extract_claims_batch(
    pages: dict[str, dict], known: dict[str, list[str]], use_llm: bool
) -> dict[str, tuple[list[dict], dict]]:
    """Jedno volání modelu pro všechny stránky (bezplatná vrstva nesnáší souběžná volání)."""
    note, by_supplier = None, None
    if use_llm and pages:
        parts = []
        for sid, page in pages.items():
            text = "\n".join(page["lines"])
            if len(text) > MAX_PAGE_CHARS:
                text = text[:MAX_PAGE_CHARS]
                note = f"Některá stránka byla pro model zkrácena na {MAX_PAGE_CHARS} znaků."
            parts.append(f"### supplier_id: {sid}\n{text}")
        user = (
            "Níže jsou texty stránek několika dodavatelů. U každého tvrzení uveď supplier_id stránky, "
            "ze které citát pochází.\n\n" + "\n\n".join(parts)
        )
        try:
            parsed = _parse(EXTRACTOR_SYSTEM, user, ExtractedClaims)
            by_supplier = {sid: [] for sid in pages}
            only = next(iter(pages)) if len(pages) == 1 else None
            for claim in parsed.claims:
                sid = only or claim.supplier_id
                if sid in by_supplier:
                    by_supplier[sid].append(claim.model_dump())
        except LlmUnavailable as exc:
            note = f"{exc} Použita klíčová slova."
    out = {}
    for sid, page in pages.items():
        if by_supplier is not None:
            info = {"method": f"model {MODEL}", "note": note}
            candidates = by_supplier[sid]
        else:
            info = {"method": "klíčová slova", "note": note}
            candidates = _keyword_extract(page["lines"])
        out[sid] = (_accept(candidates, page, known.get(sid, [])), info)
    return out


# ---------- Strategist ----------

VERDICT_LABEL = {
    SUPPORTED: "podloženo",
    PARTIAL: "částečně podloženo",
    CONTRADICTED: "v rozporu s daty",
    UNVERIFIABLE: "nelze ověřit",
}
VERDICT_ORDER = {CONTRADICTED: 0, PARTIAL: 1, UNVERIFIABLE: 2, SUPPORTED: 3}


class Briefing(BaseModel):
    headline: str
    summary: str
    priority_claim_ids: list[str]


STRATEGIST_SYSTEM = (
    "Jsi Strategist. Píšeš česky krátký briefing pro manažera dodavatele elektřiny o tvrzeních konkurence. "
    "Vycházej výhradně z předaných výsledků a neměň verdikty. „Nelze ověřit“ neznamená nepravdivé. "
    "Nevynášej právní závěry. V textu neuváděj žádná čísla, ceny, lhůty ani data; ty doplní aplikace z výpočtu. "
    "headline je jedna věta, summary nejvýše čtyři věty. Do priority_claim_ids dej nejvýše tři id tvrzení "
    "přesně podle vstupu, seřazená podle toho, jak moc si zaslouží pozornost vedení."
)


def _template_briefing(items: list[dict]) -> dict:
    counts = {v: sum(1 for i in items if i["verdict"] == v) for v in VERDICT_ORDER}
    ranked = sorted(
        [i for i in items if i["opportunity"] and i.get("evaluated", True)],
        key=lambda i: (i["verdict"] != CONTRADICTED, -int(i["opportunity"]), not i["evidence"]),
    )[:3]
    if ranked:
        headline = f"{len(ranked)} tvrzení konkurence si zaslouží pozornost."
    else:
        headline = "V ověřených tvrzeních jsme nenašli příležitost k reakci."
    summary = (
        f"Ověřeno {len(items)} tvrzení: {counts[SUPPORTED]} podloženo, {counts[PARTIAL]} částečně, "
        f"{counts[CONTRADICTED]} v rozporu s daty, {counts[UNVERIFIABLE]} nelze ověřit dostupnými podklady. "
        "„Nelze ověřit“ neznamená, že je tvrzení nepravdivé."
    )
    matrix: dict[str, dict[str, int]] = {}
    for i in items:
        row = matrix.setdefault(i["rule_label"], {v: 0 for v in VERDICT_ORDER})
        row[i["verdict"]] += 1
    return {
        "headline": headline,
        "summary": summary,
        "matrix": matrix,
        "priorities": [
            {"claim_id": i["id"], "supplier": i["supplier"], "quote": i["quote"],
             "why": i["finding"], "next_step": i["action"]}
            for i in ranked
        ],
        "method": "šablona (bez modelu)",
    }


def write_briefing(items: list[dict], use_llm: bool) -> dict:
    base = _template_briefing(items)
    if not use_llm or not items:
        return base
    payload = [
        {k: i[k] for k in ("id", "supplier", "quote", "verdict", "finding", "limitation", "action", "opportunity")}
        for i in items
    ]
    try:
        parsed = _parse(STRATEGIST_SYSTEM, json.dumps(payload, ensure_ascii=False, indent=1), Briefing)
    except LlmUnavailable as exc:
        base["method"] = f"šablona ({exc})"
        return base
    by_id = {i["id"]: i for i in items}
    ids = parsed.priority_claim_ids[:3]
    if any(cid not in by_id for cid in ids):  # model smí odkazovat jen na existující tvrzení
        base["method"] = "šablona (model odkázal na neexistující tvrzení)"
        return base
    if re.search(r"\d", parsed.headline + parsed.summary):  # čísla smí do briefingu dát jen kód
        base["method"] = "šablona (model do textu vložil čísla)"
        return base
    return {
        "headline": parsed.headline,
        "summary": parsed.summary,
        "matrix": base["matrix"],
        "priorities": [
            {"claim_id": cid, "supplier": by_id[cid]["supplier"], "quote": by_id[cid]["quote"],
             "why": by_id[cid]["finding"], "next_step": by_id[cid]["action"]}
            for cid in ids
        ],
        "method": f"model {MODEL} vybral priority a napsal shrnutí; nálezy, čísla a verdikty jsou z pravidel v kódu",
    }


__all__ = ["extract_claims", "write_briefing", "llm_available", "VERDICT_LABEL", "RULE_LABELS"]
