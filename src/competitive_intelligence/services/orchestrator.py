"""Fixed-order audit pipeline: collect -> extract -> verify -> judge -> report.

Stages hand each other typed Claim / Evidence / Verdict objects; there is no
free conversation between agents. Two intake modes share everything after
collection:

- fixture: fictional bundled data, fully offline;
- live: the Web Scout fetches the registered supplier pages.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..agents import FixtureEvidenceAgent
from ..registry import SupplierRegistry
from ..rules import apply_judge
from ..schemas import (
    AuditMode,
    AuditResult,
    AuditStatus,
    Briefing,
    BriefingItem,
    Claim,
    ClaimType,
    Evidence,
    Origin,
    Source,
    SupplierInfo,
    Verdict,
    VerdictValue,
    coerce_rule,
    quote_in_text,
)
from ..tools import ReferenceStore
from .fixtures import FixtureBundle
from .repository import AuditRepository, utc_now
from .web_scout import ScoutedSource, ScoutError, WebScout

VERDICT_LABELS = {
    VerdictValue.SUPPORTED: "podloženo",
    VerdictValue.PARTIALLY_SUPPORTED: "částečně podloženo",
    VerdictValue.CONTRADICTED: "v rozporu s daty",
    VerdictValue.UNVERIFIABLE: "nelze ověřit",
}

LIVE_SCOPE_NOTE = (
    "Živý sběr: stránky dodavatelů byly načteny při tomto běhu. Tvrzení se zatím přebírají jen "
    "ze schválené baseline; extrakce z nových nebo změněných stránek (LLM) a referenční data "
    "pro ověření ještě nejsou připojeny."
)


@dataclass
class PipelineContext:
    """Everything the worker needs to run an audit; built once per process."""

    bundle: FixtureBundle
    registry: SupplierRegistry
    scout: WebScout


def validate_claims(
    raw_claims: list[dict[str, Any]],
    sources: list[Source],
    texts: dict[str, str],
    *,
    human_reviewed: bool | None = None,
) -> tuple[list[Claim], list[str]]:
    """Turn claim candidates into validated Claims.

    The same three checks guard every extractor, fixture or LLM: the closed
    rule catalogue, schema validation and the literal-quote test against the
    captured page text. A candidate that fails is dropped and reported.
    """
    by_id = {s.source_id: s for s in sources}
    claims: list[Claim] = []
    dropped: list[str] = []
    for raw in raw_claims:
        source = by_id.get(raw.get("source_id", ""))
        if source is None:
            continue  # claim belongs to a source outside this audit
        claim_id = raw.get("id", "?")
        try:
            candidate = {
                **raw,
                # Unknown IDs become `other`; a known ID with bad params raises here.
                "rule": coerce_rule(raw.get("rule")),
                "supplier_id": source.supplier_id,
                "source_url": source.source_url,
                "captured_at": source.captured_at,
                "origin": source.origin,
            }
            if human_reviewed is not None:
                candidate["human_reviewed"] = human_reviewed
            claim = Claim.model_validate(candidate)
        except ValidationError:
            dropped.append(f"Tvrzení {claim_id} zahozeno: neplatná struktura nebo parametry pravidla.")
            continue
        if not quote_in_text(claim.quote, texts[source.source_id]):
            dropped.append(f"Tvrzení {claim_id} zahozeno: citace není doslova v textu stránky.")
            continue
        claims.append(claim)
    return claims, dropped


def build_briefing(claims: list[Claim], verdicts: list[Verdict]) -> Briefing:
    by_claim = {c.id: c for c in claims}
    matrix: dict[ClaimType, dict[VerdictValue, int]] = {}
    totals: Counter[VerdictValue] = Counter()
    items: list[BriefingItem] = []
    for verdict in verdicts:
        claim = by_claim[verdict.claim_id]
        row = matrix.setdefault(claim.claim_type, {})
        row[verdict.verdict] = row.get(verdict.verdict, 0) + 1
        totals[verdict.verdict] += 1
        if verdict.verdict is VerdictValue.CONTRADICTED:
            text = (
                f"Tvrzení „{claim.quote}“ je v rozporu s dostupnými daty. Může jít o příležitost "
                "k vymezení, pokud vlastní nabídka ve stejném srovnání obstojí; to je potřeba ověřit."
            )
        elif verdict.possible_regulatory_risk:
            text = (
                f"Tvrzení „{claim.quote}“ nelze ověřit a nese možné regulatorní riziko. "
                "Doporučeno předat k právní kontrole; nejde o právní závěr."
            )
        elif verdict.judge_changed:
            text = (
                f"U tvrzení „{claim.quote}“ důkaz neodpovídá přesně tomu, co tvrzení říká. "
                "Před jakýmkoli použitím je nutná ruční kontrola."
            )
        else:
            continue
        items.append(
            BriefingItem(
                claim_id=claim.id, supplier_id=claim.supplier_id, verdict=verdict.verdict, text=text
            )
        )
    counts = ", ".join(f"{VERDICT_LABELS[v]}: {totals[v]}" for v in VerdictValue if totals[v])
    summary = (
        f"Posouzeno {len(verdicts)} tvrzení ({counts}). "
        "Chybějící důkaz není důkazem nepravdivosti; přehled není žebříčkem dodavatelů."
        if verdicts
        else "V tomto běhu nebylo posouzeno žádné tvrzení."
    )
    return Briefing(summary=summary, items=items, matrix=matrix)


def _verify_and_report(
    repo: AuditRepository,
    audit_id: str,
    *,
    mode: AuditMode,
    scope_note: str,
    suppliers: list[SupplierInfo],
    sources: list[Source],
    claims: list[Claim],
    store: ReferenceStore,
    sample_supplier_ids: list[str],
    judge_flags: dict[str, str],
) -> AuditResult:
    request = repo.get_request(audit_id)

    repo.set_stage(audit_id, AuditStatus.VERIFYING, "evidence_agents")
    agent = FixtureEvidenceAgent(store, sample_supplier_ids)
    evidence: list[Evidence] = []
    outcomes = {}
    for claim in claims:
        facts, outcome = agent.gather(claim, request.profile_ids)
        outcomes[claim.id] = outcome
        evidence.append(
            Evidence(
                claim_id=claim.id,
                facts=facts,
                rule_result=outcome.rule_verdict,
                rule_version=outcome.rule_version,
                limitations=list(outcome.limitations),
            )
        )
    if evidence:
        repo.add_event(
            audit_id,
            "evidence_agents",
            f"Ověřeno {len(evidence)} tvrzení proti referenčním datům; verdikty spočítal kód.",
        )

    repo.set_stage(audit_id, AuditStatus.JUDGING, "judge")
    verdicts: list[Verdict] = []
    for item in evidence:
        verdict = apply_judge(
            claim_id=item.claim_id,
            outcome=outcomes[item.claim_id],
            evidence_ids=[f.evidence_id for f in item.facts],
            mismatch_reason=judge_flags.get(item.claim_id),
        )
        verdicts.append(verdict)
        if verdict.judge_changed:
            repo.add_event(
                audit_id,
                "judge",
                f"{item.claim_id}: důkaz neodpovídá tvrzení, verdikt změněn na „nelze ověřit“.",
                level="warning",
            )

    repo.set_stage(audit_id, AuditStatus.REPORTING, "strategist")
    briefing = build_briefing(claims, verdicts)
    repo.add_event(audit_id, "strategist", "Briefing sestaven z uložených verdiktů.")

    result = AuditResult(
        audit_id=audit_id,
        mode=mode,
        scope_note=scope_note,
        suppliers=suppliers,
        profile_ids=request.profile_ids,
        sources=sources,
        claims=claims,
        evidence=evidence,
        verdicts=verdicts,
        briefing=briefing,
        warnings=repo.get_status(audit_id).warnings,
        completed_at=utc_now(),
    )
    repo.complete(audit_id, result)
    return result


def run_fixture_audit(repo: AuditRepository, audit_id: str, bundle: FixtureBundle) -> AuditResult:
    request = repo.get_request(audit_id)
    wanted = set(request.supplier_ids)

    repo.set_stage(audit_id, AuditStatus.COLLECTING, "web_scout")
    sources = [s for s in bundle.sources if s.supplier_id in wanted]
    for source in sources:
        label = {
            Origin.LIVE: "načteno živě",
            Origin.BASELINE: "web beze změny, použita schválená baseline",
            Origin.SNAPSHOT: f"web nedostupný ({source.fallback_reason}), použit snapshot",
        }[source.origin]
        repo.add_event(
            audit_id,
            "web_scout",
            f"{source.supplier_id}: {label} (ukázková data).",
            level="warning" if source.origin is Origin.SNAPSHOT else "info",
            origin=source.origin,
        )

    repo.set_stage(audit_id, AuditStatus.EXTRACTING, "claim_extractor")
    claims, dropped = validate_claims(bundle.raw_claims, sources, bundle.canonical_texts)
    repo.add_event(audit_id, "claim_extractor", f"Nalezeno {len(claims)} tvrzení s doslovnou citací.")
    for message in dropped:
        repo.add_event(audit_id, "claim_extractor", message, level="warning")

    return _verify_and_report(
        repo,
        audit_id,
        mode=AuditMode.FIXTURE,
        scope_note=bundle.notice,
        suppliers=[s for s in bundle.suppliers if s.supplier_id in wanted],
        sources=sources,
        claims=claims,
        store=ReferenceStore(bundle.reference, bundle.profiles),
        sample_supplier_ids=bundle.supplier_ids,
        judge_flags=bundle.judge_flags,
    )


def run_live_audit(repo: AuditRepository, audit_id: str, ctx: PipelineContext) -> AuditResult | None:
    request = repo.get_request(audit_id)

    repo.set_stage(audit_id, AuditStatus.COLLECTING, "web_scout")
    jobs = [
        (supplier_id, page)
        for supplier_id in request.supplier_ids
        for page in ctx.registry.get(supplier_id).pages
    ]
    # A handful of independent page fetches; all are joined before the stage ends.
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda job: ctx.scout.collect(*job), jobs))

    scouted: list[ScoutedSource] = []
    for outcome in outcomes:
        if isinstance(outcome, ScoutError):
            repo.add_event(
                audit_id,
                "web_scout",
                f"{outcome.source_id}: web se nepodařilo načíst ({outcome.reason}) a není k dispozici "
                "žádný snapshot. Zdroj nebyl posouzen.",
                level="warning",
            )
            continue
        scouted.append(outcome)
        source = outcome.source
        size = f"{len(outcome.text)} znaků textu"
        if source.origin is Origin.SNAPSHOT:
            repo.add_event(
                audit_id,
                "web_scout",
                f"{source.source_id}: web se nepodařilo načíst ({source.fallback_reason}), použit "
                f"snapshot z {source.captured_at.date().isoformat()} ({size}).",
                level="warning",
                origin=source.origin,
            )
        else:
            label = (
                "web načten, beze změny proti schválené baseline"
                if source.origin is Origin.BASELINE
                else "web načten živě"
            )
            repo.add_event(audit_id, "web_scout", f"{source.source_id}: {label} ({size}).", origin=source.origin)

    if not scouted:
        repo.fail(
            audit_id,
            "SOURCE_UNAVAILABLE",
            "Žádný zdroj se nepodařilo načíst a není k dispozici žádný snapshot.",
        )
        return None

    repo.set_stage(audit_id, AuditStatus.EXTRACTING, "claim_extractor")
    sources = [s.source for s in scouted]
    texts = {s.source.source_id: s.text for s in scouted}
    raw_claims = [claim for s in scouted for claim in s.baseline_claims or []]
    claims, dropped = validate_claims(raw_claims, sources, texts, human_reviewed=True)
    if claims:
        repo.add_event(
            audit_id, "claim_extractor", f"Převzato {len(claims)} tvrzení ze schválené baseline."
        )
    for message in dropped:
        repo.add_event(audit_id, "claim_extractor", message, level="warning")
    without_baseline = [s.source.source_id for s in scouted if s.baseline_claims is None]
    if without_baseline:
        repo.add_event(
            audit_id,
            "claim_extractor",
            f"{len(without_baseline)} stránek je nových nebo změněných. Extrakce tvrzení (LLM) zatím "
            "není připojena, tyto stránky proto nemají žádná posouzená tvrzení.",
            level="warning",
        )

    return _verify_and_report(
        repo,
        audit_id,
        mode=AuditMode.LIVE,
        scope_note=LIVE_SCOPE_NOTE,
        suppliers=ctx.registry.infos(request.supplier_ids),
        sources=sources,
        claims=claims,
        # No real reference data is connected yet, so every rule abstains.
        store=ReferenceStore({}, ctx.bundle.profiles),
        sample_supplier_ids=request.supplier_ids,
        judge_flags={},
    )
