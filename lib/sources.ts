/**
 * Data-source status.
 *
 * The engine registers a connector for every entry in its catalogue, but most
 * of those are placeholders that return nothing. The interface therefore never
 * states a source count of its own: it reads the implemented connectors and
 * their last verified status from the engine, so the number on screen tracks
 * the backend instead of a hard-coded claim.
 */

export type SourceStatus = 'working' | 'partial' | 'unavailable' | 'unverified';

export interface PlainQueryResult {
  entities: number;
  relations: number;
  error?: string | null;
}

export interface DataSource {
  name: string;
  status: SourceStatus;
  covers: string;
  group: string;
  note: string;
  /** What each plain-name probe returned, e.g. { Aspirin: {...}, tubulin: {...} }. */
  plain_query?: Record<string, PlainQueryResult>;
  /**
   * False when the source is connected and healthy but returned nothing for the
   * plain names customers actually type -- it answers only its own identifier
   * format. Null when it was never probed with one.
   */
  answers_common_queries?: boolean | null;
}

export interface SourceGroup {
  name: string;
  description: string;
}

export interface DataSourcesResponse {
  available?: boolean;
  implemented_total: number;
  counts: Record<string, number>;
  groups: SourceGroup[];
  sources: DataSource[];
  generated_at?: string;
}

/** Sources that returned records in the last live audit. */
export function workingCount(data: DataSourcesResponse | null): number {
  if (!data) return 0;
  return data.counts?.working ?? data.sources.filter((s) => s.status === 'working').length;
}

/** Anything connected but not fully usable, shown separately so the count is never inflated. */
export function limitedSources(data: DataSourcesResponse | null): DataSource[] {
  if (!data) return [];
  return data.sources.filter((s) => s.status === 'partial' || s.status === 'unavailable');
}

/**
 * Sources that work, but only for the identifier format they are authoritative
 * for. Calling these out stops a customer concluding that a working source is
 * broken because their compound name returned nothing from it.
 */
export function narrowSources(data: DataSourcesResponse | null): DataSource[] {
  if (!data) return [];
  return data.sources.filter((s) => s.answers_common_queries === false);
}

export async function getSources(): Promise<DataSourcesResponse | null> {
  try {
    const res = await fetch('/api/sources');
    if (!res.ok) return null;
    const data = await res.json();
    return data?.available === false ? null : data;
  } catch {
    // The panel degrades to a neutral message rather than claiming a count it
    // could not confirm.
    return null;
  }
}