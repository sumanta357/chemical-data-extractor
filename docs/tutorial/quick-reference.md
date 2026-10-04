# Quick Reference

**Chemical Data Extractor v3.2 · desk card**

## The four steps of a search

1. Type the protein or compound name into **Query**.
2. Leave **Query type** on **Auto** unless you know it is a protein or ligand.
3. Choose an **expansion depth**: 1 for direct hits, 2 for a working network,
   3–4 for full pathway and disease context.
4. Click **Run search**, then watch the log and download the exports.

## Choosing a depth

| Depth | Use it for | Runtime |
| --- | --- | --- |
| 1 hop | Direct targets, known ligands, structure lookup | 20–90 s |
| 2 hops | Screening, mechanism of action, pathway entry | 1–4 min |
| 3 hops | Pathway and disease context | 3–10 min |
| 4 hops | Exhaustive network mapping | 5–20 min |

## Status meanings

| Status | Meaning |
| --- | --- |
| Queued | Accepted, waiting for the single search slot or for the engine to wake |
| Running | Working — the log is live |
| Completed | Exports are ready to download |
| Failed | Read the message in the red banner, then re-run |

## Export files at a glance

| File | Open it in |
| --- | --- |
| `*_Knowledge_Graph.xlsx` | Excel or LibreOffice |
| `nodes.csv`, `edges.csv` | Excel, pandas, R, a database importer |
| `graph.graphml` | Gephi, Cytoscape, yEd |
| `neo4j_import.cypher` | Neo4j (`cypher-shell`) |
| `graph.ttl` | Any RDF store (Blazegraph, Jena) |
| `nodes.parquet`, `edges.parquet` | pandas, DuckDB, Spark |
| `structure_images/` | Any image viewer |

## Two limits to remember

- **One search at a time.** A second search queues and starts automatically.
- **Exports are deleted about 30 minutes after the run.** Download what you
  need.

## Plans

| Plan | ₹ / month | Searches | Analyst messages |
| --- | --- | --- | --- |
| Free | 0 | 3 | 5 |
| Academic | 249 | 30 | 100 |
| Starter | 499 | 50 | 250 |
| Professional | 1,999 | 300 | 1,500 |
| Team | 7,499 | 3,000 | 10,000 |

## Command line

```bash
python api/scigraph.py "Aspirin" --hops 2
python api/scigraph.py --batch ./batch_queries.txt --export-dir ./exports
```

Batch files hold one query per line; `#` starts a comment.

## When something goes wrong

| Symptom | Do this |
| --- | --- |
| "Search lost — engine restarted" | The server was replaced. Run it again. |
| "Connection lost" | Check your network, then run again. |
| Almost no results | Use the full name or a database identifier. |
| Amber lines in the log | A database timed out and was skipped; harmless. |
| Payment not reflected | Do not pay twice. Reload the page. |

Full explanations: see the **User Tutorial**.
