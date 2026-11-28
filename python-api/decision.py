"""Decision intelligence layered on a finished search.

Scientists do not act on raw table rows; they act on verdicts. This module
turns the graph a search already produced (nodes.csv / edges.csv) into the
four deliverables that justify paying for the tool:

  * Compound Feasibility Verdict — an A–F efficacy grade from aggregated
    affinity values, plus IP freedom status from the patent nodes.
  * Dark Literature — prior-art overlap between compounds and patents, the
    proxy for "this scaffold was tried and abandoned".
  * Mechanism-of-Action Storyboard — the evidence blocks (targets, PDB
    structures, potency, patents, publications) assembled into a narrative.
  * Vendor Sourcing — which compounds can be bought, linked to PubChem's
    vendor registry.

Everything here is computed offline from the export files, so it is instant,
deterministic, and works on ephemeral free-tier containers. Where real
verdicts would need external data the engine does not have (EPA CompTox
mutagenicity screens, full patent claim text), the field says so explicitly
rather than inventing a number — a fabricated toxicity flag is worse than
no flag. The graph is the only input.
"""

from __future__ import annotations

import csv
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

# Grade bands on the pChEMBL scale (-log10 molar), the convention ChEMBL
# itself uses: 9.0 is sub-nanomolar territory, 6.0 is around 1 uM.
GRADE_BANDS: List[Tuple[float, str, str]] = [
    (9.0, "A", "potent"),
    (8.0, "B", "strong"),
    (7.0, "C", "moderate"),
    (6.0, "D", "weak"),
    (0.0, "F", "negligible"),
]

# A patent filed more than PATENT_TERM_YEARS years ago cannot carry an
# unexpired 20-year protection term, regardless of family extensions.
PATENT_TERM_YEARS = 20
# Protection expiring within this window is "expiring soon" (yellow).
EXPIRY_WINDOW_YEARS = 2

# How many compounds to give individual verdicts for, worst excluded: the
# response is read by a human scanning one screen.
MAX_COMPOUND_VERDICTS = 8

_YEAR_RE = re.compile(r"(19|20)\d{2}")
_COMPOUND_PREFIXES = ("COMPOUND:", "LIGAND:", "CHEMDOC:")
_PATENT_PREFIXES = ("PATENT:",)

# A patent *document* identifier: two-letter office code, an optional letter
# series, at least four digits, and an optional kind code — US5444444,
# US20190021252A1, US-2019-0021252-A1, EP0248548B1, WO2024111111, and the US
# series that share the office code with a letter: reissues (USRE45123E1),
# designs (USD123456S1), plant patents (USPP12345P2), statutory invention
# registrations (USH1234H1) and defensive publications (UST123456I4).
#
# The series letters are listed rather than opened up to any letters, so a text
# value that merely starts with an office code and ends in a year ("EGFR2024")
# is not mistaken for a publication number.
#
# This is what separates a real patent row from the "Patent Search: <query>"
# link entity the connector emits when it finds no document. That entity is
# typed PATENT as well, so without this check it was listed, counted and even
# used to colour the IP verdict.
# The series letters include Japan's era prefixes (S = Showa, H = Heisei,
# R = Reiwa): JP-S5534457-A is a real 1980 publication, and without S those
# documents were dropped from the register while still counted as prior art.
_PATENT_DOCUMENT_RE = re.compile(r"^[A-Z]{2}(?:RE|PP|D|T|H|S|R)?\d{4,}(?:[A-Z]\d*)?$")
# Non-document entities: a search link, not a patent.
_PATENT_PLACEHOLDER_PREFIXES = ("PATENTSEARCH:", "SEARCH:")


GOOGLE_PATENTS_DOCUMENT = "https://patents.google.com/patent/"
GOOGLE_PATENTS_SEARCH = "https://patents.google.com/?q="


def _now_year() -> int:
    return datetime.now(timezone.utc).year


def canonical_patent_number(number: str) -> str:
    """The publication id Google Patents resolves, or an empty string.

    Sources record numbers with their own punctuation (PubChem says
    ``US-20190021252-A1``), while Google Patents addresses the document by the
    same id with the punctuation removed (``US20190021252A1``). Pasting the
    punctuated value into the document path produced a link that landed on "no
    results", which is what "the patent links do not open" was.
    """
    compact = re.sub(r"[^A-Z0-9]", "", (number or "").upper())
    if not compact:
        return ""
    for prefix in _PATENT_PLACEHOLDER_PREFIXES:
        if compact.startswith(prefix.replace(":", "")):
            return ""
    if compact.startswith("PATENT"):
        compact = compact[len("PATENT") :]
    return compact if _PATENT_DOCUMENT_RE.match(compact) else ""


