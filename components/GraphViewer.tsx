'use client';

import { useEffect, useMemo, useState } from 'react';
import { AlertIcon, ChevronIcon, SpinnerIcon } from '@/components/icons';
import { cx, note } from '@/components/ui';
import { authHeaders } from '@/lib/billing';
import type { GraphEdge, GraphNode, SearchGraph, SearchState } from '@/lib/types';

/*
 * Interactive view of the knowledge graph a search actually built.
 *
 * Everything drawn here comes from the search's own nodes.csv / edges.csv —
 * these are the same entities and relationships that are in the downloadable
 * exports, not a separate sample. The canvas shows the relevance-ordered head
 * of a large graph (the CSV is already ranked by the engine), and says so when
 * it does.
 */

interface Props {
  search: SearchState;
}

const WIDTH = 760;
const HEIGHT = 430;
const LABEL_LIMIT = 10;

interface Position {
  x: number;
  y: number;
}

/**
 * Lay the graph out with a small force simulation.
 *
 * Deterministic on purpose: seeds come from the node order, so the same search
 * draws the same picture every time instead of shuffling on each render. The
 * iteration count and the O(n²) pass are fine for the 450-node cap that the
 * API enforces.
 */
function computeLayout(
  nodes: GraphNode[],
  edges: GraphEdge[]
): Map<string, Position> {
  const pos = new Map<string, Position>();
  const n = nodes.length;
  if (n === 0) return pos;

  const cx0 = WIDTH / 2;
  const cy0 = HEIGHT / 2;
  // Golden-angle spiral: even spacing with no overlaps at the start, which is
  // what keeps a force pass from needing many iterations to untangle itself.
  const golden = Math.PI * (3 - Math.sqrt(5));
  nodes.forEach((node, i) => {
    const r = Math.sqrt((i + 0.5) / n) * Math.min(WIDTH, HEIGHT) * 0.44;
    const angle = i * golden;
    pos.set(node.data.id, {
      x: cx0 + r * Math.cos(angle),
      y: cy0 + r * Math.sin(angle),
    });
  });

  // Index lookup once: resolving endpoints with findIndex inside the force
  // loop is what makes a naive layout crawl on a 450-node graph.
  const indexOf = new Map(nodes.map((node, i) => [node.data.id, i]));
  const pairs: Array<[number, number]> = [];
  for (const edge of edges) {
    const a = indexOf.get(edge.data.source);
    const b = indexOf.get(edge.data.target);
    if (a !== undefined && b !== undefined) pairs.push([a, b]);
  }

  const iterations = n > 250 ? 40 : 60;
  const k = Math.sqrt((WIDTH * HEIGHT) / n) * 0.55;
  const dx = new Float64Array(n);
  const dy = new Float64Array(n);

  for (let it = 0; it < iterations; it++) {
    dx.fill(0);
    dy.fill(0);
    const temperature = 18 * (1 - it / iterations) + 0.6;

    // Repulsion between every pair.
    for (let i = 0; i < n; i++) {
      const a = pos.get(nodes[i].data.id)!;
      for (let j = i + 1; j < n; j++) {
        const b = pos.get(nodes[j].data.id)!;
        let vx = a.x - b.x;
        let vy = a.y - b.y;
        let dist = Math.hypot(vx, vy);
        if (dist < 0.01) {
          // Perfectly coincident nodes would divide by zero; nudge them apart.
          vx = (i % 2 === 0 ? 1 : -1) * 0.1;
          vy = (j % 2 === 0 ? 1 : -1) * 0.1;
          dist = 0.14;
        }
        const force = (k * k) / (dist * dist);
        const fx = (vx / dist) * force;
        const fy = (vy / dist) * force;
        dx[i] += fx;
        dy[i] += fy;
        dx[j] -= fx;
        dy[j] -= fy;
      }
    }

    // Attraction along relationships.
    for (const [ia, ib] of pairs) {
      const a = pos.get(nodes[ia].data.id)!;
      const b = pos.get(nodes[ib].data.id)!;
      const vx = a.x - b.x;
      const vy = a.y - b.y;
      const dist = Math.hypot(vx, vy) || 0.01;
      const force = (dist * dist) / k;
      const fx = (vx / dist) * force;
      const fy = (vy / dist) * force;
      dx[ia] -= fx;
      dy[ia] -= fy;
      dx[ib] += fx;
      dy[ib] += fy;
    }

    for (let i = 0; i < n; i++) {
      const node = pos.get(nodes[i].data.id)!;
      const len = Math.hypot(dx[i], dy[i]) || 1;
      const step = Math.min(len, temperature);
      node.x += (dx[i] / len) * step;
      node.y += (dy[i] / len) * step;
      node.x = Math.min(WIDTH - 14, Math.max(14, node.x));
      node.y = Math.min(HEIGHT - 14, Math.max(14, node.y));
    }
  }

  return pos;
}

