'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import ThemeToggle from '@/components/ThemeToggle';
import { BookIcon } from '@/components/icons';

type PageId = 'app' | 'resources';

interface HeaderProps {
  /** Which section is current, for the nav's aria-current state. */
  current: PageId;
  /** In-page tabs. Only the search workspace has them. */
  nav?: ReactNode;
  /** The account trigger lives here so AccountPanel keeps ownership of it. */
  accountSlot?: ReactNode;
}

/**
 * Wordmark glyph: a three-node graph, which is what the product actually
 * produces. Drawn inline so there is no image request and it stays crisp.
 */
function Mark() {
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-accent-line bg-accent-soft">
      <svg
        viewBox="0 0 24 24"
        className="h-[18px] w-[18px] text-accent"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        aria-hidden
      >
        <circle cx="6" cy="17" r="2.6" />
        <circle cx="17" cy="15.5" r="2.2" />
        <circle cx="12" cy="6" r="2.6" />
        <path d="m8.1 15.4 2.3-6.3M10.4 8.4l4.3 5.3M8.4 16.7l6.4-.8" />
      </svg>
    </span>
  );
}

/** Sticky masthead shared by every page. */
export function SiteHeader({ current, nav, accountSlot }: HeaderProps) {
  const resourcesActive = current === 'resources';

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-canvas/90 backdrop-blur-md">
      <div className="mx-auto w-full max-w-content px-4 sm:px-6">
        <div className="flex h-14 items-center gap-3">
          <Link
            href="/"
            className="flex min-w-0 items-center gap-2.5 rounded-md"
            aria-label="Chemical Data Extractor — home"
          >
            <Mark />
            <span className="min-w-0">
              <span className="block truncate text-[15px] font-semibold leading-tight tracking-tight text-fg">
                Chemical Data Extractor
              </span>
              <span className="hidden text-[11px] leading-tight text-fg-subtle sm:block">
                Scientific knowledge graph
              </span>
            </span>
          </Link>

          <div className="ml-auto flex items-center gap-2">
            <nav
              aria-label="Sections"
              className="hidden items-center gap-1 md:flex"
            >
              {nav}
              <span aria-hidden className="mx-1 h-5 w-px bg-line" />
              <Link
                href="/resources/"
                aria-current={resourcesActive ? 'page' : undefined}
                className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors ${
                  resourcesActive
                    ? 'bg-surface-3 text-fg'
                    : 'text-fg-muted hover:bg-surface-2 hover:text-fg'
                }`}
              >
                <BookIcon className="h-4 w-4" />
                Resources
              </Link>
            </nav>
            <span aria-hidden className="hidden h-5 w-px bg-line md:block" />
            <ThemeToggle />
            {accountSlot}
          </div>
        </div>

        {/* On narrow screens the section tabs get their own row rather than
            competing with the theme switcher for the same line. */}
        <div className="-mx-1 flex items-center gap-1 overflow-x-auto pb-2 md:hidden">
          {nav}
          <span aria-hidden className="mx-1 h-4 w-px shrink-0 bg-line" />
          <Link
            href="/resources/"
            aria-current={resourcesActive ? 'page' : undefined}
            className={`inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors ${
              resourcesActive
                ? 'bg-surface-3 text-fg'
                : 'text-fg-muted hover:bg-surface-2 hover:text-fg'
            }`}
          >
            <BookIcon className="h-4 w-4" />
            Resources
          </Link>
        </div>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="mt-auto border-t border-line bg-sunken">
      <div className="mx-auto w-full max-w-content px-4 py-6 sm:px-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-fg">Chemical Data Extractor</p>
            <p className="mt-0.5 text-xs text-fg-subtle">
              19 database connectors · Multi-hop graph traversal · Enrichment pipeline
            </p>
          </div>
          <div className="flex items-center gap-4 text-xs text-fg-subtle">
            <Link href="/resources/" className="link">
              Documentation &amp; downloads
            </Link>
            <span>
              Built by <span className="font-medium text-fg-muted">Sumanta</span>
            </span>
          </div>
        </div>
      </div>
    </footer>
  );
}