def google_patents_url(number: str, recorded: str = "") -> str:
    """A patent link that always goes somewhere useful.

    A recorded URL is kept only when it is already a document URL. A search
    URL, a placeholder or an empty cell is replaced by the canonical document
    link; a number that cannot be parsed falls back to a patent search, so a row
    never renders a link that goes nowhere.
    """
    recorded = (recorded or "").strip()
    if recorded.startswith(GOOGLE_PATENTS_DOCUMENT):
        # Rebuild it rather than trusting it: the recorded form can still be
        # punctuated, and that is not a document URL.
        tail = recorded[len(GOOGLE_PATENTS_DOCUMENT) :].split("?", 1)[0]
        from_recorded = canonical_patent_number(tail)
        if from_recorded:
            return f"{GOOGLE_PATENTS_DOCUMENT}{from_recorded}"
    canonical = canonical_patent_number(number)
    if canonical:
        return f"{GOOGLE_PATENTS_DOCUMENT}{canonical}"
    query = (number or "").strip()
    if not query:
        return GOOGLE_PATENTS_SEARCH.rstrip("?")
    return f"{GOOGLE_PATENTS_SEARCH}{quote(query)}"


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _row_uid(row: Dict[str, str]) -> str:
    return (row.get("uid:ID") or row.get("uid") or "").strip()


def _has_prefix(uid: str, prefixes: Tuple[str, ...]) -> bool:
    upper = uid.upper()
    return any(upper.startswith(p) for p in prefixes)


def _is_patent_document(uid: str, number: str) -> bool:
    """True only for a row that identifies a patent document.

    A patent row must carry a publication number. The connector's fallback
    entity ("Patent Search: EGFR", canonical_id "SEARCH:EGFR") is a useful
    link but it is not a patent, and treating it as one both inflates the
    register and makes the IP verdict claim that "patents exist".
    """
    if _has_prefix(uid, _PATENT_PLACEHOLDER_PREFIXES):
        return False
    if _has_prefix(number, _PATENT_PLACEHOLDER_PREFIXES):
        return False
    normalised = re.sub(r"[^A-Z0-9]", "", (number or "").upper())
    return bool(_PATENT_DOCUMENT_RE.match(normalised))


def _parse_number(value: str) -> Optional[float]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


# Affinity measurements the grade may be built from. Other relation attributes
# (a HAS_STRUCTURE "9.1", a confidence score) are not potency and are ignored.
_AFFINITY_KINDS = ("IC50", "KI", "EC50", "POTENCY", "KD", "AC50")

# Concentration unit -> molar factor. Connectors report affinity values in
# their own units (BindingDB and BRENDA in nM, PDBbind sometimes none at all),
# and a raw measurement is not a pChEMBL value until it is converted.
_UNIT_TO_MOLAR = {
    "M": 1.0,
    "MM": 1e-3,
    "UM": 1e-6,
    "NM": 1e-9,
    "PM": 1e-12,
    "FM": 1e-15,
}

# pChEMBL is -log10(molar), so a real value sits roughly in 0–14 (1 M to
# 1e-14 M). Anything outside that window is a raw measurement that leaked into
# the column, not a potency score, and must not drive a grade.
_PCHEMBL_MIN = 0.0
_PCHEMBL_MAX = 14.0


def _valid_pchembl(value: Optional[float]) -> Optional[float]:
    if value is None or not (_PCHEMBL_MIN < value <= _PCHEMBL_MAX):
        return None
    return value


