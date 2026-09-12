'use client';

import type { SearchState } from '@/lib/types';

interface Props {
  search: SearchState;
}

const EXT_LABEL: Record<string, string> = {
  '.xlsx': 'Excel workbook',
  '.csv': 'CSV export',
  '.graphml': 'GraphML',
  '.cypher': 'Cypher (Neo4j)',
  '.ttl': 'Turtle (RDF)',
  '.json': 'JSON dump',
  '.parquet': 'Parquet',
  '.png': 'Graph image',
  '.txt': 'Text notes',
  '.md': 'Markdown',
};

export default function ResultsView({ search }: Props) {
  const { export_files } = search;

  const ext = (name: string) => '.' + name.split('.').pop()?.toLowerCase();

  return (
    <div className="card overflow-hidden">
      <div className="px-4 py-3 border-b border-[rgb(var(--border))] flex items-center justify-between">
        <div>
          <h2 className="text-[10px] font-semibold text-[rgb(95 178 201)] uppercase tracking-wider">
            Export Files
          </h2>
          <p className="text-[10px] text-[rgb(var(--muted))] mt-0.5">
            {export_files.length} file{export_files.length === 1 ? '' : 's'} · {search.elapsed_seconds?.toFixed(1) ?? '—'}s runtime
          </p>
        </div>
        <span className="text-[10px] font-mono text-[rgb(var(--muted))]">
          {search.search_id}
        </span>
      </div>

      <div className="divide-y divide-[rgb(30 33 42)]">
        {export_files.map((file) => {
          const isImage = ext(file.name) === '.png';
          return (
            <a
              key={file.name}
              href={`/api/exports/${file.name}?search_id=${search.search_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 px-4 py-2.5 hover:bg-[rgb(20 23 32)] transition-colors group"
            >
              {/* File-type chip */}
              <span
                className="shrink-0 w-5 h-5 rounded flex items-center justify-center text-[9px] font-semibold uppercase"
                style={{
                  background: isImage ? 'rgb(95 178 201)' : 'rgb(42 45 53)',
                  color: isImage ? '#0a0c12' : 'rgb(150 160 180)',
                }}
              >
                {ext(file.name).replace('.', '').slice(0, 3)}
              </span>

              <div className="flex-1 min-w-0">
                <p className="text-sm text-[rgb(200 208 220)] font-medium group-hover:text-[rgb(95 178 201)] truncate">
                  {file.name}
                </p>
                <p className="text-[10px] text-[rgb(90 96 120)] mt-0.5">
                  {EXT_LABEL[ext(file.name)] ?? 'Export file'} · {file.size_display}
                </p>
              </div>

              {/* Arrow hint */}
              <svg
                className="w-3.5 h-3.5 text-[rgb(90 96 120)] group-hover:text-[rgb(95 178 201)] shrink-0"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
            </a>
          );
        })}
      </div>

      {export_files.length === 0 && (
        <div className="px-4 py-6 text-center">
          <p className="text-xs text-[rgb(90 96 120)]">No export files yet.</p>
        </div>
      )}
    </div>
  );
}
