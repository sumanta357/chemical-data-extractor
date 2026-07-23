"""scigraph_main.py

A friendly CLI entrypoint that integrates scigraph.py and chemdata_port.py without modifying scigraph.py.

Features:
- Run the existing scigraph pipeline (delegates to scigraph.run_automated_search)
- Run chemical extraction on a file (--chem-extract) using chemdata_port.Document, export JSON and register extracted compounds into DuckDBRepository

Usage examples:
  python scigraph_main.py --query "Aspirin"
  python scigraph_main.py --chem-extract sample.pdf --workspace ./scigraph_data --export-dir ./exports

"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import List

# Import core scigraph pipeline
try:
    import scigraph
    from scigraph import run_automated_search, DuckDBRepository, Entity, EntityType, Evidence
except Exception as e:
    raise RuntimeError(f"Failed to import scigraph module: {e}")

# Import chemdata port
try:
    from chemdata_port import Document as ChemDocument
except Exception:
    ChemDocument = None


def safe_filename(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", s)


def register_compounds_to_duckdb(doc: ChemDocument, workspace_dir: str) -> List[str]:
    """Create Entity objects from extracted compounds and save them into DuckDB repository.
    Returns list of created entity uids.
    """
    workspace = Path(workspace_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    repo = DuckDBRepository(str(workspace / "graph.duckdb"))

    created_uids: List[str] = []
    entities = []
    for idx, comp in enumerate(doc.compounds, start=1):
        uid = f"CHEMDOC:COMPOUND:{safe_filename(str(comp.name or comp.smiles or comp.formula or idx))}:{idx}"
        preferred_name = comp.name or comp.canonical_name or comp.smiles or comp.formula or uid
        canonical_id = comp.identifiers.get("inchikey") or comp.inchi or comp.smiles or comp.formula or preferred_name
        evidence = [Evidence(database="ChemDataPort", source_url=doc.source or "inline")] if hasattr(scigraph, 'Evidence') else []
        attrs = {}
        if comp.smiles: attrs["smiles"] = comp.smiles
        if comp.inchi: attrs["inchi"] = comp.inchi
        if comp.formula: attrs["formula"] = comp.formula

        try:
            ent = Entity(uid=uid, entity_type=EntityType.COMPOUND, preferred_name=preferred_name, canonical_id=canonical_id, evidence=evidence, attributes=attrs)
            entities.append(ent)
            created_uids.append(uid)
        except Exception:
            # best-effort: skip malformed
            continue

    if entities:
        try:
            repo.save_entities_bulk(entities)
        except Exception as e:
            print(f"Warning: failed to save entities to DuckDB: {e}")

    return created_uids


def run_chem_extract(path: str, workspace: str, export_dir: str, out_json: str | None = None):
    if ChemDocument is None:
        raise RuntimeError("chemdata_port.Document is not available. Ensure chemdata_port.py is present and importable.")
    doc = ChemDocument.from_file(path)
    doc.extract_all()

    out_dir = Path(export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = Path(path).stem
    outfile = out_dir / (f"{base}_chemdata.json")
    out_text = doc.to_json()
    outfile.write_text(out_text, encoding="utf-8")
    print(f"Chemical extraction saved to: {outfile}")

    # register to duckdb
    created = register_compounds_to_duckdb(doc, workspace)
    print(f"Registered {len(created)} compounds into DuckDB repository at {workspace}/graph.duckdb")

    if out_json:
        Path(out_json).write_text(out_text, encoding="utf-8")
        print(f"Also wrote to: {out_json}")


def main():
    parser = argparse.ArgumentParser(description="SciGraph integrated runner with chem extraction")
    parser.add_argument("--query", default=None, help="Query to run through scigraph pipeline")
    parser.add_argument("--batch", default=None, help="Batch queries file")
    parser.add_argument("--workspace", default="./scigraph_data", help="Workspace dir")
    parser.add_argument("--export-dir", default="./exports", help="Export dir")
    parser.add_argument("--hops", type=int, default=4, help="Hops")
    parser.add_argument("--assistant", action="store_true", help="Run assistant")
    parser.add_argument("--chem-extract", default=None, help="Path to file (pdf/html/txt) to run chem extraction")
    parser.add_argument("--chem-out", default=None, help="Optional explicit path for extracted JSON output")

    args = parser.parse_args()

    if args.chem_extract:
        run_chem_extract(args.chem_extract, args.workspace, args.export_dir, out_json=args.chem_out)
        return

    # Delegate to existing scigraph runner
    if args.batch:
        scigraph.main() if hasattr(scigraph, 'main') else print('scigraph.main not available')
        return

    if args.query:
        # call run_automated_search from scigraph
        run_automated_search(args.query, workspace_dir=args.workspace, export_dir=args.export_dir, max_hops=args.hops)
        return

    # fallback: launch interactive mode from scigraph if available
    if hasattr(scigraph, 'interactive_mode'):
        scigraph.interactive_mode()
    else:
        print("No action specified and scigraph interactive mode not available.")


if __name__ == "__main__":
    main()