def _pchembl_value(edge: Dict[str, str]) -> Optional[float]:
    """One affinity edge on the pChEMBL scale, or None if it cannot be placed.

    The recorded `pchembl_value` is preferred. Many connectors never supply it
    (BindingDB and PDBbind report only activity_value + units, ChEMBL sometimes
    omits it), so the fallback converts the measurement: 2.1 nM is pChEMBL 8.68.
    The old fallback used the raw number, which made 19,490,000 nM read as
    "potency 19490000, grade A" and median pChEMBL 4994 — the units were being
    ignored, not the potency. A value with no usable units is skipped rather
    than guessed at.
    """
    recorded = _valid_pchembl(_parse_number(edge.get("pchembl_value", "")))
    if recorded is not None:
        return recorded

    kind = (edge.get("activity_type") or "").strip().upper().replace(" ", "")
    value = _parse_number(edge.get("activity_value", ""))
    if value is None:
        return None
    # GtoPdb labels the parameter pKi/pIC50 and already stores the log value.
    if kind.startswith("P") and kind[1:] in _AFFINITY_KINDS:
        return _valid_pchembl(value)
    if kind not in _AFFINITY_KINDS:
        return None
    unit = (
        (edge.get("units") or "")
        .strip()
        .replace("µ", "u")
        .replace("μ", "u")
        .upper()
    )
    factor = _UNIT_TO_MOLAR.get(unit)
    if factor is None:
        return None
    molar = value * factor
    if molar <= 0:
        return None
    return _valid_pchembl(round(-math.log10(molar), 2))


def _label(value: str) -> str:
    """nodes.csv :LABEL values look like 'Entity;Compound'."""
    parts = [p.strip().upper() for p in (value or "").split(";")]
    return parts[-1] if parts else ""


def _grade_for(score: float) -> Tuple[str, str]:
    for floor, grade, word in GRADE_BANDS:
        if score >= floor:
            return grade, word
    return "F", "negligible"  # pragma: no cover - bands end at 0.0


# ── 1. Feasibility verdict ───────────────────────────────────────────────────


def _affinity_edges(edges: List[Dict[str, str]]) -> Dict[str, List[float]]:
    """Compound uid -> every affinity value (pChEMBL scale) attached to it.

    Every value is normalised through `_pchembl_value`, so a recorded ChEMBL
    pChEMBL and a BindingDB nanomolar IC50 land on the same scale. A bare number
    on a relation like HAS_STRUCTURE is not an affinity and never reaches the
    grade.
    """
    by_compound: Dict[str, List[float]] = {}
    for edge in edges:
        src = (edge.get(":START_ID") or "").strip()
        if not _has_prefix(src, _COMPOUND_PREFIXES):
            continue
        pchembl = _pchembl_value(edge)
        if pchembl is None:
            continue
        by_compound.setdefault(src, []).append(pchembl)
    return by_compound


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def feasibility_verdict(
    nodes: List[Dict[str, str]], edges: List[Dict[str, str]]
) -> Dict[str, Any]:
    """Aggregate affinity values into one grade for the graph and per compound."""
    by_compound = _affinity_edges(edges)
    names = {
        _row_uid(row): (row.get("name") or _row_uid(row)).strip()
        for row in nodes
    }

    all_values = [v for vals in by_compound.values() for v in vals]
    if not all_values:
        return {
            "grade": "N/A",
            "label": None,
            "scored_compounds": 0,
            "affinity_datapoints": 0,
            "method": "pchembl_median",
            "compounds": [],
            "note": (
                "No quantitative affinity values (pChEMBL, IC50, Ki) surfaced "
                "in this graph, so no potency grade can be computed."
            ),
        }

    median = _median(all_values)
    grade, word = _grade_for(median)

    compounds = []
    for uid, vals in by_compound.items():
        score = _median(vals)
        c_grade, c_word = _grade_for(score)
        compounds.append(
            {
                "uid": uid,
                "name": names.get(uid, uid),
                "grade": c_grade,
                "label": c_word,
                "potency_score": round(score, 2),
                "datapoints": len(vals),
                "best_value": round(max(vals), 2),
            }
        )
    compounds.sort(key=lambda c: (-c["potency_score"], c["name"]))
    compounds = compounds[:MAX_COMPOUND_VERDICTS]

    return {
        "grade": grade,
        "label": word,
        "scored_compounds": len(by_compound),
        "affinity_datapoints": len(all_values),
        "median_pchembl": round(median, 2),
        "method": "pchembl_median",
        "compounds": compounds,
        "note": (
            "Grade is the median pChEMBL across every affinity datapoint in "
            "the graph: A >= 9.0, B >= 8.0, C >= 7.0, D >= 6.0, F below. "
            "IC50/Ki/Kd measurements reported in nM, uM or mM are converted "
            "to pChEMBL (-log10 molar) before ranking."
        ),
    }


# ── IP freedom ───────────────────────────────────────────────────────────────


