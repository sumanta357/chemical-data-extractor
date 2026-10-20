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
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def _now_year() -> int:
    return datetime.now(timezone.utc).year


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


def _parse_number(value: str) -> Optional[float]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


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

    activity_value is only a fallback for edges that declare what they mean
    (IC50/Ki/EC50): a bare number on a relation like HAS_STRUCTURE is not an
    affinity and must never leak into the grade.
    """
    affinity_kinds = ("IC50", "KI", "EC50", "POTENCY", "KD", "AC50")
    by_compound: Dict[str, List[float]] = {}
    for edge in edges:
        src = (edge.get(":START_ID") or "").strip()
        if not _has_prefix(src, _COMPOUND_PREFIXES):
            continue
        kind = (edge.get("activity_type") or "").strip().upper()
        pchembl = _parse_number(edge.get("pchembl_value", ""))
        if pchembl is None:
            if kind not in affinity_kinds:
                continue
            pchembl = _parse_number(edge.get("activity_value", ""))
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
            "the graph: A >= 9.0, B >= 8.0, C >= 7.0, D >= 6.0, F below."
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
        is_patent = _has_prefix(uid, _PATENT_PREFIXES) or label == "PATENT"
        if not is_patent:
            continue
        pub_year = _patent_year(
            uid, row.get("name", ""), row.get("canonical_id", ""), row.get("synonyms", "")
        )
        patents.append({"uid": uid, "name": row.get("name", uid), "year": pub_year})

    if not patents:
        return {
            "status": "green",
            "reason": "No patent records surfaced for this query.",
            "patents": [],
            "caveat": "Absence of a patent in this graph is not a freedom-to-operate opinion.",
        }

    dated = [p for p in patents if p["year"] is not None]
    if not dated:
        return {
            "status": "yellow",
            "reason": "Patents exist but their years could not be parsed from identifiers.",
            "patents": patents,
            "caveat": "Verify protection status manually before relying on this.",
        }

    latest = max(p["year"] for p in dated)
    remaining = latest + PATENT_TERM_YEARS - year
    if remaining <= 0:
        status = "green"
        reason = (
            f"Newest patent found was published in {latest}; its {PATENT_TERM_YEARS}-year "
            "term has lapsed."
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
        "caveat": (
            "Publication year stands in for the filing date; patent families "
            "and extensions can shift real expiry by years."
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
    for src, tgt in links:
        by_compound.setdefault(src, []).append(tgt)

    overlapping = [
        uid for uid, pats in by_compound.items() if len(pats) >= 2
    ]

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
            "Patent records are linked to more than one compound from this "
            "query — the scaffold space has prior art and family filings, so "
            "novelty around it is contested."
            if overlapping
            else "No compound in this graph is linked to multiple patent "
            "records; heavy prior-art overlap was not detected at graph level."
        ),
        "caveat": (
            "This reads structured patent metadata, not full claim text or "
            "assay appendices — development-abandonment reasons are not "
            "extractable at this layer."
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
        pchembl = _parse_number(edge.get("pchembl_value", ""))
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

_PUBCHEM_VENDOR_URL = (
    "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/xref/RegistryID/JSON"
)


def _inchikey_from(row: Dict[str, str]) -> str:
    """The cross_references column lists 'ChEMBL:...', 'PDB:...' style pairs."""
    for ref in (row.get("cross_references") or "").split(";"):
        ref = ref.strip()
        if ref.upper().startswith(("INCHIKEY:", "PUBCHEM:")):
            return ref.split(":", 1)[1].strip()
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
        ref = _inchikey_from(row)
        chembl = ""
        for candidate in (row.get("cross_references") or "").split(";"):
            candidate = candidate.strip()
            if candidate.upper().startswith("CHEMBL:"):
                chembl = candidate.split(":", 1)[1].strip()
                break
        lookup = ref or chembl or smiles
        vendors_url = ""
        if lookup:
            vendors_url = (
                "https://pubchem.ncbi.nlm.nih.gov/#query="
                + quote_plus(lookup)
                + "&tab=substance"
            )
        compounds.append(
            {
                "uid": uid,
                "name": name,
                "smiles": smiles,
                "identifier": lookup,
                "identifier_kind": ("inchikey/pubchem" if ref else ("chembl" if chembl else ("smiles" if smiles else ""))),
                "vendors_url": vendors_url,
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
    export_dir: str, query: str, now_year: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Everything above, computed from one search's export directory.

    Returns None when the directory is missing or outside EXPORTS_DIR — the
    caller turns that into a 404, never a traversal.
    """
    from app import EXPORTS_DIR, _export_dir_is_safe  # late import: no cycle at load

    root = str(export_dir or "")
    if not root or not _export_dir_is_safe(root):
        return None
    directory = Path(root)
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
        "not_assessed": [
            "Toxicity (EPA CompTox Ames / hERG): not computed — the engine "
            "does not hold CompTox assay data, and a fabricated toxicity flag "
            "would be worse than none.",
        ],
    }
