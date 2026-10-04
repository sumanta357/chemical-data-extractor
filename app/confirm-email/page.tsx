'use client';

/**
 * Dedicated landing route for Supabase's "Confirm email" mail.
 *
 * This path only receives traffic if it has been added to the project's
 * Redirect URLs. The default target is the Site URL with `?confirmed=1`, which
 * needs no dashboard change — see HomePage. Both render the same panel.
 */

import { SiteFooter, SiteHeader } from '@/components/SiteChrome';
import ConfirmEmailPanel from '@/components/ConfirmEmailPanel';

export default function ConfirmEmailPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader current="app" />

      <main className="flex flex-1 items-center justify-center px-4 py-12">
        <ConfirmEmailPanel />
      </main>

      <SiteFooter />
    </div>
  );
}