def _patent_year(*texts: str) -> Optional[int]:
    """Earliest explicit year in a patent's identifiers/synonyms.

    Patent uids and names embed their publication year (US8492381B2-2013,
    WO2014/123456), which is what the connectors surface. Family and
    priority dates are not present in the graph, so the publication year is
    the honest — conservative — proxy for filing age.
    """
    years = []
    for text in texts:
        for match in _YEAR_RE.finditer(text or ""):
            year = int(match.group(0))
            if 1980 <= year <= _now_year():
                years.append(year)
    return min(years) if years else None


def ip_status(
    nodes: List[Dict[str, str]],
    edges: List[Dict[str, str]],
    now_year: Optional[int] = None,
) -> Dict[str, Any]:
    """Green / Yellow / Red from the patent nodes the search surfaced."""
    year = now_year or _now_year()
    patents: List[Dict[str, Any]] = []
    for row in nodes:
        uid = _row_uid(row)
        label = _label(row.get(":LABEL", ""))
        if not (_has_prefix(uid, _PATENT_PREFIXES) or label == "PATENT"):
            continue
        number = (
            row.get("patent_number")
            or row.get("canonical_id")
            or uid.split(":")[-1]
        ).strip()
        if not _is_patent_document(uid, number):
            continue
        pub_year = _patent_year(
            uid, row.get("name", ""), row.get("canonical_id", ""), row.get("synonyms", "")
        )
        patents.append({"uid": uid, "name": row.get("name", uid), "year": pub_year})

    scope = (
        "Patent documents retrieved by this search (PubChem patent cross-references "
        "and Google Patents). Legal status, claims, patent families and "
        "jurisdictions were not checked, so this is a signal, not an FTO opinion."
    )
    assessed_at = datetime.now(timezone.utc).date().isoformat()

    if not patents:
        # Zero records is the most common outcome of a name lookup and the most
        # dangerous answer to dress in green. It is reported as an absence of
        # data, never as freedom to operate.
        return {
            "status": "insufficient_data",
            "reason": (
                "No patent document was retrieved for this query, so there is no "
                "patent signal either way. This is an absence of records in the "
                "sources searched, not evidence that no patent exists."
            ),
            "patents": [],
            "scope": scope,
            "assessed_at": assessed_at,
            "caveat": (
                "Absence of a patent in this graph is not a freedom-to-operate "
                "opinion. Search Espacenet, EPO OPS or a patent attorney before "
                "relying on it."
            ),
        }

    dated = [p for p in patents if p["year"] is not None]
    if not dated:
        return {
            "status": "yellow",
            "reason": "Patents exist but their years could not be parsed from identifiers.",
            "patents": patents,
            "scope": scope,
            "assessed_at": assessed_at,
            "caveat": "Verify protection status manually before relying on this.",
        }

    latest = max(p["year"] for p in dated)
    remaining = latest + PATENT_TERM_YEARS - year
    if remaining <= 0:
        status = "green"
        reason = (
            f"Newest patent found was published in {latest}; a {PATENT_TERM_YEARS}-year "
            "term counted from that publication year has lapsed. Legal status was "
            "not verified."
        )
    elif remaining <= EXPIRY_WINDOW_YEARS:
        status = "yellow"
        reason = (
            f"Newest patent found was published in {latest}; protection lapses "
            f"around {latest + PATENT_TERM_YEARS}."
        )
    else:
        status = "red"
        reason = (
            f"Newest patent found was published in {latest}; protection plausibly "
            f"runs to {latest + PATENT_TERM_YEARS}."
        )

    return {
        "status": status,
        "reason": reason,
        "patents": patents,
        "scope": scope,
        "assessed_at": assessed_at,
        "caveat": (
            "Publication year stands in for the filing date and legal status was "
            "not checked; patent families, term adjustments and lapses for "
            "non-payment can shift real expiry by years."
        ),
    }


# ── 2. Dark literature ───────────────────────────────────────────────────────


