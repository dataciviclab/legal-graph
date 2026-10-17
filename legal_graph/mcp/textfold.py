"""Normalizzazione testo per la search: fold accenti, stem, glossario EN→IT."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def _fold(text: str) -> str:
    """Minuscole + rimozione accenti (responsabilità → responsabilita)."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


_STOP = frozenset(
    {
        "il", "lo", "la", "le", "gli", "i", "di", "del", "dello", "della", "dei",
        "degli", "delle", "e", "ed", "o", "in", "su", "al", "allo", "alla", "ai",
        "agli", "alle", "con", "per", "che", "nel", "nello", "nella", "nei",
        "negli", "nelle", "un", "una", "uno", "come", "dove", "quando", "cui",
        "the", "of", "and", "for", "on", "da", "dalla",
        "non", "esiste", "questo", "que", "anche", "altre", "solo", "molto",
        "dopo", "prima", "oltre", "circa", "quale", "quali", "qualcuno",
        "essere", "sono", "ave", "aveva", "sara", "sarebbe",
    }
)

_EN_IT_GLOSSARY: dict[str, list[str]] = {
    "whistleblowing": ["segnalazione", "segnalazioni", "whistleblow", "2019/1937"],
    "whistleblower": ["segnalazione", "segnalazioni"],
    "ombudsman": ["garante", "difensore"],
    "data": ["dati", "personali"],
    # Lab themes → ancoraggi a mart (non FTS)
    "anticorruzione": ["corruzione", "190/2012", "231/2001", "anac"],
    "anticorrupt": ["corruzione", "190/2012", "231/2001"],
    "trasparenza": ["accesso civico", "33/2013", "pubblicazione"],
    "foia": ["accesso civico", "trasparenza", "33/2011", "accesso agli atti"],
    "open data": ["dati aperti", "accesso civico"],
    "gdpr": ["protezione dati", "privacy", "101/2018"],
    "privacy": ["protezione dati", "196/2003", "101/2018"],
}

# Prefissi stabili per ridurre rumore su match parziale
_STEM_SUFFIX = (
    "amentalmente", "amentalita", "amentalita", "amentalmente",
    "abilita", "abilita", "abilita",
    "azione", "azioni", "atore", "atori", "atrici", "atura", "ature",
    "ibile", "ibili", "ibile",
    "ista", "iste", "isti", "iste", "istica", "istico", "istiche",
    "mente", "menti", "mento", "menti",
    "zione", "zioni", "zionale", "zionali",
    "ciale", "ciali", "ciale",
    "tore", "tori", "trice", "trici", "trici",
    "essa", "esse", "essi", "esso", "essere", "essere",
    "are", "are", "are", "ire", "ire", "ere", "ere",
    "ato", "ata", "ati", "ate", "ato",
    "ito", "ita", "iti", "ite", "ito",
    "uto", "uta", "uti", "ute", "uto",
    "ale", "ali", "ale", "ale",
    "ico", "ica", "ici", "ice", "ico",
    "oso", "osa", "osi", "ose", "oso",
    "ibile", "ibili",
    "ita", "ite", "ati", "ate",
    "ato", "ata",
    "ivo", "iva", "ivi", "ive",
    "euro", "euro",
)


def _stem_prefix(term: str) -> str:
    """Prefisso comune italiano per LIKE (responsabilita → responsabilit)."""
    if len(term) <= 4:
        return term
    for suf in sorted(_STEM_SUFFIX, key=len, reverse=True):
        if term.endswith(suf) and len(term) - len(suf) >= 4:
            return term[: -len(suf)]
    if term.endswith(("a", "e", "o")):
        return term[:-1]
    return term


def _parse_ecli_or_sentenza(folded: str) -> dict[str, Any] | None:
    """Riconosce 'sentenza 2009 151', 'ECLI:IT:COST:2009:151', 'corte cost 2009'."""
    ecli = re.search(r"ecli[:\s]*it[:\s]*cost[:\s]*(\d{4})[:\s]*(\d{1,4})", folded)
    if ecli:
        return {"year": ecli.group(1), "num": ecli.group(2), "ecli": True}
    sent = re.search(
        r"(?:sentenza|ordinanza|pronuncia|corte\s+costituzionale|corte\s+cost)"
        r".*?(\d{4})\D{0,8}(\d{1,4})",
        folded,
    )
    if sent:
        return {"year": sent.group(1), "num": sent.group(2), "ecli": False}
    # num-first: "sentenza 316 2004 corte costituzionale" (anno dopo il numero)
    sent_rev = re.search(
        r"(?:sentenza|ordinanza|pronuncia|corte\s+costituzionale|corte\s+cost)"
        r".*?(\d{1,4})\D{0,8}(\d{4})",
        folded,
    )
    if sent_rev:
        return {"year": sent_rev.group(2), "num": sent_rev.group(1), "ecli": False}
    year_only = re.search(
        r"(?:sentenza|ordinanza|pronuncia|corte\s+costituzionale|corte\s+cost)"
        r".*?(\d{4})",
        folded,
    )
    if year_only:
        return {"year": year_only.group(1), "num": None, "ecli": True}
    if re.search(
        r"sentenz|ordinanz|pronunc|ecli|corte\s+cost|giurisprudenz",
        folded,
    ):
        return {"year": None, "num": None, "ecli": True}
    return None


def _parse_costituzione(folded: str) -> dict[str, Any] | None:
    """'art. 3 della costituzione', 'costituzione', 'articolo 13 cost'.

    Anche bare 'articolo N' / 'art. N' senza keyword 'Costituzione',
    purché non ci sia un segnale di atto normativo (legge/decreto/…):
    altrimenti il numero finisce nell'intent numero e restituisce atti a caso.
    """
    if re.search(r"corte\s+cost|sentenza|ordinanza|pronuncia", folded):
        return None
    art = re.search(r"(?:art\.?|articolo)\s*(\d{1,3})", folded)
    if re.search(r"costituzion", folded):
        return {"art": art.group(1) if art else None}
    # bare articolo: solo se nessun tipo di atto menzionato
    if art and not re.search(
        r"decreto|dlgs|d\.lgs|regio|\bdl\b|legge|dpr|circolare|direttiva",
        folded,
    ):
        return {"art": art.group(1)}
    return None


def _parse_date_query(folded: str) -> dict[str, str] | None:
    """'8 giugno 2001' → data ISO o anno+giorno."""
    months = {
        "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
        "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9,
        "ottobre": 10, "novembre": 11, "dicembre": 12,
        "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6,
        "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12,
    }
    m = re.search(
        r"(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|"
        r"settembre|ottobre|novembre|dicembre|gen|feb|mar|apr|mag|giu|lug|ago|"
        r"set|ott|nov|dic)\s+(\d{4})",
        folded,
    )
    if not m:
        return None
    day, mon, year = int(m.group(1)), months[m.group(2)], m.group(3)
    return {"date": f"{year}-{mon:02d}-{day:02d}", "year": year}
