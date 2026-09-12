'use client';

import type { SearchState } from '@/lib/types';

interface Props {
  search: SearchState;
}

const FILE_LABELS: Record<string, string> = {
  '.xlsx': 'Excel workbook',
  '.csv': 'CSV table',
  '.graphml': 'GraphML graph',
  '.cypher': 'Neo4j Cypher script',
  '.ttl': 'RDF Turtle graph',
  '.json': 'JSON data',
  '.parquet': 'Parquet table',
  '.png': 'Image',
  '.txt': 'Text',
  '.md': 'Markdown',
};

export default function ResultsView({ search }: Props) {
  const { export_files } = search;

  const getLabel = (name: string) => {
    const ext = '.' + name.split('.').pop()?.toLowerCase();
    return FILE_LABELS[ext] || 'File';
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div className="p-4 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
          Export Files
        </h2>
        <p className="text-xs text-gray-500 mt-0.5">
          {export_files.length} files · {search.elapsed_seconds?.toFixed(1)}s runtime
        </p>
      </div>
      <div className="divide-y divide-gray-800">
        {export_files.map((file) => (
          <a
            key={file.name}
            href={`/api/exports/${file.name}?search_id=${search.search_id}`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-800/50 transition-colors group"
          >
            <div className="flex-1 min-w-0">
              <p className="text-sm text-gray-200 truncate group-hover:text-cyan-400 transition-colors">
                {file.name}
              </p>
              <p className="text-xs text-gray-600">{getLabel(file.name)} · {file.size_display}</p>
            </div>
            <span className="text-xs text-gray-500 group-hover:text-cyan-400 transition-colors shrink-0">
              Download
            </span>
          </a>
        ))}
      </div>
    </div>
  );
}
