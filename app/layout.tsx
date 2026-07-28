import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'SciGraph — Scientific Knowledge Graph Platform',
  description:
    'Multi-hop automated scientific discovery engine. Search proteins, compounds, and pathways across 19+ databases.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-[rgb(3,7,18)] text-gray-200 antialiased">
        {children}
      </body>
    </html>
  );
}