def dark_literature(nodes: List[Dict[str, str]], edges: List[Dict[str, str]]) -> Dict[str, Any]:
    """Prior-art overlap: compounds in this graph that the patent literature touched.

    The engine reads structured patent metadata, not claim text — so "abandoned
    due to microsomal clearance" cannot be asserted here. What can be asserted,
    from the graph alone: how many distinct compounds link to how many patents,
    and where multiple compounds of the same query share prior art. That overlap
    is the actionable warning: the scaffold space was already worked.
    """
    compound_names = {
        _row_uid(row): (row.get("name") or _row_uid(row)).strip()
        for row in nodes
        if _has_prefix(_row_uid(row), _COMPOUND_PREFIXES)
    }

    patent_ids: Dict[str, Dict[str, Any]] = {}
    links: List[Tuple[str, str]] = []
    for edge in edges:
        rel = (edge.get(":TYPE") or "").strip().upper()
        src = (edge.get(":START_ID") or "").strip()
        tgt = (edge.get(":END_ID") or "").strip()
        if rel not in ("PATENTED_IN", "PATENTED_FOR", "DESCRIBED_IN") or not _has_prefix(
            tgt, _PATENT_PREFIXES
        ):
            continue
        if _has_prefix(src, _COMPOUND_PREFIXES):
            links.append((src, tgt))
            patent_ids.setdefault(tgt, {"uid": tgt})

    by_compound: Dict[str, List[str]] = {}
    by_patent: Dict[str, set] = {}
    for src, tgt in links:
        by_compound.setdefault(src, []).append(tgt)
        by_patent.setdefault(tgt, set()).add(src)

    # "Overlap" is prior art shared between compounds of this query — a patent
    # document that names more than one of them. Counting one compound's long
    # patent list as overlap made a single well-covered drug look like a shared
    # scaffold, and the reading claimed records were "linked to more than one
    # compound" when they were not.
    overlapping = [uid for uid, compounds in by_patent.items() if len(compounds) >= 2]

    tried_compounds = [
        {
            "uid": uid,
            "name": compound_names.get(uid, uid),
            "patent_count": len(pats),
        }
        for uid, pats in sorted(
            by_compound.items(), key=lambda kv: -len(kv[1])
        )[:MAX_COMPOUND_VERDICTS]
    ]

    return {
        "patent_count": len(patent_ids),
        "compounds_with_patents": len(by_compound),
        "overlap_signals": len(overlapping),
        "compounds": tried_compounds,
        "reading": (
            f"{len(overlapping)} patent record"
            f"{'s are' if len(overlapping) != 1 else ' is'} linked to more than "
            "one compound from this query — the scaffold space has prior art "
            "and family filings, so novelty around it is contested."
            if overlapping
            else "No patent record in this graph is linked to more than one "
            "compound from this query; shared prior-art overlap was not "
            "detected at graph level."
        ),
        "caveat": (
            "This reads structured patent metadata, not full claim text or "
            "assay appendices — development-abandonment reasons are not "
            "extractable at this layer."
        ),
    }


