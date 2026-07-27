const API_BASE = '/api';

export async function startSearch(
  query: string,
  queryType: string = 'auto',
  hops: number = 1
): Promise<{ search_id: string }> {
  const res = await fetch(`${API_BASE}/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, query_type: queryType, hops }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Search failed to start');
  }
  return res.json();
}

export async function getSearchStatus(searchId: string) {
  const res = await fetch(`${API_BASE}/search/${searchId}`);
  if (!res.ok) throw new Error('Search not found');
  return res.json();
}

export async function getSearchLog(searchId: string, offset: number = 0) {
  const res = await fetch(`${API_BASE}/search/${searchId}/log?offset=${offset}`);
  if (!res.ok) throw new Error('Log not available');
  return res.json();
}

export async function listSearches(limit: number = 20) {
  const res = await fetch(`${API_BASE}/searches?limit=${limit}`);
  if (!res.ok) return [];
  return res.json();
}

export function getExportUrl(searchId: string, filename: string) {
  return `${API_BASE}/exports/${filename}?search_id=${searchId}`;
}
