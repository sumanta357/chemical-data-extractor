/**
 * Patent identifiers, and the links that resolve them.
 *
 * Sources record patent numbers with whatever punctuation their own API uses:
 * PubChem returns `US-20190021252-A1`, Google Patents indexes the same document
 * as `US20190021252A1`. Building a link by pasting the raw value into
 * `patents.google.com/patent/` therefore produced URLs that did not open the
 * document — which is exactly what "the patent links don't open" meant.
 *
 * Everything patent-shaped goes through here so the ID shown in the UI, the ID
 * in the exports and the link the browser opens cannot drift apart.
 *
 * Mirrored by `_PATENT_DOCUMENT_RE` / `google_patents_url` in
 * python-api/decision.py; both accept the same series letters on purpose.
 */

/**
 * A patent *document* identifier: two-letter office code, an optional letter
 * series (reissue/plant/design/statutory-invention/defensive), at least four
 * digits and an optional kind code — US5444444, US20190021252A1,
 * US-2019-0021252-A1, EP0248548B1, WO2024111111, USRE45123E1, USD123456S1,
 * USPP12345P2, USH1234H1, UST123456I4.
 *
 * The series letters are an explicit list rather than an open `[A-Z]*`, so a
 * text value that merely starts with an office code and ends in a year
 * ("EGFR2024") is not mistaken for a publication number.
 */
const PATENT_DOCUMENT_RE = /^[A-Z]{2}(?:RE|PP|D|T|H)?\d{4,}(?:[A-Z]\d*)?$/;

/** The connector's "no document found" link rows, which are not patents. */
const PLACEHOLDER_PREFIXES = ['PATENTSEARCH', 'SEARCH'];

export const GOOGLE_PATENTS_BASE = 'https://patents.google.com/patent/';

/** True only for a value that identifies a patent document. */
export function isPatentDocument(uid: string, number: string): boolean {
  const upperUid = (uid || '').toUpperCase();
  if (
    upperUid.startsWith('PATENTSEARCH:') ||
    upperUid.startsWith('SEARCH:')
  ) {
    return false;
  }
  const upperNumber = (number || '').toUpperCase();
  if (
    upperNumber.startsWith('SEARCH:') ||
    upperNumber.startsWith('PATENTSEARCH:')
  ) {
    return false;
  }
  return PATENT_DOCUMENT_RE.test(upperNumber.replace(/[^A-Z0-9]/g, ''));
}

/**
 * The canonical publication id for a recorded number, or null.
 *
 * `US-2019-0021252-A1` → `US20190021252A1`. Hyphens and spaces are removed
 * because that is the form Google Patents resolves as a document; the
 * punctuated form is what its search box takes, not its document path.
 */
export function canonicalPatentNumber(raw: string): string | null {
  let compact = (raw || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (!compact) return null;
  for (const prefix of PLACEHOLDER_PREFIXES) {
    if (compact.startsWith(prefix)) return null;
  }
  if (compact.startsWith('PATENT')) compact = compact.slice('PATENT'.length);
  return PATENT_DOCUMENT_RE.test(compact) ? compact : null;
}

/** A Google Patents search for a value we could not parse as a document. */
export function patentSearchUrl(query: string): string {
  return `https://patents.google.com/?q=${encodeURIComponent(
    (query || '').trim()
  )}`;
}

export interface PatentLink {
  /** The link to open. Always absolute, always https. */
  href: string;
  /** The canonical document id, when the number could be parsed. */
  number: string | null;
  /** True when `href` is a document page rather than a search page. */
  document: boolean;
}

/**
 * The link for one patent row.
 *
 * A recorded `google_patents_url` is used only when it is already a document
 * URL; a search URL, a placeholder or an empty cell is replaced by the
 * canonical document link. A number we cannot parse still gets a search link,
 * so a row never renders an anchor that goes nowhere.
 */
export function patentLink(
  rawNumber: string,
  recordedUrl?: string | null
): PatentLink {
  const number = canonicalPatentNumber(rawNumber);
  const recorded = (recordedUrl || '').trim();

  // Rebuild a recorded document URL rather than trusting it: the recorded
  // value can still be punctuated, and that is not a document URL.
  if (recorded.startsWith(GOOGLE_PATENTS_BASE)) {
    const fromRecorded = canonicalPatentNumber(
      recorded.slice(GOOGLE_PATENTS_BASE.length).split('?')[0]
    );
    if (fromRecorded) {
      return {
        href: `${GOOGLE_PATENTS_BASE}${fromRecorded}`,
        number: number ?? fromRecorded,
        document: true,
      };
    }
  }
  if (number) {
    return { href: `${GOOGLE_PATENTS_BASE}${number}`, number, document: true };
  }
  return {
    href: patentSearchUrl(rawNumber),
    number: null,
    document: false,
  };
}
