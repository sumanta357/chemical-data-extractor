'use client';

/**
 * Landing route for the "confirm your email" link. The account panel reads
 * ?token= from the URL and verifies it, then reports success or an expired
 * link here.
 */

import { useEffect, useState } from 'react';
import AccountPanel from '@/components/AccountPanel';
import { verifyEmail } from '@/lib/billing';

export default function VerifyEmailPage() {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<'working' | 'ok' | 'error'>('working');
  const [message, setMessage] = useState('Confirming your email address…');

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get('token');
    if (!token) {
      setState('error');
      setMessage('This link is missing its confirmation token.');
      return;
    }
    verifyEmail(token)
      .then((res) => {
        setState('ok');
        setMessage(res.message);
        setOpen(true);
      })
      .catch((err: any) => {
        setState('error');
        setMessage(err.message || 'This confirmation link is invalid or has expired.');
      });
  }, []);

  return (
    <main className="min-h-screen bg-[#050912] text-gray-300 px-4 py-16">
      <div className="max-w-md mx-auto space-y-3 text-center">
        <h1 className="text-lg font-semibold text-gray-100">Email confirmation</h1>
        <p className={`text-sm ${state === 'error' ? 'text-amber-400' : 'text-gray-500'}`}>
          {message}
        </p>
      </div>
      <div className="mt-8">
        <AccountPanel
          open={open}
          onOpen={() => setOpen(true)}
          onClose={() => setOpen(false)}
          hideTrigger
        />
      </div>
    </main>
  );
}
