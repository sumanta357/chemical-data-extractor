/**
 * Serve a signed export link from the origin the reader is actually on.
 *
 * The signature covers (search, file, expiry), not the host, so the path is
 * portable. A route handler only sees the deployment's internal address, which
 * is why a minted link can name a host the browser cannot reach (0.0.0.0, a
 * private 10.x address behind a proxy). Rewriting the origin keeps the token
 * intact and makes the URL usable for a download or a Cytoscape "from URL"
 * import from the page the reader has open.
 */
export function sameOriginLink(link: string): string {
  if (typeof window === 'undefined' || !link) return link;
  try {
    const parsed = new URL(link, window.location.origin);
    return `${window.location.origin}${parsed.pathname}${parsed.search}`;
  } catch {
    return link;
  }
}
