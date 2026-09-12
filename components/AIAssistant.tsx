'use client';

import { useRef, useState, type FormEvent } from 'react';

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
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: trimmed, context }),
      });
      const data = await res.json().catch(() => ({}));

      if (res.ok && data.analysis) {
        setTurns([
          ...nextTurns,
          { role: 'assistant', text: data.analysis },
        ]);
      } else {
        setTurns([
          ...nextTurns,
          {
            role: 'assistant',
            text:
              data.detail ||
              'AI analysis is unavailable — add GEMINI_API_KEY or AGENTROUTER_API_KEY in Settings → Environment.',
            error: true,
          },
        ]);
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
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
      });
    }
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    ask(input);
  };

  return (
    <section className="ai-panel">
      {/* Header */}
      <div className="ai-panel__head">
        <div className="flex items-center gap-2 min-w-0">
          {/* Small hex badge — AI mark, matches uiverse.io geometric motif */}
          <span className="ai-panel__badge" aria-hidden="true">
            <svg viewBox="0 0 24 24" className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2.2}>
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 2.5l7.5 4.3v10.4L12 21.5l-7.5-4.3V6.8L12 2.5z"
              />
              <path strokeLinecap="round" d="M12 8v8M8.5 10.5l7 3M15.5 10.5l-7 3" />
            </svg>
          </span>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-white">
              AI Analyst
            </h2>
            <p className="text-xs text-[rgb(120 130 160)] -mt-0.5 truncate">
              {query ? `Context: ${query}` : 'Ask about chemistry, targets, or your results'}
            </p>
          </div>
        </div>
        <span className="ai-panel__status" title="Provider falls back automatically">
          online
        </span>
      </div>

      {/* Conversation */}
      <div className="ai-panel__thread" ref={scrollRef}>
        {turns.length === 0 && (
          <div className="ai-panel__empty">
            <p className="text-[11px] text-[rgb(110 120 150)] leading-relaxed">
              Ask anything about your search results — potency comparisons,
              structural hints, or what to explore next.
            </p>
          </div>
        )}

        {turns.map((turn, i) => (
          <div
            key={i}
            className={
              turn.role === 'user' ? 'ai-msg ai-msg--user' : 'ai-msg ai-msg--bot'
            }
          >
            <p className="whitespace-pre-wrap leading-relaxed">{turn.text}</p>
          </div>
        ))}

        {busy && (
          <div className="ai-msg ai-msg--bot">
            <span className="ai-typing">
              <i /><i /><i />
            </span>
          </div>
        )}
      </div>

      {/* Quick prompts — only shown before the first exchange */}
      {turns.length === 0 && !busy && (
        <div className="ai-panel__quick">
          {QUICK_PROMPTS.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => ask(q)}
              disabled={busy}
              className="ai-chip"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {/* Input */}
      <form onSubmit={handleSubmit} className="ai-panel__form">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask the analyst…"
          disabled={busy}
          className="ai-panel__input"
        />
        <button
          type="submit"
          disabled={!input.trim() || busy}
          className="ai-panel__send"
          aria-label="Send"
        >
          <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2}>
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M5 12h14M13 6l6 6-6 6"
            />
          </svg>
        </button>
      </form>
    </section>
  );
}
