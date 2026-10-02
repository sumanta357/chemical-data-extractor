/**
 * Supabase Auth — browser client.
 *
 * Identity lives in Supabase; this app only reads the session that Supabase
 * hands back and attaches its access token to API calls. No token is ever
 * parsed or trusted here, and nothing about the user is invented client-side:
 * the server asks Supabase who the token belongs to.
 *
 * The project URL and the anon/publishable key are fetched from
 * /api/billing/config at runtime rather than baked into the static export, so
 * rotating the key is a server-side env change and no rebuild is needed.
 */

import { createClient, type SupabaseClient } from '@supabase/supabase-js';

let client: SupabaseClient | null = null;
let configuredUrl = '';

export function isSupabaseConfigured(url?: string | null): boolean {
  return Boolean(url);
}

/** Create (once) the Supabase client. Returns null when it is not configured.
 *
 *  Called with no arguments it returns the client built earlier. Pages that
 *  load their config asynchronously (e.g. /reset-password) rely on this: the
 *  cached client is the only way their later handlers can reach it.
 */
export function getSupabase(url?: string | null, anonKey?: string | null): SupabaseClient | null {
  // No arguments: hand back the client an earlier call already created. Without
  // this, a page that bootstraps the client from /api/billing/config gets null
  // in its submit handler and silently does nothing.
  if (!url && !anonKey) return client;
  if (!url || !anonKey) return null;
  if (client && configuredUrl === url) return client;

  client = createClient(url, anonKey, {
    auth: {
      // Supabase stores the session and refreshes it for us; this app never
      // handles a password or a refresh token itself.
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
      flowType: 'pkce',
    },
  });
  configuredUrl = url;
  return client;
}

/** The current access token, or null when nobody is signed in. */
export async function getAccessToken(supabase: SupabaseClient | null): Promise<string | null> {
  if (!supabase) return null;
  try {
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    return null;
  }
}

export function authErrorMessage(error: unknown): string {
  const message = (error as { message?: string } | null)?.message || '';
  if (/invalid login credentials/i.test(message)) return 'Incorrect email or password';
  if (/email not confirmed/i.test(message)) return 'Confirm your email address before signing in';
  if (/already registered/i.test(message)) return 'An account with this email already exists';
  if (/rate limit|too many/i.test(message)) return 'Too many attempts. Please wait a moment.';
  if (/failed to fetch|network/i.test(message)) return 'Could not reach the sign-in service';
  return message || 'Something went wrong. Please try again.';
}