import type { Metadata, Viewport } from 'next';
import './globals.css';

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  colorScheme: 'light dark',
};

/**
 * Applied before first paint so a dark-mode visitor never sees a white flash
 * and a light-mode visitor never sees a black one. It reads the stored
 * preference, falls back to the operating system, and writes the resolved
 * theme onto <html> as both a class and a data attribute.
 */
const THEME_BOOTSTRAP = `(function(){try{
var k='cde-theme';var s=localStorage.getItem(k);
var m=window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';
var t=(s==='light'||s==='dark')?s:m;
document.documentElement.classList.remove('light','dark');
document.documentElement.classList.add(t);
document.documentElement.setAttribute('data-theme',t);
document.documentElement.style.colorScheme=t;
}catch(e){document.documentElement.classList.add('dark')}})();`;

export const metadata: Metadata = {
  title: 'Chemical Data Extractor — Scientific Knowledge Graph Platform',
  description:
    'Multi-hop automated scientific discovery engine. Extract chemical compounds, bioactivities, protein targets, and pathways from 18 verified data sources.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // `dark` is the server-rendered default; the script below corrects it
    // before paint if the visitor prefers light.
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body className="min-h-screen bg-canvas font-sans text-fg antialiased">
        {children}
      </body>
    </html>
  );
}
