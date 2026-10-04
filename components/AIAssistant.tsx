'use client';

import { useRef, useState, type FormEvent } from 'react';
import { authHeaders } from '@/lib/billing';
import { AlertIcon, ArrowRightIcon, EllipsisIcon, SpinnerIcon } from '@/components/icons';
import { btnPrimary, cx } from '@/components/ui';

interface Props {
  searchId?: string | null;
  query?: string | null;
}

interface Turn {
  role: 'user' | 'assistant';
  text: string;
  error?: boolean;
}

/**
 * Quick prompts tailored to the search context the user is looking at.
 */
const QUICK_PROMPTS = [
  'Summarize the key findings of this search',
  'Which compounds have the strongest binding evidence?',
  'Suggest follow-up queries worth running next',
];

export default function AIAssistant({ searchId, query }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const ask = async (prompt: string) => {
    const trimmed = prompt.trim();
    if (!trimmed || busy) return;

    const nextTurns: Turn[] = [...turns, { role: 'user', text: trimmed }];
    setTurns(nextTurns);
    setInput('');
    setBusy(true);

    try {
      // Pass search context so the model can reason about the current result set
      const contextParts = [
        query ? `Active search query: "${query}"` : null,
        searchId && searchId !== 'error' ? `Search id: ${searchId}` : null,
      ].filter(Boolean);
      const context = contextParts.join(' · ') || undefined;

      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
        body: JSON.stringify({ prompt: trimmed, context }),
      });
      const data = await res.json().catch(() => ({}));

      if (res.ok && data.analysis) {
        setTurns([...nextTurns, { role: 'assistant', text: data.analysis }]);
      } else {
        // The engine answers 401/402 with {message, code}; a bare string is
        // used for unconfigured providers. Never render the object itself.
        const detail = data.detail;
        const text =
          typeof detail === 'string'
            ? detail
            : detail?.message ||
              'AI analysis is temporarily unavailable — please try again shortly.';
        setTurns([...nextTurns, { role: 'assistant', text, error: true }]);
      }
    } catch {
      setTurns([
        ...nextTurns,
        {
          role: 'assistant',
          text: 'Could not reach the analysis service. Check your connection and try again.',
          error: true,
        },
      ]);
    } finally {
      setBusy(false);
      requestAnimationFrame(() => {
        scrollRef.current?.scrollTo({
          top: scrollRef.current.scrollHeight,
          behavior: 'smooth',
        });
      });
    }
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    ask(input);
  };

  return (
    <section className="panel flex min-h-[22rem] flex-col overflow-hidden">
      <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-fg">AI analyst</h2>
          <p className="mt-0.5 truncate text-xs text-fg-subtle">
            {query
              ? `Context: ${query}`
              : 'Ask about chemistry, targets, or your results'}
          </p>
        </div>
        <span className="mt-0.5 flex shrink-0 items-center gap-1.5 text-[11px] text-fg-subtle">
          <span
            aria-hidden
            className="h-1.5 w-1.5 rounded-full bg-success"
          />
          Online
        </span>
      </div>

      {/* Conversation */}
      <div
        ref={scrollRef}
        className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-4"
        aria-live="polite"
      >
        {turns.length === 0 && (
          <div className="flex flex-1 items-center justify-center">
            <p className="max-w-[16rem] text-center text-xs leading-relaxed text-fg-subtle">
              Ask anything about your results — potency comparisons, structural
              hints, or what to explore next.
            </p>
          </div>
        )}

        {turns.map((turn, i) => (
          <div
            key={i}
            className={cx(
              'max-w-[92%] rounded-lg border px-3 py-2 text-[13px] leading-relaxed',
              turn.role === 'user'
                ? 'self-end border-accent-line bg-accent-soft text-fg'
                : turn.error
                ? 'self-start border-danger-line bg-danger-soft text-fg'
                : 'self-start border-line bg-surface-2 text-fg'
            )}
          >
            {turn.error && (
              <span className="mb-1 flex items-center gap-1.5 text-[11px] font-medium text-danger">
                <AlertIcon className="h-3.5 w-3.5" />
                Analyst unavailable
              </span>
            )}
            <p className="whitespace-pre-wrap">{turn.text}</p>
          </div>
        ))}

        {busy && (
          <div className="flex items-center gap-2 self-start rounded-lg border border-line bg-surface-2 px-3 py-2 text-xs text-fg-muted">
            <SpinnerIcon className="h-3.5 w-3.5 animate-spin-slow" />
            Reading your results…
          </div>
        )}
      </div>

      {/* Quick prompts — only shown before the first exchange */}
      {turns.length === 0 && !busy && (
        <div className="flex flex-col gap-1.5 px-4 pb-3">
          <span className="text-[11px] font-semibold uppercase tracking-[0.07em] text-fg-subtle">
            Try asking
          </span>
          {QUICK_PROMPTS.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => ask(q)}
              disabled={busy}
              className="group flex items-center justify-between gap-2 rounded-md border border-line bg-surface-2 px-2.5 py-1.5 text-left text-[11px] text-fg-muted transition-colors hover:border-accent-line hover:text-fg disabled:opacity-55"
            >
              {q}
              <ArrowRightIcon className="h-3.5 w-3.5 shrink-0 text-fg-subtle group-hover:text-accent" />
            </button>
          ))}
        </div>
      )}

      <form onSubmit={handleSubmit} className="border-t border-line p-3">
        <label htmlFor="analyst-input" className="sr-only">
          Ask the AI analyst a question
        </label>
        <div className="flex gap-2">
          <input
            id="analyst-input"
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask the analyst…"
            disabled={busy}
            className="field"
          />
          <button
            type="submit"
            disabled={!input.trim() || busy}
            className={cx(btnPrimary, 'shrink-0')}
          >
            {busy ? (
              <EllipsisIcon className="h-4 w-4" />
            ) : (
              <ArrowRightIcon className="h-4 w-4" />
            )}
            <span className="sr-only">Send question</span>
          </button>
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-fg-subtle">
          Answers are generated from the current search context. Verify critical
          values against the exported files before citing them.
        </p>
      </form>
    </section>
  );
}
