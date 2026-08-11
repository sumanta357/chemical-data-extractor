import { spawn } from 'child_process';
import path from 'path';
import fs from 'fs';
import type { SearchState, ExportFile } from './types';

// ── In-memory search state ──────────────────────────────────────────────
const searches = new Map<string, SearchState>();

// ── Constants ───────────────────────────────────────────────────────────
const BASE_DIR = process.cwd();
const EXPORTS_DIR = path.join(BASE_DIR, 'exports');

// Production (Vercel) has a read-only filesystem — never crash module load
// if we can't create the exports dir here.
try {
  if (!fs.existsSync(EXPORTS_DIR)) {
    fs.mkdirSync(EXPORTS_DIR, { recursive: true });
  }
} catch {
  // read-only FS (production hosting) — exports are served via the hosted
  // engine or kept in memory.
}

// ── Helpers ─────────────────────────────────────────────────────────────

function formatSize(size: number): string {
  for (const unit of ['B', 'KB', 'MB']) {
    if (size < 1024) return `${size.toFixed(0)} ${unit}`;
    size /= 1024;
  }
  return `${size.toFixed(1)} GB`;
}

function listExportFiles(exportDir: string): ExportFile[] {
  const files: ExportFile[] = [];
  const dirPath = path.resolve(exportDir);
  if (!fs.existsSync(dirPath)) return files;

  try {
    const entries = fs.readdirSync(dirPath, { withFileTypes: true });
    for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
      if (entry.isFile() && !entry.name.startsWith('.')) {
        const stat = fs.statSync(path.join(dirPath, entry.name));
        files.push({
          name: entry.name,
          size_bytes: stat.size,
          size_display: formatSize(stat.size),
          url: `/api/exports/${entry.name}?search_id=${exportDir.split('/').pop()?.split('_')[0] || ''}`,
        });
      }
    }
  } catch {
    // directory might not be readable
  }
  return files;
}

function guessMime(filename: string): string {
  const ext = path.extname(filename).toLowerCase();
  const mimes: Record<string, string> = {
    '.csv': 'text/csv',
    '.json': 'application/json',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.graphml': 'application/xml',
    '.ttl': 'text/turtle',
    '.cypher': 'text/plain',
    '.parquet': 'application/octet-stream',
    '.png': 'image/png',
    '.txt': 'text/plain',
    '.md': 'text/markdown',
  };
  return mimes[ext] || 'application/octet-stream';
}

// ── Hosted engine helpers (production) ───────────────────────────────────

function hostedEngineUrl(): string | null {
  const url = process.env.SEARCH_ENGINE_URL;
  return url ? url.replace(/\/+$/, '') : null;
}

