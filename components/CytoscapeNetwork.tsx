'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type cytoscape from 'cytoscape';
import { AlertIcon, ChevronIcon, DownloadIcon, SpinnerIcon } from '@/components/icons';
import { cx, note } from '@/components/ui';
import { authHeaders } from '@/lib/billing';
import type { ExportFile, GraphNodeData, SearchGraph, SearchState } from '@/lib/types';

/*
 * The network, drawn by Cytoscape.js itself.
 *
 * The results screen used to explain that the graph could be opened "in
 * Cytoscape" while offering no way to open it: the button copied a URL that
 * pointed at an http:// origin (blocked as an insecure download), and nothing
 * on the page rendered a network at all. This is the actual Cytoscape renderer,
 * fed by the search's own nodes.csv / edges.csv, with the same layout,
 * selection and detail behaviour a desktop network tool has — and the signed
 * `.cyjs` URL is still one click away for opening the full graph in Cytoscape
 * desktop.
 *
 * Cytoscape is imported inside the effect rather than at module scope: it
 * touches `window` on load, and this file is evaluated during server rendering
 * as well.
 */

type CyCore = cytoscape.Core;

const LAYOUTS = [
  { id: 'cose', label: 'Force-directed' },
  { id: 'concentric', label: 'Concentric' },
  { id: 'breadthfirst', label: 'Hierarchy' },
  { id: 'circle', label: 'Circle' },
  { id: 'grid', label: 'Grid' },
] as const;

type LayoutId = (typeof LAYOUTS)[number]['id'];

const LABEL_LIMIT = 14;

interface Props {
  search: SearchState;
  files: ExportFile[];
}

interface Neighbour {
  data: GraphNodeData;
  relation: string;
  value: string;
}

/** Hide the entity types the reader switched off, and any edge that loses an
 *  endpoint to a filter. */
function applyHiddenTypes(cy: CyCore, hidden: Set<string>) {
  cy.batch(() => {
    cy.nodes().forEach((node) => {
      node.style(
        'display',
        hidden.has(String(node.data('type'))) ? 'none' : 'element'
      );
    });
    cy.edges().forEach((edge) => {
      const visible = edge.source().visible() && edge.target().visible();
      edge.style('display', visible ? 'element' : 'none');
    });
  });
}

/** A CSS custom property as a usable colour, with a fallback. */
function cssColor(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  if (!raw) return fallback;
  // Tokens are stored as "R G B" triplets so Tailwind can add opacity.
  return /^\d+\s+\d+\s+\d+$/.test(raw)
    ? `rgb(${raw.split(/\s+/).join(', ')})`
    : raw;
}

function layoutFor(name: LayoutId): cytoscape.LayoutOptions {
  switch (name) {
    case 'concentric':
      return {
        name: 'concentric',
        concentric: (node: cytoscape.NodeSingular) => node.degree(),
        levelWidth: () => 2,
        minNodeSpacing: 22,
        padding: 24,
        animate: false,
      } as cytoscape.LayoutOptions;
    case 'breadthfirst':
      return {
        name: 'breadthfirst',
        directed: true,
        spacingFactor: 1.2,
        padding: 24,
        animate: false,
      } as cytoscape.LayoutOptions;
    case 'circle':
      return { name: 'circle', padding: 28, animate: false } as cytoscape.LayoutOptions;
    case 'grid':
      return { name: 'grid', padding: 24, avoidOverlap: true } as cytoscape.LayoutOptions;
    default:
      return {
        name: 'cose',
        // Deterministic and quick: the graph is capped at 450 nodes, and an
        // animated layout would re-shuffle on every filter change.
        animate: false,
        randomize: false,
        numIter: 1200,
        nodeRepulsion: () => 12000,
        idealEdgeLength: () => 90,
        edgeElasticity: () => 90,
        gravity: 60,
        padding: 24,
      } as cytoscape.LayoutOptions;
  }
}

