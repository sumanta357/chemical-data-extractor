import { NextRequest, NextResponse } from 'next/server';
import fs from 'fs';
import {
  getExportFilePath,
  getBase64File,
  getRemoteExportUrl,
  getSearchOwnership,
  guessMime,
} from '@/lib/search-engine';
import { verifyExportToken } from '@/lib/export-token';
import { requestOwnsSearch } from '@/lib/search-owner';

export const dynamic = 'force-dynamic';

/**
 * Download one export file.
 *
 * Two ways in, the same two the Python engine accepts:
 *   * a signed, expiring token — this is how Cytoscape imports a network, since
 *     its fetcher sends no cookie at all;
 *   * the search's owner.
 *
 * Anything else gets the same 404 as a file that does not exist, so the
 * response never confirms that somebody else's search is there.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: { filename: string } }
) {
  const filename = params.filename;
  const url = new URL(request.url);
  const searchId = url.searchParams.get('search_id') || undefined;
  const expiresRaw = url.searchParams.get('expires');
  const signature = url.searchParams.get('signature');
  const authorization = request.headers.get('authorization');

  let authorized = false;
  if (expiresRaw || signature) {
    // A signature that is present but wrong must never fall through to the
    // owner path.
    const expiresAt = Number(expiresRaw);
    authorized =
      Boolean(searchId && signature) &&
      Number.isFinite(expiresAt) &&
      verifyExportToken(searchId!, filename, expiresAt, signature!);
  } else if (searchId) {
    const ownership = getSearchOwnership(searchId);
    authorized =
      ownership !== undefined && requestOwnsSearch(request, ownership.ownerKey);
  }

  if (!authorized) {
    return NextResponse.json(
      { detail: `File not found: ${filename}` },
      { status: 404 }
    );
  }

  const filePath = getExportFilePath(filename, searchId);

  let fileBuffer: Buffer | null = null;

  if (filePath) {
    fileBuffer = fs.readFileSync(filePath);
  } else {
    // In-memory base64 (legacy hosted-engine payloads)
    const b64 = getBase64File(searchId, filename);
    if (b64) fileBuffer = Buffer.from(b64, 'base64');
  }

  // Production: proxy the download to the hosted Python engine. The engine owns
  // the file and its account, so it gets the caller's token and applies its own
  // ownership check.
  if (!fileBuffer) {
    const remoteUrl = getRemoteExportUrl(searchId, filename);
    if (remoteUrl) {
      try {
        const remoteRes = await fetch(remoteUrl, {
          headers: authorization ? { authorization } : undefined,
          signal: AbortSignal.timeout(60_000),
        });
        if (remoteRes.ok) {
          const buf = Buffer.from(await remoteRes.arrayBuffer());
          return fileResponse(
            buf,
            filename,
            remoteRes.headers.get('content-type') || guessMime(filename)
          );
        }
      } catch {
        // fall through to 404 below
      }
    }
  }

  if (!fileBuffer) {
    return NextResponse.json(
      { detail: `File not found: ${filename}` },
      { status: 404 }
    );
  }

  const body = new Uint8Array(fileBuffer);
  return fileResponse(fileBuffer, filename, guessMime(filename));
}

/**
 * One download response, with the headers a browser needs to save the file.
 *
 * `Content-Disposition` must be set explicitly: a route cannot rely on the
 * filename alone, and without it Chrome can preview a `.txt`/`.cyjs` payload
 * instead of saving it. RFC 5987's `filename*` carries non-ASCII names, and
 * `nosniff` stops the browser from second-guessing the declared type.
 */
function fileResponse(buffer: Buffer, filename: string, mime: string) {
  const ascii = filename.replace(/[^\x20-\x7e]/g, '_').replace(/"/g, "'");
  const body = new Uint8Array(buffer);
  return new NextResponse(body, {
    headers: {
      'Content-Type': mime,
      'Content-Disposition':
        `attachment; filename="${ascii}"; ` +
        `filename*=UTF-8''${encodeURIComponent(filename)}`,
      'Content-Length': String(buffer.length),
      'Cache-Control': 'private, no-store',
      'X-Content-Type-Options': 'nosniff',
    },
  });
}
