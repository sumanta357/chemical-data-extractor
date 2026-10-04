'use client';

import { MoonIcon, SunIcon } from '@/components/icons';
import { useTheme, type Theme } from '@/lib/theme';

/**
 * Theme switcher.
 *
 * A two-state control rather than a single ambiguous toggle: the active theme
 * is visibly selected and announced with `aria-pressed`, so it is obvious both
 * what the current theme is and what a click will do.
 */
export default function ThemeToggle({ className = '' }: { className?: string }) {
  const { theme, setTheme } = useTheme();

  const options: { value: Theme; label: string; Icon: typeof SunIcon }[] = [
    { value: 'light', label: 'Light theme', Icon: SunIcon },
    { value: 'dark', label: 'Dark theme', Icon: MoonIcon },
  ];

  return (
    <div
      role="group"
      aria-label="Colour theme"
      className={`inline-flex shrink-0 items-center gap-0.5 rounded-lg border border-line bg-surface-2 p-0.5 ${className}`}
    >
      {options.map(({ value, label, Icon }) => {
        const active = theme === value;
        return (
          <button
            key={value}
            type="button"
            onClick={() => setTheme(value)}
            aria-pressed={active}
            aria-label={label}
            title={label}
            className={`flex h-7 w-7 items-center justify-center rounded-md transition-colors ${
              active
                ? 'bg-surface text-fg shadow-sm ring-1 ring-line'
                : 'text-fg-subtle hover:text-fg'
            }`}
          >
            <Icon className="h-[15px] w-[15px]" />
          </button>
        );
      })}
    </div>
  );
}