async function fetchJson(url: string, init?: RequestInit): Promise<any> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Engine HTTP ${res.status}: ${text.slice(0, 300)}`);
  }
  return res.json();
}

/**
 * Refresh a search's state from the hosted Python engine. Mutates the
 * SearchState in place so the Next.js routes can serve the UI unchanged.
 */
async function refreshRemoteState(state: SearchState): Promise<void> {
  if (!state.remote_engine_url || !state.remote_search_id) return;

  try {
    const data = await fetchJson(
      `${state.remote_engine_url}/api/search/${state.remote_search_id}`,
      { signal: AbortSignal.timeout(20_000) }
    );

    state.status = data.status || state.status;
    state.progress = data.progress ?? state.progress;
    if (Array.isArray(data.log)) state.log = data.log;
    if (typeof data.elapsed_seconds === 'number') {
      state.elapsed_seconds = data.elapsed_seconds;
    }
    if (data.error) state.error = data.error;

    if (Array.isArray(data.export_files)) {
      state.export_files = data.export_files.map((f: any) => ({
        name: f.name,
        size_bytes: f.size_bytes ?? 0,
        size_display: f.size_display ?? formatSize(f.size_bytes ?? 0),
        // Always route downloads through this app so the UI keeps working
        url: `/api/exports/${encodeURIComponent(f.name)}?search_id=${state.search_id}`,
      }));
    }
  } catch {
    // Engine unreachable — keep last known state; the poller will retry.
  }
}

async function startHostedSearch(
  searchId: string,
  query: string,
  queryType: string,
  hops: number,
  engineUrl: string,
  startTime: number
): Promise<void> {
  const state = searches.get(searchId)!;
  try {
    state.log.push(`[${new Date().toISOString()}] Contacting hosted engine...`);
    const data = await fetchJson(`${engineUrl}/api/search`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query, query_type: queryType, hops }),
      signal: AbortSignal.timeout(30_000),
    });

    state.remote_engine_url = engineUrl;
    state.remote_search_id = data.search_id;
    state.status = data.status || 'running';
    state.progress = data.progress || '🌐 Hosted engine running...';
    state.log.push(`  Hosted search started: ${data.search_id}`);
    if (Array.isArray(data.log)) {
      for (const line of data.log) {
        if (line && line.trim()) {
          state.log.push(line);
          updateProgress(line, state);
        }
      }
    }

    // First refresh gets the current snapshot immediately.
    await refreshRemoteState(state);
    state.elapsed_seconds = (Date.now() - startTime) / 1000;
  } catch (err: any) {
    state.status = 'failed';
    state.error = err.message || 'Hosted engine unreachable';
    state.log.push(`❌ Hosted engine error: ${err.message}`);
    state.elapsed_seconds = (Date.now() - startTime) / 1000;
  }
}

// ── Search Runner ───────────────────────────────────────────────────────

function runSearch(
  searchId: string,
  query: string,
  queryType: string,
  hops: number,
  exportDir: string
): void {
  const state = searches.get(searchId)!;
  const startTime = Date.now();

  // Production: use the hosted Python engine (Render) — python3 and a
  // writable FS don't exist in the Vercel Node runtime.
  const engineUrl = hostedEngineUrl();
  if (engineUrl) {
    state.status = 'running';
    state.log.push(`[${new Date().toISOString()}] Starting search via hosted engine...`);
    state.log.push(`  Query: ${query} (${queryType}, ${hops}-hop)`);
    void startHostedSearch(searchId, query, queryType, hops, engineUrl, startTime);
    return;
  }

  // Sandbox/preview: run python3 locally.
  try {
    fs.mkdirSync(path.resolve(exportDir), { recursive: true });
  } catch (err: any) {
    state.status = 'failed';
    state.error = `Cannot create export directory: ${err.message}`;
    state.elapsed_seconds = 0;
    return;
  }

  const pythonCmd = 'python3';
  const pythonArgs = [
    path.join(BASE_DIR, 'api', 'scigraph.py'),
    query,
    '--query-type',
    queryType,
    '--hops',
    String(hops),
    '--export-dir',
    exportDir,
  ];

  state.status = 'running';
  state.log.push(`[${new Date().toISOString()}] Starting scigraph search...`);
  state.log.push(`  Query: ${query} (${queryType}, ${hops}-hop)`);
  state.log.push(`  Export: ${exportDir}`);

  const proc = spawn(pythonCmd, pythonArgs, {
    cwd: BASE_DIR,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  const handleOutput = (data: Buffer) => {
    const lines = data.toString('utf-8').split('\n');
    for (const raw of lines) {
      const line = raw.replace(/\r$/, '');
      if (line.trim()) {
        state.log.push(line);
        updateProgress(line, state);
      }
    }
  };

  proc.stdout?.on('data', handleOutput);
  proc.stderr?.on('data', handleOutput);

  proc.on('close', async (code) => {
    state.log.push(`\n═══════════════════════════════════════════════════`);
    state.log.push(`  scigraph finished with exit code ${code}`);

    if (code === 0) {
      state.progress = '🧪 Enriching compounds with PubChem & CrossRef...';
      state.log.push('');
      state.log.push('═'.repeat(60));
      state.log.push('  Starting Enrichment Pipeline (PubChem SMILES / CrossRef metadata)');
      state.log.push('═'.repeat(60));

      try {
        await runEnrichment(exportDir, state);
        state.log.push(`  ✦ Enrichment pipeline completed`);
      } catch (err: any) {
        state.log.push(`  ⚠️  Enrichment step error: ${err.message}`);
      }

      state.status = 'completed';
      state.elapsed_seconds = (Date.now() - startTime) / 1000;
      state.export_files = listExportFiles(exportDir);

      const enrichedExists = state.export_files.some(
        (f) => f.name === 'enriched_data.xlsx'
      );
      state.progress = `✅ Completed in ${state.elapsed_seconds.toFixed(1)}s${
        enrichedExists ? ' + enriched multi-sheet Excel' : ''
      }`;
    } else {
      state.status = 'failed';
      state.error = `Process exited with code ${code}`;
      state.elapsed_seconds = (Date.now() - startTime) / 1000;
    }
  });

  proc.on('error', (err: any) => {
    if (err && err.code === 'ENOENT') {
      state.status = 'failed';
      state.error =
        'python3 is not available in this runtime and SEARCH_ENGINE_URL is not configured. ' +
        'Set SEARCH_ENGINE_URL (the hosted Python engine) to run searches in production.';
      state.log.push(`❌ python3 not found — set SEARCH_ENGINE_URL to use the hosted engine`);
      state.elapsed_seconds = (Date.now() - startTime) / 1000;
      return;
    }
    state.status = 'failed';
    state.error = err.message;
    state.log.push(`❌ Spawn error: ${err.message}`);
    state.elapsed_seconds = (Date.now() - startTime) / 1000;
  });
}

function updateProgress(line: string, state: SearchState): void {
  if (
    line.includes('[1/6]') ||
    line.includes('[2/6]') ||
    line.includes('[3/6]') ||
    line.includes('[4/6]') ||
    line.includes('[5/6]') ||
    line.includes('[6/6]')
  ) {
    state.progress = line.trim();
  } else if (line.includes('╔══ Hop')) {
    state.progress = line.trim();
  } else if (line.includes('Pipeline finished') || line.includes('finished successfully')) {
    state.progress = '✅ scigraph complete!';
  } else if (line.includes('Error') || line.toLowerCase().includes('error')) {
    state.progress = `⚠️ ${line.trim().slice(0, 100)}`;
  }
}

function runEnrichment(
  exportDir: string,
  state: SearchState
): Promise<void> {
  return new Promise((resolve, reject) => {
    const proc = spawn(
      'python3',
      [
        path.join(BASE_DIR, 'api', 'enrich_exports.py'),
        '--export-dir',
        exportDir,
      ],
      {
        cwd: BASE_DIR,
        env: { ...process.env, PYTHONUNBUFFERED: '1' },
        stdio: ['ignore', 'pipe', 'pipe'],
      }
    );

    proc.stdout?.on('data', (data: Buffer) => {
      const lines = data.toString('utf-8').split('\n');
      for (const raw of lines) {
        const line = raw.replace(/\r$/, '');
        if (line.trim()) {
          state.log.push(line);
          if (line.includes('Enriching compounds')) {
            state.progress = '🧪 ' + line.trim().slice(0, 80);
          } else if (line.includes('Downloading 2D')) {
            state.progress = '🖼️ ' + line.trim().slice(0, 80);
          } else if (line.includes('Enriching publications')) {
            state.progress = '📄 ' + line.trim().slice(0, 80);
          } else if (line.includes('Writing Excel')) {
            state.progress = '📊 ' + line.trim().slice(0, 80);
          } else if (line.includes('Enriched Excel saved')) {
            state.progress = '✅ Enrichment complete!';
          }
        }
      }
    });

    proc.stderr?.on('data', (data: Buffer) => {
      const lines = data.toString('utf-8').split('\n');
      for (const raw of lines) {
        const line = raw.replace(/\r$/, '');
        if (line.trim()) state.log.push(line);
      }
    });

    proc.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Enrichment exit code ${code}`));
    });

    proc.on('error', reject);
  });
}

