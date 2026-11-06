export interface SearchRequest {
  query: string;
  query_type: 'protein' | 'ligand' | 'auto';
  hops: number;
}

export interface ExportFile {
  name: string;
  size_bytes: number;
  size_display: string;
  url: string;
}

export interface SearchArchive {
  provider: 'gcs';
  bucket: string;
  object: string;
  filename: string;
  size_bytes: number;
  size_display: string;
  uploaded_at: string;
}

export interface SearchState {
  search_id: string;
  query: string;
  query_type: string;
  hops: number;
  status: 'queued' | 'running' | 'completed' | 'failed';
  progress: string | null;
  /** Which of the engine's own pipeline steps is running (1..step_total). */
  step?: number | null;
  step_total?: number | null;
  /** The engine's title for the current step, as it printed it. */
  step_label?: string | null;
  log: string[];
  export_files: ExportFile[];
  /** Durable archive in Google Cloud Storage, when the engine made one. */
  storage?: SearchArchive | null;
  export_dir: string;
  /** base64 file payloads returned by the hosted Python engine (production) */
  base64_files?: Record<string, string>;
  /** Hosted Python engine (Render) binding — set when SEARCH_ENGINE_URL is used */
  remote_engine_url?: string;
  /** Search id on the hosted Python engine */
  remote_search_id?: string;
  /** Consecutive failed refreshes against the hosted engine */
  remote_failures?: number;
  /** Epoch ms of the last observed change in remote status/progress/log size */
  remote_last_change_ms?: number;
  /** Fingerprint of the last remote snapshot, for stall detection */
  remote_last_fingerprint?: string;
  created_at: string;
  elapsed_seconds: number | null;
  error: string | null;
}

export interface SearchSummary {
  search_id: string;
  query: string;
  hops?: number | null;
  status: string;
  progress: string | null;
  created_at: string;
  elapsed_seconds: number | null;
  file_count: number;
}

export interface LogResponse {
  search_id: string;
  status: string;
  offset: number;
  total_lines: number;
  new_lines: string[];
}

/** One entity in the interactive network view, straight from nodes.csv. */
export interface GraphNodeData {
  id: string;
  label: string;
  name: string;
  type: string;
  color: string;
  canonical_id: string;
  cross_references: string;
  smiles: string;
  formula: string;
  molecular_weight: string;
  bioactivity_summary: string;
}

export interface GraphEdgeData {
  source: string;
  target: string;
  label: string;
  relation: string;
  pchembl: string | null;
}

export interface GraphNode {
  data: GraphNodeData;
}

export interface GraphEdge {
  data: GraphEdgeData;
}

/**
 * The real graph behind a finished search, read from its own nodes.csv and
 * edges.csv. Capped for the canvas: `truncated` says so, and the totals let
 * the UI state honestly how much of the network is on screen.
 */
export interface SearchGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  node_count: number;
  edge_count: number;
  node_total: number;
  edge_total: number;
  truncated: boolean;
}

/** Decision intelligence computed by the engine from the finished graph. */
export interface DecisionIntel {
  search_id: string;
  query: string;
  node_count: number;
  edge_count: number;
  feasibility: {
    grade: string;
    label: string | null;
    scored_compounds: number;
    affinity_datapoints: number;
    median_pchembl?: number;
    method: string;
    compounds: Array<{
      uid: string;
      name: string;
      grade: string;
      label: string;
      potency_score: number;
      datapoints: number;
      best_value: number;
    }>;
    note: string;
  };
  ip: {
    status: 'green' | 'yellow' | 'red';
    reason: string;
    patents: Array<{ uid: string; name: string; year: number | null }>;
    caveat: string;
  };
  dark_literature: {
    patent_count: number;
    compounds_with_patents: number;
    overlap_signals: number;
    compounds: Array<{ uid: string; name: string; patent_count: number }>;
    reading: string;
    caveat: string;
  };
  moa: {
    query: string;
    targets: Array<{ name: string; best: MoaEvidence; count: number }>;
    structures: Array<{ pdb_id: string; compound: string }>;
    potency_edges: MoaEvidence[];
    compounds: Array<{ uid: string; name: string; smiles: string }>;
    publications: number;
    patents: number;
  };
  vendors: {
    compounds: Array<{
      uid: string;
      name: string;
      smiles: string;
      identifier: string;
      identifier_kind: string;
      vendors_url: string;
    }>;
    registry: string;
    note: string;
  };
  not_assessed: string[];
}

interface MoaEvidence {
  compound: string;
  target: string;
  activity_type: string;
  value: string;
  units: string;
  pchembl: number;
}
