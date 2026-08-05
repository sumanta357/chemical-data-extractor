import { NextRequest, NextResponse } from 'next/server';
import fs from 'fs';
import { getExportFilePath, getBase64File, guessMime } from '@/lib/search-engine';

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
    // Production: exports live in memory (base64) returned by the hosted
    // Python engine.
    const b64 = getBase64File(searchId, filename);
    if (b64) fileBuffer = Buffer.from(b64, 'base64');
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
