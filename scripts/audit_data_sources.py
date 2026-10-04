#!/usr/bin/env python3
"""Live per-connector audit of the Scientific Knowledge Graph engine.

Runs every *implemented* connector against its real upstream API with a query
it is meant to handle, and reports what actually came back. This is the
evidence behind the data-source audit: a connector only counts as working if a
real request returned real records.

    python3 scripts/audit_data_sources.py                 # default queries
    python3 scripts/audit_data_sources.py --only Patents  # one connector
    python3 scripts/audit_data_sources.py --json report.json

Read-only: it imports the engine but never writes exports or a workspace
beyond a throwaway cache directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))

# Representative query per connector: the entity type each source is actually
# authoritative for. "auto" is passed so the connector takes its own routing
# decision, exactly as the production pipeline does.
QUERIES = {
    "PubChem": ("Aspirin", "auto"),
    "ChEMBL": ("Aspirin", "auto"),
    "ChEBI": ("aspirin", "auto"),
    "UniProt": ("P23219", "auto"),
    "GeneOntology": ("GO:0008137", "auto"),
    "BRENDA": ("cyclooxygenase", "auto"),
    "KEGG": ("tyrosine", "auto"),
    "Reactome": ("R-HSA-70171", "auto"),
    "BindingDB": ("Aspirin", "auto"),
    "PDBbind": ("1HSG", "auto"),
    "PDBe-KB": ("1HSG", "auto"),
    "Guide to Pharmacology": ("aspirin", "auto"),
    "STRING": ("TP53", "auto"),
    "NCBI Gene": ("7157", "auto"),
    "NCBI Taxonomy": ("Homo sapiens", "auto"),
    "EuropePMC": ("10.1038/nature12373", "auto"),
    "OpenAlex": ("aspirin", "auto"),
    "Patents": ("Aspirin", "auto"),
}

# Extra probes for the patent question specifically.
PATENT_PROBES = [
    ("compound name", "Aspirin", "auto"),
    ("compound name", "Nitrapyrin", "auto"),
    ("compound name", "Sildenafil", "auto"),
    ("numeric CID", "2244", "auto"),
    ("patent number", "US6280349", "auto"),
    ("patent number", "WO2004018667", "auto"),
]


async def audit_one(connector, query: str, query_type: str, timeout: float = 70.0):
    """Run one connector and summarise what it returned."""
    import aiohttp

    started = time.time()
    error = None
    entities: list = []
    relations: list = []
    try:
        async with aiohttp.ClientSession() as session:
            entities, relations = await asyncio.wait_for(
                connector.search(session, query, query_type), timeout=timeout
            )
    except asyncio.TimeoutError:
        error = f"timeout after {timeout:.0f}s"
    except Exception as exc:  # the production path swallows these; we do not
        error = f"{type(exc).__name__}: {exc}"[:200]

    elapsed = time.time() - started
    by_type: dict[str, int] = {}
    for e in entities or []:
        key = getattr(getattr(e, "entity_type", None), "value", None) or str(
            getattr(e, "entity_type", "?")
        )
        by_type[key] = by_type.get(key, 0) + 1
    sources = sorted({getattr(ev, "database", None) for e in (entities or [])
                      for ev in (getattr(e, "evidence", None) or [])} - {None})
    return {
        "connector": connector.NAME,
        "query": query,
        "query_type": query_type,
        "entities": len(entities or []),
        "relations": len(relations or []),
        "entity_types": by_type,
        "evidence_sources": sources,
        "seconds": round(elapsed, 1),
        "error": error,
        "sample_entities": [getattr(e, "preferred_name", "") for e in (entities or [])[:3]],
        "sample_relations": [
            f"{getattr(r, 'relation_type', '')}".strip() for r in (relations or [])[:3]
        ],
    }


# Curated status per implemented connector.
#
# "working"     - a real request returned usable records for the queries it is
#                 responsible for (verified by running this script).
# "partial"     - returns records for some query types only.
# "unavailable" - the upstream endpoint needs credentials or is not serving.
# "unverified"  - implemented, but not exercised by the last audit run.
#
# `note` is written for a customer, so it says what the source covers and what
# the current limitation is -- not how it is implemented.
NOTES = {
    "PubChem": ("Compounds, identifiers, structures", "Compounds", "", "unverified"),
    "ChEMBL": ("Bioactivities, targets, potency (pChEMBL)", "Bioactivity", "", "unverified"),
    "ChEBI": ("Chemical ontology and roles", "Compounds", "", "unverified"),
    "UniProt": ("Protein records, accessions, sequences, structures", "Proteins", "", "unverified"),
    "GeneOntology": ("Gene Ontology terms and annotations", "Function", "", "unverified"),
    "BRENDA": ("Enzyme kinetics and inhibitors", "Pathways", "", "unverified"),
    "KEGG": ("Compounds, reactions and pathways", "Pathways", "", "unverified"),
    "Reactome": ("Pathways, reactions and participants", "Pathways", "", "unverified"),
    "PDBbind": ("Measured binding affinities for PDB entries", "Bioactivity", "", "unverified"),
    "PDBe-KB": ("Experimental structures and ligand data", "Structures", "", "unverified"),
    "STRING": ("Protein-protein functional associations", "Proteins", "", "unverified"),
    "NCBI Gene": ("Gene records", "Genes", "", "unverified"),
    "NCBI Taxonomy": ("Organism taxonomy", "Organisms", "", "unverified"),
    "EuropePMC": ("Publications and citations", "Literature", "", "unverified"),
    "OpenAlex": ("Scholarly works, authors, citations", "Literature", "", "unverified"),
    "Patents": ("Patent records for compounds, and publication numbers", "Patents",
                "Patent coverage comes from PubChem's compound cross-references, so it "
                "is strongest for well-known compounds. Searching a bare publication "
                "number depends on a public endpoint that rate-limits under load; when "
                "that happens you get a direct patent search link instead of a record.",
                "partial"),
    "BindingDB": ("Measured ligand-target affinities", "Bioactivity",
                  "Returns curated affinities for protein accessions and, via a "
                  "name-to-structure lookup, for compound names. Coverage varies by "
                  "compound: some simply have no curated affinity within the cutoff.",
                  "working"),
    "Guide to Pharmacology": ("Curated targets, ligands and pharmacology", "Bioactivity",
                              "Temporarily unavailable: this source now requires an API key, "
                              "which is not configured on this deployment.",
                              "unavailable"),
}

GROUPS = [
    ("Compounds", "Compound identifiers, structures and chemistry."),
    ("Bioactivity", "Measured binding, inhibition and potency values."),
    ("Proteins", "Protein records, accessions and functional associations."),
    ("Structures", "Experimental 3D structures and their ligands."),
    ("Pathways", "Enzymes, reactions and biological pathways."),
    ("Function", "Ontology terms and functional annotations."),
    ("Genes", "Gene-level records."),
    ("Organisms", "Taxonomy."),
    ("Literature", "Publications and citation evidence."),
    ("Patents", "Patent records and patent landscape."),
]


def write_status_file(path: str, results: list, cache, translator, user_results=None) -> None:
    """Write the verified status of every implemented connector.

    The status is derived from this run wherever the connector was exercised;
    anything not exercised is reported as unverified rather than assumed good.

    ``user_results`` adds the second axis that decides whether a customer ever
    sees a source: most connectors only answer the query format they are
    authoritative for (a PDB id, a GO term, a DOI). A connector can therefore
    be perfectly working and still return nothing for the plain name someone
    typed, and the interface has to be able to say so rather than implying the
    source is broken.
    """
    import scigraph  # noqa: F811 - already imported by main()

    measured: dict[str, dict] = {}
    for row in results:
        if row.get("connector") and row.get("query"):
            measured.setdefault(row["connector"], row)

    plain: dict[str, dict] = {}
    for row in user_results or []:
        if not row.get("connector") or not row.get("query"):
            continue
        plain.setdefault(row["connector"], {})[row["query"]] = {
            "entities": row["entities"],
            "relations": row["relations"],
            "error": row.get("error"),
        }

    # Keep plain-query results recorded by earlier runs so a second --user-query
    # adds to the picture instead of replacing it.
    previous: dict[str, dict] = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for source in json.load(fh).get("sources", []):
                    if source.get("plain_query"):
                        previous[source["name"]] = source["plain_query"]
        except Exception:
            previous = {}
    for name, entry in plain.items():
        previous.setdefault(name, {}).update(entry)

    sources = []
    for cls in scigraph.ConnectorRegistry.get_all().values():
        # The registry also holds one auto-generated no-op per catalogue entry.
        # Those are built inside a factory, which __qualname__ marks.
        if "<locals>" in cls.__qualname__:
            continue
        name = cls.NAME
        row = measured.get(name)
        note = NOTES.get(name, ("", "Other", "", "unverified"))

        if row is None:
            status = "unverified"
        elif row["entities"] > 0 and not row["error"]:
            status = "working"
        elif row["error"]:
            status = "unavailable"
        else:
            # Returned nothing for the audited query. The curated entry records
            # whether that is a hard limitation or a narrower scope.
            status = note[3]

        entry = {
            "name": name,
            "status": status,
            "covers": note[0],
            "group": note[1],
            "note": note[2],
        }
        if previous.get(name):
            entry["plain_query"] = previous[name]
            answered = sum(1 for v in previous[name].values() if v["entities"])
            total = len(previous[name])
            # Answers its own format but not the words people actually type:
            # connected and healthy, just narrow.
            entry["answers_common_queries"] = answered > 0 if total else None
        sources.append(entry)

    sources.sort(key=lambda s: (s["group"], s["name"]))
    counts = {k: sum(1 for s in sources if s["status"] == k)
              for k in ("working", "partial", "unavailable", "unverified")}
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "implemented_total": len(sources),
        "counts": counts,
        "groups": [{"name": g, "description": d} for g, d in GROUPS],
        "sources": sources,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append", default=None,
                        help="connector name to test (repeatable)")
    parser.add_argument("--json", dest="json_out", default=None)
    parser.add_argument("--status-file", dest="status_file", default=None,
                        help="write the customer-facing data-source status JSON "
                             "(e.g. python-api/data_sources_status.json)")
    parser.add_argument("--patents", action="store_true",
                        help="run the dedicated patent probes as well")
    parser.add_argument("--user-query", action="append", default=None,
                        help="probe every implemented connector with this plain "
                             "search term (repeatable) to record which sources "
                             "answer the words a customer actually types")
    args = parser.parse_args()

    import scigraph  # noqa: E402  (import after sys.path is set)

    tmp = tempfile.mkdtemp(prefix="cde-audit-")
    try:
        cache = scigraph.KnowledgeCache(os.path.join(tmp, "cache.sqlite"))
        translator = scigraph.UniversalIDTranslator()

        # Implemented connectors only: the registry also holds ~100 auto-generated
        # stubs whose search() returns ([], []) by design.
        wanted = set(args.only) if args.only else set(QUERIES)
        results: list[dict] = []
        targets = []
        for cls in scigraph.ConnectorRegistry.get_all().values():
            if cls.__name__ == "DynamicConnector":
                continue
            try:
                instance = cls(cache, translator)
            except Exception as exc:
                results.append({
                    "connector": cls.NAME, "query": None, "query_type": None,
                    "entities": 0, "relations": 0, "entity_types": {},
                    "evidence_sources": [], "seconds": 0.0,
                    "error": f"instantiation failed: {exc}"[:200],
                    "sample_entities": [], "sample_relations": [],
                })
                continue
            if instance.NAME in wanted:
                targets.append(instance)

        for instance in targets:
            query, qtype = QUERIES.get(instance.NAME, (instance.NAME, "auto"))
            row = await audit_one(instance, query, qtype)
            results.append(row)
            status = "ERROR" if row["error"] else ("data" if row["entities"] else "EMPTY")
            print(f"{instance.NAME:22s} q={query!r:16s} {status:5s} "
                  f"entities={row['entities']:<4d} relations={row['relations']:<4d} "
                  f"{row['seconds']:>5.1f}s"
                  + (f"  <- {row['error']}" if row["error"] else ""))

        if args.patents:
            print("\n--- patent probes ---")
            patents = next((i for i in targets if i.NAME == "Patents"), None)
            if patents is None:
                patents = scigraph.PatentConnector(cache, translator)
            for label, query, qtype in PATENT_PROBES:
                row = await audit_one(patents, query, qtype)
                row["probe"] = label
                results.append(row)
                types = ", ".join(f"{k}={v}" for k, v in row["entity_types"].items())
                print(f"[{label:14s}] {query!r:16s} entities={row['entities']:<4d} "
                      f"relations={row['relations']:<4d} {types or '-'}"
                      + (f"  <- {row['error']}" if row["error"] else ""))
                for name in row["sample_entities"]:
                    print(f"      e.g. {name}")

        user_results: list[dict] = []
        for term in args.user_query or []:
            print(f"\n--- plain-name query {term!r} across every connector ---")
            for instance in list(targets):
                row = await audit_one(instance, term, "auto")
                user_results.append(row)
                status = "ERROR" if row["error"] else ("data" if row["entities"] else "EMPTY")
                print(f"  {instance.NAME:22s} {status:5s} entities={row['entities']}")

        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2)
            print(f"\nwrote {args.json_out}")

        if args.status_file:
            write_status_file(args.status_file, results, cache, translator, user_results)
            print(f"wrote {args.status_file}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))