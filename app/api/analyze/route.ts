import { randomUUID } from 'crypto';
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
const DEFAULT_OPENCODE_KEY = 'sk-y2338SzSUvBqPKHmjQ75DbWY0D78CfTbzl6jcBqMnthziB0XaEmGI9QJ6a6IpOum';

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
  /** Auth header styles to try in order. Google accepts both Bearer and
   *  x-goog-api-key; some token types only work with one of them. */
  authStyles: ('bearer' | 'x-goog-api-key')[];
  /** Extra headers, re-evaluated per request (e.g. a fresh OpenCode session id). */
  extraHeaders?: () => Record<string, string>;
  /** Appended to the system message for this provider only. */
  systemSuffix?: string;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function authHeaders(style: 'bearer' | 'x-goog-api-key', apiKey: string): Record<string, string> {
  return style === 'bearer'
    ? { Authorization: `Bearer ${apiKey}` }
    : { 'x-goog-api-key': apiKey };
}

function getProviders(): ProviderConfig[] {
  const providers: ProviderConfig[] = [];

  // OpenCode Zen (OpenAI-compatible) — primary. Free-tier models require an
  // x-opencode-session header; a per-request id satisfies it. Several free
  // models sit behind DIFFERENT upstream pools, so if one is overloaded
  // (Nvidia 502s) the next one often still works.
  const ocKey = process.env.OPENCODE_API_KEY || DEFAULT_OPENCODE_KEY;
  if (ocKey) {
    const ocModels = [
      process.env.OPENCODE_MODEL || 'nemotron-3-ultra-free',
      'nemotron-3.5-lightning-free',
      'deepseek-v4-flash-free',
    ];
    for (const model of ocModels) {
      providers.push({
        name: `opencode:${model}`,
        url: 'https://opencode.ai/zen/v1/chat/completions',
        model,
        apiKey: ocKey,
        authStyles: ['bearer'],
        extraHeaders: () => ({ 'x-opencode-session': `cde-${randomUUID()}` }),
        // Nemotron-family models leak chain-of-thought into the content;
        // ask for a direct answer instead.
        systemSuffix:
          ' Answer directly and concisely — do not show your reasoning steps.',
      });
    }
  }

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
      authStyles: ['bearer'],
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
      authStyles: ['bearer', 'x-goog-api-key'],
    });
  }

  return providers;
}

/**
 * Some providers (OpenCode Zen) return errors wrapped in HTTP 200 bodies:
 * { "type": "error", "error": { "type": "server_error", "message": ... } }
 */
function extractProviderError(data: any): string | null {
  if (!data || typeof data !== 'object') return null;
  const err = data.error ?? (data.type === 'error' ? data : null);
  if (!err) return null;
  const msg =
    typeof err === 'string' ? err : err.message || err.type || 'unknown provider error';
  return String(msg);
}

/** Transient failures that are worth retrying with backoff. */
function isTransientError(text: string): boolean {
  return /(\b502\b|\b503\b|\b529\b|\b429\b|overloaded|temporarily|timeout|rate.?limit)/i.test(
    text
  );
}

/**
 * AI Analysis endpoint — tries multiple OpenAI-compatible providers in order.
 * Each provider gets up to 3 attempts with backoff for transient errors
 * (the free OpenCode models sit behind an Nvidia upstream that occasionally
 * returns 502 "overloaded"). Returns structured analysis of search results.
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

    const baseSystemMessage = context
      ? `You are a scientific data analysis assistant. Context: ${context}`
      : 'You are a scientific data analysis assistant specializing in chemical knowledge graphs and drug discovery. Be concise and practical.';

    let lastError: string = '';

    for (const provider of providers) {
      const systemMessage = baseSystemMessage + (provider.systemSuffix || '');
      let providerSucceeded = false;

      for (const style of provider.authStyles) {
        // Up to 3 attempts with backoff for transient upstream errors.
        for (let attempt = 1; attempt <= 3; attempt++) {
          try {
            const res = await fetch(provider.url, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                ...authHeaders(style, provider.apiKey),
                ...(provider.extraHeaders ? provider.extraHeaders() : {}),
              },
              body: JSON.stringify({
                model: provider.model,
                messages: [
                  { role: 'system', content: systemMessage },
                  { role: 'user', content: prompt },
                ],
                // Generous budget — newer models spend tokens on internal
                // reasoning, so a small max_tokens yields empty completions.
                max_tokens: 2048,
                temperature: 0.7,
              }),
              signal: AbortSignal.timeout(45_000),
            });

            const text = await res.text().catch(() => '');
            let data: any = null;
            try {
              data = JSON.parse(text);
            } catch {
              // non-JSON body — handled below via embeddedError fallback
            }

            const embeddedError = data
              ? extractProviderError(data)
              : String(text).slice(0, 200) || null;

            if (!res.ok || embeddedError) {
              lastError = `${provider.name}: HTTP ${res.status}${embeddedError ? ` — ${embeddedError.slice(0, 120)}` : ''}`;
              console.warn(
                `AI fallback: ${lastError} (auth=${style}, attempt=${attempt})`
              );

              if (isPermanentFailure(res.status, text)) {
                // Try the next auth style before giving up on this provider;
                // only trip the breaker after every style has failed.
                if (style === provider.authStyles[provider.authStyles.length - 1]) {
                  tripBreaker(provider.name);
                }
                break; // permanent — stop retrying this provider
              }

              if (isTransientError(text) && attempt < 3) {
                await sleep(attempt * 800); // 0.8s, then 1.6s
                continue; // retry same provider
              }
              break; // exhausted — try next provider
            }

            const content =
              data?.choices?.[0]?.message?.content || 'No analysis generated';

            providerSucceeded = true;
            return NextResponse.json({
              analysis: content,
              provider: provider.name,
              model: data?.model || provider.model,
              usage: data?.usage || null,
            });
          } catch (err: any) {
            lastError = `${provider.name}: ${err?.message || 'unknown'}`;
            console.warn(`AI fallback: ${lastError} (auth=${style}, attempt=${attempt})`);
            if (attempt < 3) {
              await sleep(attempt * 800);
              continue;
            }
            break;
          }
        }
        if (providerSucceeded) break;
      }

      if (providerSucceeded) break; // kept for clarity
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
