import type { Metadata, Viewport } from 'next';
import './globals.css';

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
};

export const metadata: Metadata = {
  title: 'Chemical Data Extractor — Scientific Knowledge Graph Platform',
  description:
    'Multi-hop automated scientific discovery engine. Extract chemical compounds, bioactivities, protein targets, and pathways from 19+ databases.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body className="min-h-screen bg-[rgb(3,7,18)] text-gray-200 antialiased">
        {children}
      </body>
    </html>
  );
}