export default function CytoscapeNetwork({ search, files }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<CyCore | null>(null);

  const [graph, setGraph] = useState<SearchGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [layout, setLayout] = useState<LayoutId>('cose');
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [linkState, setLinkState] = useState<'idle' | 'busy' | 'copied' | 'error'>('idle');
  // Read by the (async) instance builder, which must not re-run on every
  // filter toggle just to pick the current filter set up.
  const hiddenRef = useRef<Set<string>>(new Set());
  hiddenRef.current = hiddenTypes;

  // ── Load the graph ─────────────────────────────────────────────────────
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
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = (await res.json()) as SearchGraph;
        if (alive) setGraph(body);
      } catch {
        if (alive) {
          setError(
            'The network could not be loaded. The export files are unaffected.'
          );
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, [search.search_id]);

  // ── Type legend, with counts, in the graph's own colours ───────────────
  const types = useMemo(() => {
    if (!graph) return [] as { type: string; count: number; color: string }[];
    const counts = new Map<string, { count: number; color: string }>();
    for (const node of graph.nodes) {
      const entry = counts.get(node.data.type);
      if (entry) entry.count += 1;
      else counts.set(node.data.type, { count: 1, color: node.data.color });
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1].count - a[1].count)
      .map(([type, { count, color }]) => ({ type, count, color }));
  }, [graph]);

  const degrees = useMemo(() => {
    const map = new Map<string, number>();
    for (const edge of graph?.edges ?? []) {
      map.set(edge.data.source, (map.get(edge.data.source) ?? 0) + 1);
      map.set(edge.data.target, (map.get(edge.data.target) ?? 0) + 1);
    }
    return map;
  }, [graph]);

  const selectedNode = useMemo(
    () => graph?.nodes.find((n) => n.data.id === selectedId)?.data ?? null,
    [graph, selectedId]
  );

  const neighbours = useMemo<Neighbour[]>(() => {
    if (!graph || !selectedId) return [];
    const byId = new Map(graph.nodes.map((n) => [n.data.id, n.data]));
    return graph.edges
      .filter((e) => e.data.source === selectedId || e.data.target === selectedId)
      .map((e) => {
        const otherId =
          e.data.source === selectedId ? e.data.target : e.data.source;
        const other = byId.get(otherId);
        return other
          ? { data: other, relation: e.data.relation, value: e.data.label }
          : null;
      })
      .filter((n): n is Neighbour => n !== null)
      .slice(0, 30);
  }, [graph, selectedId]);

  // ── Build the Cytoscape instance ───────────────────────────────────────
  useEffect(() => {
    if (!graph || !containerRef.current) return;
    let disposed = false;

    (async () => {
      const mod = await import('cytoscape');
      const cytoscapeFn = (mod.default ?? mod) as typeof cytoscape;
      if (disposed || !containerRef.current) return;

      cyRef.current?.destroy();

      const labelled = new Set(
        [...graph.nodes]
          .sort(
            (a, b) =>
              (degrees.get(b.data.id) ?? 0) - (degrees.get(a.data.id) ?? 0)
          )
          .slice(0, LABEL_LIMIT)
          .map((n) => n.data.id)
      );

      const elements = [
        ...graph.nodes.map((node) => ({
          data: {
            id: node.data.id,
            name: node.data.name,
            type: node.data.type,
            color: node.data.color,
            // Labelling every node is unreadable; hubs carry the names.
            label: labelled.has(node.data.id) ? node.data.label : '',
            degree: degrees.get(node.data.id) ?? 0,
          },
        })),
        ...graph.edges.map((edge, i) => ({
          data: {
            id: `e${i}`,
            source: edge.data.source,
            target: edge.data.target,
            label: edge.data.label ?? '',
            relation: edge.data.relation ?? '',
          },
        })),
      ];

      const muted = cssColor('--fg-subtle', '#8b99ab');
      const line = cssColor('--line-strong', '#3f4a59');
      const accent = cssColor('--accent', '#38cde2');
      const fg = cssColor('--fg', '#e8edf3');
      const canvas = cssColor('--bg-sunken', '#050609');

      const style: cytoscape.StylesheetStyle[] = [
        {
          selector: 'node',
          style: {
            'background-color': 'data(color)',
            width: 'mapData(degree, 0, 30, 11, 34)',
            height: 'mapData(degree, 0, 30, 11, 34)',
            label: 'data(label)',
            'font-size': 9,
            'font-family': 'Inter, system-ui, sans-serif',
            color: fg,
            'text-outline-color': canvas,
            'text-outline-width': 2.5,
            'text-outline-opacity': 1,
            'text-valign': 'top',
            'text-halign': 'center',
            'text-margin-y': -3,
            'text-wrap': 'wrap',
            'text-max-width': '96px',
            'border-width': 1,
            'border-color': canvas,
            'overlay-opacity': 0,
          },
        },
        {
          selector: 'node:selected',
          style: {
            'border-width': 3,
            'border-color': accent,
            'text-outline-width': 3,
          },
        },
        {
          selector: 'node.hover',
          style: { 'border-width': 2, 'border-color': accent },
        },
        {
          selector: 'node.faded',
          style: { opacity: 0.18 },
        },
        {
          selector: 'edge',
          style: {
            width: 1,
            'line-color': line,
            'curve-style': 'bezier',
            'target-arrow-shape': 'triangle',
            'target-arrow-color': line,
            'arrow-scale': 0.7,
            opacity: 0.55,
            label: 'data(label)',
            'font-size': 7,
            color: muted,
            'text-outline-color': canvas,
            'text-outline-width': 2,
            'text-rotation': 'autorotate',
            'overlay-opacity': 0,
          },
        },
        {
          selector: 'edge.highlight',
          style: {
            'line-color': accent,
            'target-arrow-color': accent,
            width: 1.8,
            opacity: 0.95,
            'z-index': 20,
          },
        },
        {
          selector: 'edge.faded',
          style: { opacity: 0.06 },
        },
      ];

      const cy = cytoscapeFn({
        container: containerRef.current,
        elements,
        style,
        layout: layoutFor(layout),
        minZoom: 0.15,
        maxZoom: 4,
        wheelSensitivity: 0.25,
        boxSelectionEnabled: false,
        selectionType: 'single',
      });

      cyRef.current = cy;

      // Hover: dim everything that is not this node, its neighbours or the
      // edges between them — the desktop-tool behaviour, not a static picture.
      const clearHover = () => {
        cy.elements().removeClass('faded hover highlight');
      };
      cy.on('mouseover', 'node', (event) => {
        const node = event.target;
        const hood = node.closedNeighborhood();
        cy.elements().not(hood).addClass('faded');
        hood.removeClass('faded');
        node.addClass('hover');
        node.connectedEdges().addClass('highlight');
      });
      cy.on('mouseout', 'node', clearHover);

      cy.on('tap', 'node', (event) => {
        setSelectedId(String(event.target.id()));
      });
      cy.on('tap', (event) => {
        if (event.target === cy) setSelectedId(null);
      });

      // A rebuild must restore the filters the reader already applied.
      applyHiddenTypes(cy, hiddenRef.current);
    })();

    return () => {
      disposed = true;
      cyRef.current?.destroy();
      cyRef.current = null;
    };
    // The instance is rebuilt when the data or the chosen layout changes; the
    // type filters are applied without discarding it (below).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, degrees, layout]);

  // ── Type filters, applied to the live instance ─────────────────────────
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    applyHiddenTypes(cy, hiddenTypes);
  }, [hiddenTypes, graph]);

  // The container changes size in the expanded view, and Cytoscape keeps its
  // old canvas metrics until it is told; without this the graph is drawn in a
  // corner of a full-screen canvas.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const timer = window.setTimeout(() => {
      cy.resize();
      cy.fit(undefined, 30);
    }, 80);
    return () => window.clearTimeout(timer);
  }, [expanded]);

  // ── Canvas actions ─────────────────────────────────────────────────────
  const fit = useCallback(() => cyRef.current?.fit(undefined, 30), []);

  const zoomBy = useCallback((factor: number) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({
      level: Math.max(0.15, Math.min(4, cy.zoom() * factor)),
      renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
    });
  }, []);

  /** Centre the canvas on one node and select it. */
  const focusNode = useCallback((id: string) => {
    const cy = cyRef.current;
    setSelectedId(id);
    if (!cy) return;
    const node = cy.getElementById(id);
    if (node.empty()) return;
    cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 1.1) }, { duration: 220 });
  }, []);

  const savePng = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const uri = cy.png({ bg: 'transparent', scale: 2, full: true });
    const a = document.createElement('a');
    a.href = uri;
    a.download = `${search.query.replace(/[^\w.-]+/g, '_') || 'network'}-network.png`;
    a.click();
  }, [search.query]);

  /** The `.cyjs` / `.graphml` file this search exported, if any. */
  const cytoFile = useMemo(
    () =>
      files.find((f) => f.name.endsWith('.cyjs')) ??
      files.find((f) => f.name.endsWith('.graphml')) ??
      null,
    [files]
  );

  /**
   * Copy a signed URL for desktop Cytoscape.
   *
   * Browsers carry the session cookie for a download, but Cytoscape's
   * "Network from URL" sends no cookie, so it needs a capability URL — minted
   * by the engine against its *public* origin, otherwise the copied link is
   * `http://` and the import fails.
   */
  const copyCytoscapeLink = useCallback(async () => {
    if (!cytoFile) return;
    setLinkState('busy');
    try {
      const res = await fetch(
        `/api/search/${encodeURIComponent(search.search_id)}/export-link?file=${encodeURIComponent(cytoFile.name)}`,
        { headers: await authHeaders() }
      );
      const body = await res.json().catch(() => null);
      if (!res.ok || typeof body?.url !== 'string') throw new Error('no link');
      await navigator.clipboard.writeText(body.url);
      setLinkState('copied');
      window.setTimeout(() => setLinkState('idle'), 4000);
    } catch {
      setLinkState('error');
    }
  }, [cytoFile, search.search_id]);

  const toggleType = (type: string) => {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  // Escape closes the expanded view.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setExpanded(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [expanded]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-1 py-12 text-xs text-fg-subtle">
        <SpinnerIcon className="h-3.5 w-3.5 animate-spin-slow" />
        Building the network…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-start gap-2" role="alert">
        <AlertIcon className="mt-px h-4 w-4 shrink-0" />
        <p className={note('danger')}>{error}</p>
      </div>
    );
  }

  if (!graph || graph.nodes.length === 0) {
    return (
      <p className="py-8 text-xs leading-relaxed text-fg-muted">
        This search produced no network data to draw. Its export files are
        still complete — open the Files tab.
      </p>
    );
  }

  const canvas = (
    <div
      className={cx(
        'relative overflow-hidden rounded-lg border border-line bg-sunken',
        expanded ? 'h-[calc(100vh-8rem)]' : 'h-[480px]'
      )}
    >
      <div ref={containerRef} className="h-full w-full" />

      {/* Floating controls, top-left: layout and view actions. */}
      <div className="pointer-events-none absolute left-2 top-2 flex flex-wrap items-center gap-1.5">
        <div className="pointer-events-auto flex items-center gap-1 rounded-md border border-line bg-surface/95 p-1 shadow-sm backdrop-blur">
          <label className="sr-only" htmlFor="network-layout">
            Layout
          </label>
          <select
            id="network-layout"
            value={layout}
            onChange={(e) => setLayout(e.target.value as LayoutId)}
            className="rounded border-0 bg-transparent px-1.5 py-1 text-[11px] font-medium text-fg-muted focus:ring-1 focus:ring-accent"
          >
            {LAYOUTS.map((l) => (
              <option key={l.id} value={l.id}>
                {l.label}
              </option>
            ))}
          </select>
          <span aria-hidden className="h-4 w-px bg-line" />
          <button
            type="button"
            onClick={fit}
            className="rounded px-1.5 py-1 text-[11px] font-medium text-fg-muted hover:bg-surface-2 hover:text-fg"
          >
            Fit
          </button>
          <button
            type="button"
            onClick={() => zoomBy(1.25)}
            aria-label="Zoom in"
            className="rounded px-1.5 py-1 text-[12px] leading-none text-fg-muted hover:bg-surface-2 hover:text-fg"
          >
            +
          </button>
          <button
            type="button"
            onClick={() => zoomBy(0.8)}
            aria-label="Zoom out"
            className="rounded px-1.5 py-1 text-[12px] leading-none text-fg-muted hover:bg-surface-2 hover:text-fg"
          >
            −
          </button>
          <button
            type="button"
            onClick={savePng}
            title="Save this view as a PNG"
            className="rounded px-1.5 py-1 text-[11px] font-medium text-fg-muted hover:bg-surface-2 hover:text-fg"
          >
            PNG
          </button>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="rounded px-1.5 py-1 text-[11px] font-medium text-fg-muted hover:bg-surface-2 hover:text-fg"
          >
            {expanded ? 'Close' : 'Expand'}
          </button>
        </div>
      </div>

      <p className="pointer-events-none absolute bottom-2 left-2 rounded bg-surface/85 px-1.5 py-0.5 text-[10px] text-fg-subtle backdrop-blur">
        Rendered with Cytoscape.js · scroll to zoom · drag to pan · click a node
      </p>
    </div>
  );

  const body = (
    <div className="space-y-3">
      {/* Legend / filters */}
      <div className="flex flex-wrap items-center gap-1.5">
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
        <span className="ml-auto text-[11px] tabular-nums text-fg-subtle">
          {graph.node_total.toLocaleString()} entities ·{' '}
          {graph.edge_total.toLocaleString()} relationships
        </span>
      </div>

      <div className={cx(expanded && 'fixed inset-0 z-50 flex flex-col bg-canvas p-4')}>
        {expanded && (
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-fg">Network — {search.query}</p>
              <p className="text-xs text-fg-subtle">
                {graph.node_total.toLocaleString()} entities ·{' '}
                {graph.edge_total.toLocaleString()} relationships
              </p>
            </div>
            <button
              type="button"
              onClick={() => setExpanded(false)}
              className="rounded-md border border-line px-2.5 py-1 text-xs text-fg-muted hover:text-fg"
            >
              Close
            </button>
          </div>
        )}
        {canvas}
      </div>

      {graph.truncated && (
        <p className="text-[11px] leading-relaxed text-fg-subtle">
          Showing the {graph.node_count} highest-ranked entities of{' '}
          {graph.node_total.toLocaleString()}. The complete graph is in the
          exports — GraphML and Cytoscape JSON open directly in Cytoscape.
        </p>
      )}

      {/* Selection detail */}
      {selectedNode ? (
        <div className="rounded-lg border border-line bg-surface-2 p-3.5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-fg-muted">
                <span
                  aria-hidden
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ backgroundColor: selectedNode.color }}
                />
                {selectedNode.type}
              </span>
              <p className="mt-1.5 break-words text-sm font-medium text-fg">
                {selectedNode.name}
              </p>
              {selectedNode.canonical_id && (
                <p className="mt-0.5 break-all font-mono text-[11px] text-fg-subtle">
                  {selectedNode.canonical_id}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className="shrink-0 text-[11px] text-fg-subtle hover:text-fg"
            >
              Clear
            </button>
          </div>

          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
            {selectedNode.formula && (
              <div>
                <dt className="text-fg-subtle">Formula</dt>
                <dd className="text-fg-muted">{selectedNode.formula}</dd>
              </div>
            )}
            {selectedNode.molecular_weight && (
              <div>
                <dt className="text-fg-subtle">Molecular weight</dt>
                <dd className="tabular-nums text-fg-muted">
                  {selectedNode.molecular_weight} g/mol
                </dd>
              </div>
            )}
            {selectedNode.cross_references && (
              <div className="col-span-2">
                <dt className="text-fg-subtle">Cross-references</dt>
                <dd className="mt-0.5 break-words font-mono text-[11px] text-fg-muted">
                  {selectedNode.cross_references}
                </dd>
              </div>
            )}
            {selectedNode.smiles && (
              <div className="col-span-2">
                <dt className="text-fg-subtle">SMILES</dt>
                <dd className="mt-0.5 break-all font-mono text-[11px] text-fg-muted">
                  {selectedNode.smiles}
                </dd>
              </div>
            )}
            {selectedNode.bioactivity_summary && (
              <div className="col-span-2">
                <dt className="text-fg-subtle">Evidence</dt>
                <dd className="mt-0.5 leading-relaxed text-fg-muted">
                  {selectedNode.bioactivity_summary}
                </dd>
              </div>
            )}
          </dl>

          {neighbours.length > 0 && (
            <div className="mt-3 border-t border-line pt-2.5">
              <p className="text-[11px] font-semibold uppercase tracking-[0.07em] text-fg-subtle">
                Relationships ({neighbours.length})
              </p>
              <ul className="mt-1.5 max-h-48 space-y-1 overflow-y-auto pr-1">
                {neighbours.map(({ data, relation, value }, i) => (
                  <li key={`${data.id}-${i}`} className="flex items-baseline gap-2 text-xs">
                    <span className="shrink-0 font-mono text-[10px] uppercase text-accent">
                      {relation}
                    </span>
                    <button
                      type="button"
                      onClick={() => focusNode(data.id)}
                      className="min-w-0 flex-1 truncate text-left text-fg-muted hover:text-fg hover:underline"
                    >
                      {data.name}
                    </button>
                    {value && value !== relation && (
                      <span className="shrink-0 tabular-nums text-fg-subtle">
                        {value}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ) : (
        <p className="flex items-center gap-1.5 text-[11px] text-fg-subtle">
          <ChevronIcon className="h-3 w-3 rotate-90" />
          Select a node to inspect its identifiers, evidence and relationships.
        </p>
      )}

      {/* Desktop Cytoscape hand-off */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2.5">
        <p className="min-w-0 flex-1 text-[11px] leading-relaxed text-fg-muted">
          {cytoFile ? (
            <>
              Open the complete graph in Cytoscape desktop:{' '}
              <span className="font-mono text-[10px] text-fg-subtle">
                File → Import → Network from URL
              </span>
              . The link is signed and expires.
            </>
          ) : (
            'Cytoscape JSON is generated with every completed search; this one has no .cyjs file yet.'
          )}
        </p>
        {cytoFile && (
          <div className="flex shrink-0 items-center gap-1.5">
            <a
              href={`/api/exports/${encodeURIComponent(cytoFile.name)}?search_id=${encodeURIComponent(search.search_id)}`}
              className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] font-medium text-fg-muted hover:border-accent hover:text-accent"
            >
              <DownloadIcon className="h-3 w-3" />
              {cytoFile.name.endsWith('.cyjs') ? '.cyjs' : '.graphml'}
            </a>
            <button
              type="button"
              onClick={copyCytoscapeLink}
              disabled={linkState === 'busy'}
              className="rounded-md border border-accent px-2 py-1 text-[11px] font-medium text-accent hover:bg-accent-soft disabled:opacity-55"
            >
              {linkState === 'busy'
                ? 'Signing…'
                : linkState === 'copied'
                  ? 'Link copied'
                  : 'Copy Cytoscape link'}
            </button>
          </div>
        )}
      </div>
      {linkState === 'error' && (
        <p className={note('danger')}>
          Could not sign a link for this file. Download the .cyjs file and open
          it from disk instead.
        </p>
      )}
    </div>
  );

  return body;
}

