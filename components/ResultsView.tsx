'use client';

import type { SearchState } from '@/lib/types';

interface Props {
  search: SearchState;
}

const FILE_ICONS: Record<string, string> = {
  '.xlsx': '📊',
  '.csv': '📋',
  '.graphml': '📐',
  '.cypher': '💾',
  '.ttl': '🏷️',
  '.json': '📦',
  '.parquet': '🗄️',
  '.png': '🖼️',
  '.txt': '📄',
  '.md': '📝',
};

export default function ResultsView({ search }: Props) {
  const { export_files } = search;

  const getIcon = (name: string) => {
    const ext = '.' + name.split('.').pop()?.toLowerCase();
    return FILE_ICONS[ext] || '📄';
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div className="p-4 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
          📂 Export Files
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
            <span className="text-lg">{getIcon(file.name)}</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm text-gray-200 truncate group-hover:text-cyan-400 transition-colors">
                {file.name}
              </p>
              <p className="text-xs text-gray-600">{file.size_display}</p>
            </div>
            <svg
              className="w-4 h-4 text-gray-600 group-hover:text-cyan-400 transition-colors shrink-0"
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
        ))}
      </div>
    </div>
  );
}
