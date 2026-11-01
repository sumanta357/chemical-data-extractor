'use client';

import { useState, useCallback } from 'react';

export interface PasswordFieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  autoComplete?: string;
  required?: boolean;
  minLength?: number;
  error?: string | null;
  hint?: string | null;
  showStrength?: boolean;
}

export default function PasswordField({
  id,
  label,
  value,
  onChange,
  placeholder = 'Enter your password',
  autoComplete = 'new-password',
  required,
  minLength,
  error,
  hint,
  showStrength,
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);
  const toggle = useCallback(() => setVisible((v) => !v), []);

  return (
    <div>
      <label htmlFor={id} className="label">
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type={visible ? 'text' : 'password'}
          autoComplete={autoComplete}
          required={required}
          minLength={minLength}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="field pr-10"
        />
        <button
          type="button"
          onClick={toggle}
          className="absolute right-1.5 top-1/2 -translate-y-1/2"
          aria-label={visible ? 'Hide password' : 'Show password'}
          title={visible ? 'Hide password' : 'Show password'}
        >
          {visible ? (
            <svg
              className="h-4 w-4 text-fg-muted"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
              <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
              <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24" />
              <line x1="1" y1="1" x2="23" y2="23" />
            </svg>
          ) : (
            <svg
              className="h-4 w-4 text-fg-muted"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          )}
        </button>
      </div>
      {hint && <p className="mt-1.5 text-[11px] text-fg-subtle">{hint}</p>}
      {showStrength && value && (
        <p className="mt-1.5 text-[11px] text-fg-muted">
          {value.length < (minLength ?? 8)
            ? `At least ${minLength ?? 8} characters`
            : 'Looks good'}
        </p>
      )}
    </div>
  );
}
