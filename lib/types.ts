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

export interface SearchState {
  search_id: string;
  query: string;
  query_type: string;
  hops: number;
  status: 'queued' | 'running' | 'completed' | 'failed';
  progress: string | null;
  log: string[];
  export_files: ExportFile[];
  export_dir: string;
  /** base64 file payloads returned by the hosted Python engine (production) */
  base64_files?: Record<string, string>;
  created_at: string;
  elapsed_seconds: number | null;
  error: string | null;
}

export interface SearchSummary {
  search_id: string;
  query: string;
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
