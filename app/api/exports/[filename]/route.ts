import { NextRequest, NextResponse } from 'next/server';
import fs from 'fs';
import {
  getExportFilePath,
  getBase64File,
  getRemoteExportUrl,
  guessMime,
} from '@/lib/search-engine';

export const dynamic = 'force-dynamic';

export async function GET(
  _request: NextRequest,
  { params }: { params: { filename: string } }
) {
  const filename = params.filename;
  const url = new URL(_request.url);
  const searchId = url.searchParams.get('search_id') || undefined;

  const filePath = getExportFilePath(filename, searchId);

  let fileBuffer: Buffer | null = null;

  if (filePath) {
    fileBuffer = fs.readFileSync(filePath);
  } else {
    // In-memory base64 (legacy hosted-engine payloads)
    const b64 = getBase64File(searchId, filename);
    if (b64) fileBuffer = Buffer.from(b64, 'base64');
  }

  // Production: proxy the download to the hosted Python engine.
  if (!fileBuffer) {
    const remoteUrl = getRemoteExportUrl(searchId, filename);
    if (remoteUrl) {
      try {
        const remoteRes = await fetch(remoteUrl, {
          signal: AbortSignal.timeout(60_000),
        });
        if (remoteRes.ok) {
          const buf = Buffer.from(await remoteRes.arrayBuffer());
          return new NextResponse(new Uint8Array(buf), {
            headers: {
              'Content-Type': remoteRes.headers.get('content-type') || guessMime(filename),
              'Content-Disposition': `attachment; filename="${filename}"`,
              'Content-Length': String(buf.length),
            },
          });
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
  const mime = guessMime(filename);

  return new NextResponse(body, {
    headers: {
      'Content-Type': mime,
      'Content-Disposition': `attachment; filename="${filename}"`,
      'Content-Length': String(fileBuffer.length),
    },
  });
}
