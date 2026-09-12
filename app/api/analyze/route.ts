import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/**
 * Built-in provider keys (user-supplied, committed by explicit request so the
 * deployed app works with zero environment configuration).
 *
 * Priority: env var → built-in default.
 *   AGENTROUTER_API_KEY / GEMINI_API_KEY override these when set.
 *
 * NOTE: keep the repository private; anyone who can read this file can use
 * these keys. If the repo ever goes public, rotate them in their consoles.
 */
const DEFAULT_AGENTROUTER_KEY = 'sk-W0U5Yo0MM3wbY3xB0ZpS1f19iyABhATRCBK7N3hyJYjbF6ez';
const DEFAULT_GEMINI_KEY = 'AQ.Ab8RN6KLITc78xyz1ke-xlDGzavLpKWrka-gmVcHlkI8njvDJA';

/**
 * Circuit breaker: when a provider fails with a permanent-looking error
 * (401 "unauthorized client", IP blocks), skip it for a cooldown window
 * so each request doesn't waste a round-trip on a dead provider.
 */
const COOLDOWN_MS = 5 * 60 * 1000; // 5 minutes
const providerCooldowns = new Map<string, number>();

function isCoolingDown(name: string): boolean {
  const until = providerCooldowns.get(name);
  if (!until) return false;
  if (Date.now() > until) {
    providerCooldowns.delete(name);
    return false;
  }
  return true;
}

function tripBreaker(name: string) {
  providerCooldowns.set(name, Date.now() + COOLDOWN_MS);
}

/** Permanent-style errors that won't get better with retries. */
function isPermanentFailure(status: number, body: string): boolean {
  if (status === 401 || status === 403) return true;
  if (/unauthorized client|invalid.{0,20}key|expired/i.test(body)) return true;
  return false;
}

interface ProviderConfig {
  name: string;
  url: string;
  model: string;
  apiKey: string;
}

function getProviders(): ProviderConfig[] {
  const providers: ProviderConfig[] = [];

  // AgentRouter (OpenAI-compatible). Model is overridable via env so you can
  // swap models (e.g. claude-3-5-haiku, gpt-4.1-mini) without a code change.
  // Note: AgentRouter rejects datacenter IPs — from server hosts it always
  // trips the breaker and Gemini takes over. It only works from local runs.
  const arKey = process.env.AGENTROUTER_API_KEY || DEFAULT_AGENTROUTER_KEY;
  if (arKey) {
    providers.push({
      name: 'agentrouter',
      url: 'https://agentrouter.org/v1/chat/completions',
      model: process.env.AGENTROUTER_MODEL || 'gpt-4o-mini',
      apiKey: arKey,
    });
  }

  // Google Gemini via AI Studio (OpenAI-compatible endpoint) — the reliable
  // provider in production; AgentRouter above falls through to this.
  const geminiKey =
    process.env.GEMINI_API_KEY ||
    process.env.GOOGLE_AI_STUDIO_API_KEY ||
    DEFAULT_GEMINI_KEY;
  if (geminiKey) {
    providers.push({
      name: 'gemini',
      url: 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
      model: process.env.GEMINI_MODEL || 'gemini-3.6-flash',
      apiKey: geminiKey,
    });
  }

  return providers;
}

/**
 * AI Analysis endpoint — tries multiple OpenAI-compatible providers in order.
 * Returns structured analysis of search results, bugs, or suggestions.
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { prompt, context } = body;

    if (!prompt || typeof prompt !== 'string') {
      return NextResponse.json(
        { detail: 'prompt is required' },
        { status: 400 }
      );
    }

    const providers = getProviders().filter((p) => {
      if (isCoolingDown(p.name)) {
        console.warn(`AI fallback: skipping ${p.name} (cooldown active)`);
        return false;
      }
      return true;
    });
    if (providers.length === 0) {
      return NextResponse.json(
        {
          detail: 'AI service is not configured. Please try again later.',
          fallback: true,
        },
        { status: 503 }
      );
    }

    const systemMessage = context
      ? `You are a scientific data analysis assistant. Context: ${context}`
      : 'You are a scientific data analysis assistant specializing in chemical knowledge graphs and drug discovery. Be concise and practical.';

    let lastError: string = '';

    for (const provider of providers) {
      try {
        const res = await fetch(provider.url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${provider.apiKey}`,
          },
          body: JSON.stringify({
            model: provider.model,
            messages: [
              { role: 'system', content: systemMessage },
              { role: 'user', content: prompt },
            ],
            // Generous budget — newer Gemini models spend tokens on internal
            // reasoning, so a small max_tokens yields empty completions.
            max_tokens: 2048,
            temperature: 0.7,
          }),
          signal: AbortSignal.timeout(30_000),
        });

        if (!res.ok) {
          const text = await res.text().catch(() => '');
          lastError = `${provider.name}: HTTP ${res.status}`;
          console.warn(`AI fallback: ${lastError} — ${text.slice(0, 150)}`);
          if (isPermanentFailure(res.status, text)) tripBreaker(provider.name);
          continue; // try next provider
        }

        const data = await res.json();
        const content =
          data?.choices?.[0]?.message?.content || 'No analysis generated';

        return NextResponse.json({
          analysis: content,
          provider: provider.name,
          model: data?.model || provider.model,
          usage: data?.usage || null,
        });
      } catch (err: any) {
        lastError = `${provider.name}: ${err?.message || 'unknown'}`;
        console.warn(`AI fallback: ${lastError}`);
        continue;
      }
    }

    // All providers failed
    return NextResponse.json(
      {
        detail: `All AI providers failed. Last error: ${lastError}`,
        fallback: true,
      },
      { status: 502 }
    );
  } catch (err: any) {
    console.error('AI analysis error:', err?.message);
    return NextResponse.json(
      {
        detail: err?.message || 'AI analysis failed',
        fallback: true,
      },
      { status: 500 }
    );
  }
}
