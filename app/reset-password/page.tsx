'use client';

/**
 * Landing route for the "reset your password" link in the reset email.
 * The panel reads ?token= from the URL and shows the new-password form.
 */

import { useState } from 'react';
import AccountPanel from '@/components/AccountPanel';

export default function ResetPasswordPage() {
  const [open, setOpen] = useState(false);

  return (
    <main className="min-h-screen bg-[#050912] text-gray-300 px-4 py-16">
      <div className="max-w-md mx-auto space-y-3 text-center">
        <h1 className="text-lg font-semibold text-gray-100">Reset your password</h1>
        <p className="text-sm text-gray-500">
          Choose a new password for your SciGraph account.
        </p>
        <button
          onClick={() => setOpen(true)}
          className="text-sm px-4 py-2 border border-cyan-600 text-cyan-400 hover:bg-cyan-500/10 transition-colors"
        >
          {open ? 'Form open below' : 'Open reset form'}
        </button>
      </div>
      <div className="mt-8">
        <AccountPanel
          open={open}
          onOpen={() => setOpen(true)}
          onClose={() => setOpen(false)}
          initiallyOpen
          hideTrigger
        />
      </div>
    </main>
  );
}