// ── Public API ──────────────────────────────────────────────────────────

export function createSearch(
  query: string,
  queryType: string,
  hops: number
): SearchState {
  const searchId = generateId(8);
  const cleanQ = query
    .replace(/[^a-zA-Z0-9_\-]/g, '_')
    .slice(0, 30);
  const exportDir = path.join(EXPORTS_DIR, `${searchId}_${cleanQ}`);

  const state: SearchState = {
    search_id: searchId,
    query,
    query_type: queryType,
    hops,
    status: 'queued',
    progress: '⏳ Queued...',
    log: [],
    export_files: [],
    export_dir: exportDir,
    created_at: new Date().toISOString(),
    elapsed_seconds: null,
    error: null,
  };

  searches.set(searchId, state);

  // Kick off the search asynchronously (not awaited)
  runSearch(searchId, query, queryType, hops, exportDir);

  return state;
}

export async function getSearch(searchId: string): Promise<SearchState | undefined> {
  const state = searches.get(searchId);
  if (!state) return undefined;

  // Hosted engine: refresh from the Python host on every poll.
  if (state.remote_engine_url && state.remote_search_id) {
    await refreshRemoteState(state);
  }

  if (state && state.status === 'completed' && state.export_files.length === 0) {
    state.export_files = listExportFiles(state.export_dir);
  }
  return state;
}

export async function getSearchLog(
  searchId: string
): Promise<{ state: SearchState | undefined }> {
  const state = await getSearch(searchId);
  return { state };
}

export function listSearches(limit: number = 20): SearchState[] {
  return Array.from(searches.values())
    .sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    )
    .slice(0, limit);
}

export function getExportFilePath(
  filename: string,
  searchId?: string
): string | null {
  if (searchId) {
    const state = searches.get(searchId);
    if (state) {
      const p = path.resolve(state.export_dir, filename);
      if (fs.existsSync(p)) return p;
    }
  }

  // Fallback: search all export dirs
  try {
    for (const entry of fs.readdirSync(EXPORTS_DIR, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        const candidate = path.join(EXPORTS_DIR, entry.name, filename);
        if (fs.existsSync(candidate)) return candidate;
      }
    }
  } catch {
    // exports dir unavailable (read-only FS)
  }

  const direct = path.resolve(EXPORTS_DIR, filename);
  if (fs.existsSync(direct)) return direct;

  return null;
}

export function getBase64File(
  searchId: string | undefined,
  filename: string
): string | null {
  if (!searchId) return null;
  const state = searches.get(searchId);
  if (state?.base64_files) {
    const b64 = state.base64_files[filename];
    if (b64) return b64;
  }
  return null;
}

/**
 * If the search is running on the hosted engine, return the full remote URL
 * for a given export file so the Next.js route can proxy the download.
 */
export function getRemoteExportUrl(
  searchId: string | undefined,
  filename: string
): string | null {
  if (!searchId) return null;
  const state = searches.get(searchId);
  if (state?.remote_engine_url && state.remote_search_id) {
    return `${state.remote_engine_url}/api/exports/${encodeURIComponent(
      filename
    )}?search_id=${state.remote_search_id}`;
  }
  return null;
}

export { guessMime, EXPORTS_DIR };

// ── Utility ─────────────────────────────────────────────────────────────

function generateId(length: number): string {
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let result = '';
  for (let i = 0; i < length; i++) {
    result += chars[Math.floor(Math.random() * chars.length)];
  }
  return result;
}