export default function GraphViewer({ search }: Props) {
  const [graph, setGraph] = useState<SearchGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const res = await fetch(
          `/api/search/${encodeURIComponent(search.search_id)}/graph`,
          { headers: await authHeaders() }
        );
        if (!alive) return;
        if (res.status === 404) {
          setGraph(null);
          setLoading(false);
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = (await res.json()) as SearchGraph;
        if (alive) {
          setGraph(body);
          setLoading(false);
        }
      } catch {
        if (alive) {
          setError('The network view could not be loaded. The export files below are unaffected.');
          setLoading(false);
        }
      }
    })();

    return () => {
      alive = false;
    };
  }, [search.search_id]);

  const types = useMemo(() => {
    if (!graph) return [];
    const counts = new Map<string, number>();
    for (const node of graph.nodes) {
      counts.set(node.data.type, (counts.get(node.data.type) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([type, count]) => ({
        type,
        count,
        color: graph.nodes.find((n) => n.data.type === type)?.data.color ?? '#6b7280',
      }));
  }, [graph]);

  const visibleNodes = useMemo(
    () => (graph ? graph.nodes.filter((n) => !hiddenTypes.has(n.data.type)) : []),
    [graph, hiddenTypes]
  );

  const visibleIds = useMemo(
    () => new Set(visibleNodes.map((n) => n.data.id)),
    [visibleNodes]
  );

  const visibleEdges = useMemo(
    () =>
      graph
        ? graph.edges.filter(
            (e) => visibleIds.has(e.data.source) && visibleIds.has(e.data.target)
          )
        : [],
    [graph, visibleIds]
  );

  const positions = useMemo(
    () => computeLayout(visibleNodes, visibleEdges),
    [visibleNodes, visibleEdges]
  );

  // Degree decides how big a node is drawn and which few get a label: the hubs
  // are what a reader needs, and labelling 450 nodes is unreadable.
  const degrees = useMemo(() => {
    const map = new Map<string, number>();
    for (const edge of visibleEdges) {
      map.set(edge.data.source, (map.get(edge.data.source) ?? 0) + 1);
      map.set(edge.data.target, (map.get(edge.data.target) ?? 0) + 1);
    }
    return map;
  }, [visibleEdges]);

  const labelled = useMemo(() => {
    return new Set(
      [...visibleNodes]
        .sort((a, b) => (degrees.get(b.data.id) ?? 0) - (degrees.get(a.data.id) ?? 0))
        .slice(0, LABEL_LIMIT)
        .map((n) => n.data.id)
    );
  }, [visibleNodes, degrees]);

  const selectedNode = useMemo(
    () => visibleNodes.find((n) => n.data.id === selected) ?? null,
    [visibleNodes, selected]
  );

  const connections = useMemo(() => {
    if (!selected || !graph) return [];
    const byId = new Map(graph.nodes.map((n) => [n.data.id, n.data]));
    return graph.edges
      .filter((e) => e.data.source === selected || e.data.target === selected)
      .map((e) => {
        const otherId =
          e.data.source === selected ? e.data.target : e.data.source;
        return { edge: e.data, other: byId.get(otherId) };
      })
      .filter((c) => c.other)
      .slice(0, 12);
  }, [selected, graph]);

  const toggleType = (type: string) => {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  return (
    <section className="border-t border-line">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-5 py-4">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-fg">Knowledge graph</h3>
          <p className="mt-0.5 text-xs text-fg-muted">
            {graph
              ? `${graph.node_total.toLocaleString()} entities · ${graph.edge_total.toLocaleString()} relationships extracted for “${search.query}”`
              : `The network this search built for “${search.query}”`}
          </p>
        </div>
        {graph && (
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => setZoom((z) => Math.max(1, Number((z - 0.25).toFixed(2))))}
              disabled={zoom <= 1}
              className="rounded border border-line px-2 py-0.5 text-xs text-fg-muted transition-colors hover:text-fg disabled:opacity-40"
              aria-label="Zoom out"
            >
              −
            </button>
            <button
              type="button"
              onClick={() => setZoom((z) => Math.min(3, Number((z + 0.25).toFixed(2))))}
              disabled={zoom >= 3}
              className="rounded border border-line px-2 py-0.5 text-xs text-fg-muted transition-colors hover:text-fg disabled:opacity-40"
              aria-label="Zoom in"
            >
              +
            </button>
            <button
              type="button"
              onClick={() => setZoom(1)}
              className="rounded border border-line px-2 py-0.5 text-xs text-fg-muted transition-colors hover:text-fg"
            >
              Reset
            </button>
          </div>
        )}
      </div>

      {loading && (
        <div className="flex items-center gap-2 border-t border-line px-5 py-8 text-xs text-fg-subtle">
          <SpinnerIcon className="h-3.5 w-3.5 animate-spin-slow" />
          Reading the extracted network…
        </div>
      )}

      {!loading && !graph && !error && (
        <p className="border-t border-line px-5 py-4 text-xs leading-relaxed text-fg-muted">
          This search has no network data to draw — the export files below are
          still complete.
        </p>
      )}

      {error && (
        <div className="flex items-start gap-2 border-t border-line px-5 py-4" role="alert">
          <AlertIcon className="mt-px h-4 w-4 shrink-0" />
          <p className={note('danger')}>{error}</p>
        </div>
      )}

      {graph && (
        <>
          {types.length > 1 && (
            <div className="flex flex-wrap gap-1.5 border-t border-line px-5 py-2.5">
              {types.map(({ type, count, color }) => {
                const on = !hiddenTypes.has(type);
                return (
                  <button
                    key={type}
                    type="button"
                    onClick={() => toggleType(type)}
                    aria-pressed={on}
                    className={cx(
                      'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors',
                      on
                        ? 'border-line bg-surface-2 text-fg'
                        : 'border-line/60 bg-transparent text-fg-subtle line-through'
                    )}
                  >
                    <span
                      aria-hidden
                      className="h-2 w-2 rounded-full"
                      style={{ backgroundColor: color, opacity: on ? 1 : 0.35 }}
                    />
                    {type}
                    <span className="tabular-nums text-fg-subtle">{count}</span>
                  </button>
                );
              })}
            </div>
          )}

          <div className="border-t border-line bg-sunken">
            <svg
              viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
              className="block h-auto w-full select-none"
              role="img"
              aria-label={`Network of ${visibleNodes.length} entities and ${visibleEdges.length} relationships extracted for ${search.query}`}
            >
              <g
                transform={`translate(${WIDTH / 2} ${HEIGHT / 2}) scale(${zoom}) translate(${-WIDTH / 2} ${-HEIGHT / 2})`}
              >
                {visibleEdges.map((edge, i) => {
                  const a = positions.get(edge.data.source);
                  const b = positions.get(edge.data.target);
                  if (!a || !b) return null;
                  const touchesSelection =
                    selected &&
                    (edge.data.source === selected || edge.data.target === selected);
                  return (
                    <line
                      key={`${edge.data.source}-${edge.data.target}-${i}`}
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      stroke="currentColor"
                      strokeWidth={touchesSelection ? 1.4 : 0.6}
                      className={touchesSelection ? 'text-accent' : 'text-fg-subtle'}
                      opacity={selected ? (touchesSelection ? 0.9 : 0.12) : 0.35}
                    />
                  );
                })}

                {visibleNodes.map((node) => {
                  const at = positions.get(node.data.id);
                  if (!at) return null;
                  const degree = degrees.get(node.data.id) ?? 0;
                  const r = Math.min(11, 3.5 + Math.sqrt(degree) * 1.7);
                  const isSelected = node.data.id === selected;
                  const showLabel = isSelected || (!selected && labelled.has(node.data.id));
                  return (
                    <g key={node.data.id}>
                      <circle
                        cx={at.x}
                        cy={at.y}
                        r={isSelected ? r + 2 : r}
                        fill={node.data.color}
                        stroke={isSelected ? 'currentColor' : 'none'}
                        strokeWidth={2}
                        className={cx('cursor-pointer transition-opacity', isSelected ? 'text-accent' : '')}
                        opacity={selected && !isSelected ? 0.45 : 0.95}
                        onClick={() => setSelected(isSelected ? null : node.data.id)}
                      >
                        <title>{`${node.data.name} (${node.data.type})`}</title>
                      </circle>
                      {(showLabel || (selected && highlightedNeighbour(selected, graph, node.data.id))) && (
                        <text
                          x={at.x}
                          y={at.y - r - 4}
                          textAnchor="middle"
                          className="pointer-events-none fill-current text-fg-muted"
                          fontSize={9}
                        >
                          {node.data.label}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            </svg>
          </div>

          {graph.truncated && (
            <p className="border-t border-line px-5 py-2 text-[11px] leading-relaxed text-fg-subtle">
              Showing the {graph.node_count} highest-ranked entities of{' '}
              {graph.node_total.toLocaleString()} — the full network is in the
              graph exports below, including GraphML for Cytoscape and Cypher for
              Neo4j.
            </p>
          )}

          {selectedNode ? (
            <div className="border-t border-line px-5 py-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <span
                    className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-fg-muted"
                  >
                    <span
                      aria-hidden
                      className="h-1.5 w-1.5 rounded-full"
                      style={{ backgroundColor: selectedNode.data.color }}
                    />
                    {selectedNode.data.type}
                  </span>
                  <p className="mt-1.5 break-words text-sm font-medium text-fg">
                    {selectedNode.data.name}
                  </p>
                  {selectedNode.data.canonical_id && (
                    <p className="mt-0.5 font-mono text-[11px] text-fg-subtle">
                      {selectedNode.data.canonical_id}
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setSelected(null)}
                  className="shrink-0 text-[11px] text-fg-subtle transition-colors hover:text-fg"
                >
                  Close
                </button>
              </div>

              <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                {selectedNode.data.cross_references && (
                  <div className="col-span-2">
                    <dt className="text-fg-subtle">Cross-references</dt>
                    <dd className="mt-0.5 break-words font-mono text-[11px] text-fg-muted">
                      {selectedNode.data.cross_references}
                    </dd>
                  </div>
                )}
                {selectedNode.data.formula && (
                  <div>
                    <dt className="text-fg-subtle">Formula</dt>
                    <dd className="text-fg-muted">{selectedNode.data.formula}</dd>
                  </div>
                )}
                {selectedNode.data.molecular_weight && (
                  <div>
                    <dt className="text-fg-subtle">Molecular weight</dt>
                    <dd className="tabular-nums text-fg-muted">
                      {selectedNode.data.molecular_weight} g/mol
                    </dd>
                  </div>
                )}
                {selectedNode.data.smiles && (
                  <div className="col-span-2">
                    <dt className="text-fg-subtle">SMILES</dt>
                    <dd className="mt-0.5 break-all font-mono text-[11px] text-fg-muted">
                      {selectedNode.data.smiles}
                    </dd>
                  </div>
                )}
                {selectedNode.data.bioactivity_summary && (
                  <div className="col-span-2">
                    <dt className="text-fg-subtle">Evidence</dt>
                    <dd className="mt-0.5 leading-relaxed text-fg-muted">
                      {selectedNode.data.bioactivity_summary}
                    </dd>
                  </div>
                )}
              </dl>

              {connections.length > 0 && (
                <div className="mt-3">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.07em] text-fg-subtle">
                    Relationships ({connections.length})
                  </p>
                  <ul className="mt-1.5 space-y-1">
                    {connections.map(({ edge, other }, i) => (
                      <li key={i} className="flex items-baseline gap-2 text-xs">
                        <span className="font-mono text-[10px] uppercase text-accent">
                          {edge.relation}
                        </span>
                        <button
                          type="button"
                          onClick={() => setSelected(other!.id)}
                          className="min-w-0 flex-1 truncate text-left text-fg-muted hover:text-fg hover:underline"
                        >
                          {other!.name}
                        </button>
                        {edge.label && edge.label !== edge.relation && (
                          <span className="shrink-0 tabular-nums text-fg-subtle">
                            {edge.label}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <p className="flex items-center gap-1.5 border-t border-line px-5 py-2.5 text-[11px] text-fg-subtle">
              <ChevronIcon className="h-3 w-3 rotate-90" />
              Select any node to inspect its identifiers, evidence and
              relationships.
            </p>
          )}
        </>
      )}
    </section>
  );
}

/** True when `id` is directly connected to the selected node. */
function highlightedNeighbour(
  selected: string,
  graph: SearchGraph,
  id: string
): boolean {
  return graph.edges.some(
    (e) =>
      (e.data.source === selected && e.data.target === id) ||
      (e.data.target === selected && e.data.source === id)
  );
}
