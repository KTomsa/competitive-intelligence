"""Ručně posouzené případy (nezávislý „správný výsledek“ k reálným datům ve snapshotech).

Každý řádek posoudil člověk z citátu a zdroje dřív, než se zapsal do testu, a důvod je
uveden u případu. Test tak nekopíruje předpoklady implementace: když se pravidlo změní
a začne tvrdit víc, než dovolují podklady, tabulka to odhalí. Snapshoty: 13. 11. 2026.
"""
import pytest

from competitive_intelligence import workflow

CASES = [
    ("epet-1", "contradicted",
     "„Vždy tu nejlepší cenu“: ČEZ Fix na 3 roky má podle obou živě stažených ceníků (D02d, PRE) nižší "
     "cenu i stálý plat než epet FIX 36 GARANT, takže roční náklad je nižší při 2 i 4 MWh."),
    ("dobra-2", "supported",
     "„Více než 185 000 odběrných míst“ společně s epet: OTE 09/2026 dává 44 154 + 22 890 + 91 653 + 28 765 "
     "= 187 462 odběrných míst elektřiny a plynu; metrika, subjekty i komodity odpovídají."),
    ("cez-1", "unverifiable",
     "„Největší dodavatel elektřiny a plynu“ neříká, podle čeho; OTE eviduje jen odběrná místa. Podle nich "
     "je ČEZ v plynu druhý, ale tvrzení může myslet objem dodávky."),
    ("centropol-2", "unverifiable",
     "„Největší český dodavatel“: „český“ je otázka vlastnictví, které OTE neeviduje."),
    ("dobra-1", "unverifiable",
     "„Největší alternativní dodavatel“: pojem ani metrika nejsou definované; počty odběrných míst jsou kontext."),
    ("eon-2", "unverifiable",
     "„1,5 milionu zákazníků“: OTE eviduje odběrná místa, ne zákazníky; počet zákazníků z nich určit nelze."),
    ("spp-1", "unverifiable",
     "„1,5 milionu zákazníků v Česku a na Slovensku“: slovenskou část český zdroj nepokrývá."),
    ("pre-1", "unverifiable",
     "„Elektřina krytá zárukami původu“: jediný podklad je prohlášení dodavatele na téže stránce, ne nezávislý "
     "doklad o zárukách původu."),
    ("cez-2", "supported",
     "„Stabilně výhodné ceny“ vyloženo jako cena komodity nepřesahující pásmo ERÚ: všechny čtyři ceníky ČEZ "
     "(D02d, PRE) jsou pod pásmem pro svou fixaci a měsíc."),
    ("cez-3", "unverifiable",
     "„Nejdůvěryhodnější dodavatel“ nemá měřitelnou definici; rozhodnutí ERÚ je kontext, ne důkaz."),
    ("centropol-1", "unverifiable",
     "„Garance nejnižší ceny“ je závazek dorovnání, ne tvrzení, že ceník je nejnižší."),
]


@pytest.fixture(scope="module")
def claims():
    report = workflow.run(live=False)
    return {c["id"]: c for s in report["suppliers"] for c in s["claims"]}


@pytest.mark.parametrize("claim_id, expected, reason", CASES, ids=[c[0] for c in CASES])
def test_adjudicated_case(claims, claim_id, expected, reason):
    assert claims[claim_id]["verdict"] == expected, reason