def patent_register(
    nodes: List[Dict[str, str]],
    edges: List[Dict[str, str]],
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """The patents this search surfaced, newest first, with their links.

    The patents were only ever readable by opening the workbook; the results
    screen showed a graph full of grey patent dots and no way to see a single
    patent number. This is the same data the export holds, shaped for a list:
    one entry per patent with what the source actually supplied, and an empty
    register when there is nothing rather than a placeholder row.

    Every patent is returned by default. An earlier twelve-row cap made a
    search with thirty patents read as "12 of 30", which looks like missing
    data; `limit` remains for callers that genuinely want a preview.
    """
    patents: List[Dict[str, Any]] = []
    for row in nodes:
        uid = _row_uid(row)
        label = _label(row.get(":LABEL", ""))
        if not (_has_prefix(uid, _PATENT_PREFIXES) or label == "PATENT"):
            continue
        name = (row.get("name") or "").strip()
        number = (
            row.get("patent_number")
            or row.get("canonical_id")
            or uid.split(":")[-1]
        ).strip()
        if not number or not _is_patent_document(uid, number):
            continue
        title = (row.get("title") or "").strip()
        patents.append({
            "uid": uid,
            "number": number,
            "title": title or name,
            "year": _patent_year(
                uid, name, row.get("canonical_id", ""), row.get("publication_year", "") or ""
            ),
            "assignee": (row.get("assignee") or "").strip(),
            "compound": (row.get("queried_compound") or "").strip(),
            "url": google_patents_url(
                number, row.get("google_patents_url") or ""
            ),
        })

    patent_uids = {p["uid"] for p in patents}
    names = {_row_uid(row): (row.get("name") or _row_uid(row)).strip() for row in nodes}
    linked: set = set()
    for edge in edges:
        if (edge.get(":TYPE") or "").strip().upper() != "PATENTED_IN":
            continue
        if (edge.get(":END_ID") or "").strip() in patent_uids:
            linked.add((edge.get(":START_ID") or "").strip())

    patents.sort(key=lambda p: (-(p["year"] or 0), p["number"]))
    listed = patents if limit is None else patents[:max(0, limit)]
    return {
        "patent_count": len(patents),
        "compound_count": len(linked),
        "compounds": [names.get(uid, uid) for uid in sorted(linked)][:8],
        "patents": listed,
        "truncated": len(listed) < len(patents),
    }


# ── Toxicity on record ──────────────────────────────────────────────────────


def toxicity_profile(nodes: List[Dict[str, str]], edges: List[Dict[str, str]]) -> Dict[str, Any]:
    """The toxicity statements this search actually collected, per compound.

    The engine writes one Toxicity node per statement quoted from a compound's
    PubChem Toxicity section, joined to the compound by `has_toxicity`. Nothing
    is predicted or graded here: the section, the statement and its citation are
    passed through as recorded. A compound PubChem lists nothing for simply has
    no entries — which is not the same as being shown safe, and the caveat says
    so.
    """
    statements: Dict[str, Dict[str, Any]] = {}
    for row in nodes:
        uid = _row_uid(row)
        label = _label(row.get(":LABEL", ""))
        if not (uid.upper().startswith("TOXICITY:") or label == "TOXICITY"):
            continue
        text = (
            row.get("bioactivity_summary")
            or row.get("toxicity_statement")
            or row.get("name")
            or ""
        ).strip()
        if not text:
            continue
        statements[uid] = {
            "uid": uid,
            "section": (row.get("toxicity_section") or "Toxicity").strip(),
            "statement": text,
            "citation": (row.get("source_citation") or "").strip(),
        }

    by_compound: Dict[str, List[str]] = {}
    for edge in edges:
        if (edge.get(":TYPE") or "").strip().upper() != "HAS_TOXICITY":
            continue
        src = (edge.get(":START_ID") or "").strip()
        tgt = (edge.get(":END_ID") or "").strip()
        if tgt in statements:
            by_compound.setdefault(src, []).append(tgt)

    names = {
        _row_uid(row): (row.get("name") or _row_uid(row)).strip() for row in nodes
    }
    compounds: List[Dict[str, Any]] = []
    for uid, uids in sorted(by_compound.items(), key=lambda kv: -len(kv[1]))[:MAX_COMPOUND_VERDICTS]:
        compounds.append({
            "uid": uid,
            "name": names.get(uid, uid),
            "statements": [statements[u] for u in uids[:8]],
        })

    total = len(statements)
    if not total:
        return {
            "statement_count": 0,
            "compound_count": 0,
            "compounds": [],
            "reading": (
                "No toxicity statements were collected for this graph. That "
                "means PubChem lists none for these compounds, not that they "
                "are safe."
            ),
            "caveat": (
                "Toxicity here is what PubChem's Toxicity section records — "
                "hazard classifications and measured lethal doses — not a "
                "prediction. Absence of a statement is not evidence of safety."
            ),
        }

    return {
        "statement_count": total,
        "compound_count": len(by_compound),
        "compounds": compounds,
        "reading": (
            f"{total} toxicity statements on record from PubChem cover "
            f"{len(by_compound)} compound{'s' if len(by_compound) != 1 else ''} "
            "in this graph — read the classification and lethal-dose values "
            "before committing to in-vivo work."
        ),
        "caveat": (
            "Statements are quoted from PubChem's Toxicity section, which "
            "aggregates HSDB, IARC and national-authority records; each entry "
            "carries its own citation. Absence of a statement is not evidence "
            "of safety."
        ),
    }


# ── 3. Mechanism-of-Action storyboard ────────────────────────────────────────


def _pdb_ref(value: str) -> Optional[str]:
    m = re.search(r"\b([0-9][A-Za-z0-9]{3})\b", value or "")
    return m.group(1).upper() if m else None


def moa_storyboard(
    nodes: List[Dict[str, str]], edges: List[Dict[str, str]], query: str
) -> Dict[str, Any]:
    """Assemble the evidence blocks for a MoA narrative from the graph."""
    rows = {_row_uid(row): row for row in nodes}

    structures: List[Dict[str, Any]] = []
    potency_edges: List[Dict[str, Any]] = []
    publications = 0
    patents = 0

    for edge in edges:
        rel = (edge.get(":TYPE") or "").strip().upper()
        src = (edge.get(":START_ID") or "").strip()
        tgt = (edge.get(":END_ID") or "").strip()
        src_row = rows.get(src, {})
        tgt_row = rows.get(tgt, {})
        if rel == "HAS_STRUCTURE" or _label(tgt_row.get(":LABEL", "")) == "STRUCTURE":
            ref = _pdb_ref(tgt_row.get("canonical_id", "") or tgt)
            if ref:
                structures.append({"pdb_id": ref, "compound": src_row.get("name", src)})
        pchembl = _pchembl_value(edge)
        if pchembl is not None:
            potency_edges.append(
                {
                    "compound": src_row.get("name", src),
                    "target": tgt_row.get("name", tgt),
                    "activity_type": (edge.get("activity_type") or "").strip(),
                    "value": edge.get("activity_value", "").strip(),
                    "units": (edge.get("units") or "").strip(),
                    "pchembl": pchembl,
                }
            )
        if rel in ("CITED_BY", "PUBLISHED_IN") or _label(tgt_row.get(":LABEL", "")) == "PUBLICATION":
            publications += 1
        if _has_prefix(tgt, _PATENT_PREFIXES) or _label(tgt_row.get(":LABEL", "")) == "PATENT":
            patents += 1

    potency_edges.sort(key=lambda p: -p["pchembl"])
    targets: Dict[str, Dict[str, Any]] = {}
    for p in potency_edges[:12]:
        entry = targets.setdefault(p["target"], {"name": p["target"], "best": p, "count": 0})
        entry["count"] += 1

    compounds = [
        {
            "uid": uid,
            "name": row.get("name", uid),
            "smiles": (row.get("smiles") or "").strip(),
        }
        for uid, row in rows.items()
        if _has_prefix(uid, _COMPOUND_PREFIXES)
    ][:MAX_COMPOUND_VERDICTS]

    return {
        "query": query,
        "targets": list(targets.values()),
        "structures": structures[:8],
        "potency_edges": potency_edges[:12],
        "compounds": compounds,
        "publications": publications,
        "patents": patents,
    }


# ── 4. Vendor sourcing ───────────────────────────────────────────────────────

_PUBCHEM_COMPOUND_URL = "https://pubchem.ncbi.nlm.nih.gov/compound/"


def _cross_refs(row: Dict[str, str]) -> Dict[str, str]:
    """Parse the cross_references column into ``{DATABASE: accession}``.

    Exports join the pairs with ``|`` (see ``scigraph.export_to_csv``); older
    extracts used ``;``. Splitting on only one separator left the entire joined
    string as the "accession" — e.g. ``CHEMBL25|PDB:1ABX|PUBCHEM:2244`` — so
    every vendor link queried PubChem for a string that matches nothing.
    """
    refs: Dict[str, str] = {}
    raw = (row.get("cross_references") or "").replace(";", "|")
    for ref in raw.split("|"):
        ref = ref.strip()
        if ":" not in ref:
            continue
        database, accession = ref.split(":", 1)
        database = database.strip().upper().replace(" ", "")
        # Exports written before the fix serialised the enum itself
        # ("DatabaseSource.PUBCHEM:2244"); keep those saved searches working.
        if database.startswith("DATABASESOURCE."):
            database = database.split(".", 1)[1]
        accession = accession.strip()
        if database and accession and database not in refs:
            refs[database] = accession
    return refs


def _vendor_url(identifier: str, kind: str) -> str:
    """A PubChem page that shows who sells the compound.

    A CID gets its compound summary's Chemical Vendors section directly; other
    identifiers (InChIKey, ChEMBL id, SMILES, name) go through PubChem Quick
    Search, which resolves them to the matching compound record.
    """
    if not identifier:
        return ""
    if kind == "pubchem_cid" and identifier.isdigit():
        return f"{_PUBCHEM_COMPOUND_URL}{identifier}#section=Chemical-Vendors"
    return "https://pubchem.ncbi.nlm.nih.gov/#query=" + quote_plus(identifier)


def _chembl_id(row: Dict[str, str], refs: Dict[str, str], uid: str) -> str:
    """The ChEMBL accession, from the xrefs or the node's own identifiers."""
    cached = refs.get("CHEMBL", "")
    if cached:
        return cached
    for candidate in ((row.get("canonical_id") or "").strip(), uid.split(":", 1)[-1].strip()):
        if candidate.upper().startswith("CHEMBL") and candidate[6:].strip().isdigit():
            return candidate.strip()
    return ""


def vendor_sourcing(nodes: List[Dict[str, str]]) -> Dict[str, Any]:
    """Who can sell me this? Link out to PubChem's vendor registry per compound.

    Vendor availability itself is a live lookup (PubChem xref/RegistryID), not
    something the graph carries; the deliverable here is the resolution from
    graph compound to the queryable identifier and the exact registry URL.
    """
    compounds: List[Dict[str, Any]] = []
    for row in nodes:
        uid = _row_uid(row)
        if not _has_prefix(uid, _COMPOUND_PREFIXES):
            continue
        smiles = (row.get("smiles") or "").strip()
        name = (row.get("name") or uid).strip()
        refs = _cross_refs(row)
        pubchem = refs.get("PUBCHEM", "")
        inchikey = refs.get("INCHIKEY", "")
        if pubchem and not pubchem.isdigit():
            # A non-numeric PubChem accession is usually an InChIKey or a name.
            inchikey = inchikey or pubchem
            pubchem = ""
        chembl = _chembl_id(row, refs, uid)

        identifier, kind = "", ""
        for candidate, candidate_kind in (
            (pubchem, "pubchem_cid"),
            (inchikey, "inchikey"),
            (chembl, "chembl"),
            (smiles, "smiles"),
            (name, "name"),
        ):
            if candidate:
                identifier, kind = candidate, candidate_kind
                break

        compounds.append(
            {
                "uid": uid,
                "name": name,
                "smiles": smiles,
                "identifier": identifier,
                "identifier_kind": kind,
                "vendors_url": _vendor_url(identifier, kind),
            }
        )
        if len(compounds) >= MAX_COMPOUND_VERDICTS:
            break

    return {
        "compounds": compounds,
        "registry": "PubChem Substance (vendor xref: Sigma-Aldrich, Enamine, MolPort, WuXi...)",
        "note": (
            "Availability is resolved live from PubChem's vendor registry at "
            "query time via the links; the graph itself does not store vendor "
            "catalogues."
        ),
    }


def quote_plus(value: str) -> str:
    from urllib.parse import quote_plus as _qp

    return _qp(value)


# ── Combined bundle ──────────────────────────────────────────────────────────


def decision_intel(
    export_dir: str,
    query: str,
    now_year: Optional[int] = None,
    exports_root: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Everything above, computed from one search's export directory.

    Returns None when the directory is missing or outside the exports root —
    the caller turns that into a 404, never a traversal.

    `exports_root` defaults to the FastAPI app's EXPORTS_DIR, which is where the
    engine writes. The Next.js reader passes its own `exports/` directory
    instead: the two processes keep their files in different places, so the
    app's default rejected every Next export as out-of-tree, and importing the
    web app also dragged FastAPI into a bare `python3` that does not have it.
    """
    root = str(export_dir or "")
    if not root:
        return None
    if exports_root is None:
        from app import EXPORTS_DIR as exports_root  # late import: no cycle at load

    try:
        base = Path(exports_root).resolve()
        directory = Path(root).resolve()
    except (OSError, ValueError):
        return None
    if directory != base and base not in directory.parents:
        return None
    if not directory.is_dir():
        return None

    nodes_file = directory / "nodes.csv"
    edges_file = directory / "edges.csv"
    if not nodes_file.exists() or not edges_file.exists():
        return None

    nodes = _read_csv(nodes_file)
    edges = _read_csv(edges_file)
    verdict = feasibility_verdict(nodes, edges)
    ip = ip_status(nodes, edges, now_year=now_year)
    dark = dark_literature(nodes, edges)
    moa = moa_storyboard(nodes, edges, query)
    vendors = vendor_sourcing(nodes)
    toxicity = toxicity_profile(nodes, edges)

    return {
        "search_id": "",
        "query": query,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "feasibility": verdict,
        "ip": ip,
        "dark_literature": dark,
        "moa": moa,
        "vendors": vendors,
        "toxicity": toxicity,
        "not_assessed": [
            "Predicted toxicity (hERG, Ames mutagenicity): not computed — this "
            "bundle reports the statements PubChem publishes, not a model's "
            "guess, and an invented flag would be worse than none.",
        ],
    }
