'use client';

/**
 * Documentation and downloads.
 *
 * Every file here is a static asset served straight from the exported UI, so a
 * download link works offline and needs no API. Each entry states what the file
 * contains and when to download it, so the list can be scanned rather than
 * browsed.
 */

import Link from 'next/link';
import { SiteFooter, SiteHeader } from '@/components/SiteChrome';
import { ArrowRightIcon, BookIcon, DownloadIcon } from '@/components/icons';
import { btnPrimary, btnSecondary, cx } from '@/components/ui';

interface Resource {
  file: string;
  title: string;
  kind: string;
  meta: string;
  contains: string;
  when: string;
  primary?: boolean;
}

const RESOURCES: Resource[] = [
  {
    file: '/downloads/Chemical-Data-Extractor-User-Tutorial.pdf',
    title: 'User Tutorial',
    kind: 'PDF',
    meta: '15 pages · complete guide',
    contains:
      'The whole application end to end: what nodes, edges and hops are, creating an account, ' +
      'choosing an expansion depth, reading a running search and its log, understanding every ' +
      'export file, search history, the AI analyst, plans and payment, light and dark mode, a ' +
      'troubleshooting table for every message the app can show, and batch or command-line use.',
    when:
      'Read it once when you start, then keep it as the reference for anything the interface does ' +
      'not explain in place. Covers searching, exports, search history, the AI analyst, plans and ' +
      'payment, light and dark mode, and batch or command-line use.',
    primary: true,
  },
  {
    file: '/downloads/Chemical-Data-Extractor-Quick-Reference.pdf',
    title: 'Quick Reference',
    kind: 'PDF',
    meta: '4 pages · desk card',
    contains:
      'The search steps, what each expansion depth costs, every status value, the export-file map ' +
      'with the tool that opens each one, plan limits, the two operational limits, the command ' +
      'line, and a one-line fix for each common problem.',
    when:
      'Print it or keep it open while you run searches. This is the version to have on a second ' +
      'screen during a long batch.',
  },
  {
    file: '/downloads/Chemical-Data-Extractor-Export-Formats-Reference.pdf',
    title: 'Export Formats Reference',
    kind: 'PDF',
    meta: '5 pages · data dictionary',
    contains:
      'Column-by-column documentation for nodes.csv and edges.csv, what the Excel workbook ' +
      'contains sheet by sheet, how to load the GraphML, Cypher, Turtle, Parquet and JSON exports, ' +
      'and a decision table for choosing the right file.',
    when:
      'Download it before you write the analysis or import script — it tells you which columns to ' +
      'join on and which reader to use, so you do not have to guess at the file layout.',
  },
  ];

const FAQ = [
  {
    question: 'Where do my own search results come from?',
    answer:
      'Every completed search lists its own export files in the workspace. Those are generated for ' +
      'you and are removed from the server 24 hours after the search finishes, so download ' +
      'them before then. The files on this page are static and never expire.',
  },
  {
    question: 'Do I need an account to download these?',
    answer:
      'No. These are public documents. An account is only needed to run a search, because searches ' +
      'are metered against your plan.',
  },
];

export default function ResourcesPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader current="resources" />

      <main className="mx-auto w-full max-w-content flex-1 px-4 py-7 sm:px-6 sm:py-9">
        <div className="max-w-3xl">
          <h1 className="text-2xl font-semibold tracking-tight text-fg sm:text-[28px] sm:leading-[1.2]">
            Documentation and downloads
          </h1>
          <p className="mt-2 text-[15px] leading-relaxed text-fg-muted">
            Guides for using Chemical Data Extractor, and the templates that go with them. Each
            file below states what it contains and when you would want it.
          </p>
        </div>

        <ul className="mt-8 space-y-3">
          {RESOURCES.map((item) => (
            <li key={item.file}>
              <article className="panel overflow-hidden transition-colors hover:border-line-strong">
                <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-start sm:gap-6">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        aria-hidden
                        className="rounded border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase text-fg-muted"
                      >
                        {item.kind}
                      </span>
                      <h2 className="text-sm font-semibold text-fg">{item.title}</h2>
                      <span className="text-xs text-fg-subtle">{item.meta}</span>
                    </div>
                    <p className="mt-1 break-all font-mono text-[11px] text-fg-subtle">
                      {item.file.split('/').pop()}
                    </p>

                    <dl className="mt-3 space-y-2 text-sm">
                      <div className="flex gap-2">
                        <dt className="w-[4.5rem] shrink-0 text-xs font-medium text-fg-muted">
                          Contains
                        </dt>
                        <dd className="leading-relaxed text-fg-muted">{item.contains}</dd>
                      </div>
                      <div className="flex gap-2">
                        <dt className="w-[4.5rem] shrink-0 text-xs font-medium text-fg-muted">
                          Download it
                        </dt>
                        <dd className="leading-relaxed text-fg-muted">{item.when}</dd>
                      </div>
                    </dl>
                  </div>

                  <div className="shrink-0 sm:pt-0.5">
                    <a
                      href={item.file}
                      download
                      className={cx(
                        item.primary ? btnPrimary : btnSecondary,
                        'w-full sm:w-auto'
                      )}
                    >
                      <DownloadIcon className="h-4 w-4" />
                      Download {item.kind}
                    </a>
                  </div>
                </div>
              </article>
            </li>
          ))}
        </ul>

        <section className="mt-10" aria-labelledby="resources-faq">
          <h2 id="resources-faq" className="text-base font-semibold text-fg">
            Common questions
          </h2>
          <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {FAQ.map((entry) => (
              <div key={entry.question} className="panel p-4">
                <dt className="text-sm font-medium text-fg">{entry.question}</dt>
                <dd className="mt-1.5 text-sm leading-relaxed text-fg-muted">
                  {entry.answer}
                </dd>
              </div>
            ))}
          </dl>
        </section>

        <div className="mt-10 flex flex-col gap-3 rounded-lg border border-accent-line bg-accent-soft px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-fg">
            Ready to try it? The first search takes about a minute.
          </p>
          <Link href="/" className={btnPrimary}>
            Go to the search workspace
            <ArrowRightIcon className="h-4 w-4" />
          </Link>
        </div>

        <p className="mt-8 flex items-center gap-2 text-xs text-fg-subtle">
          <BookIcon className="h-4 w-4" />
          Documentation is regenerated from the application source, so it always matches the
          version you are running.
        </p>
      </main>

      <SiteFooter />
    </div>
  );
}
