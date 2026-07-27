// API response types

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

export interface SearchStatus {
  search_id: string;
  query: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  progress: string | null;
  log: string[];
  export_files: ExportFile[];
  created_at: string;
  elapsed_seconds: number | null;
  error: string | null;
}

export interface LogResponse {
  search_id: string;
  status: string;
  offset: number;
  total_lines: number;
  new_lines: string[];
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
