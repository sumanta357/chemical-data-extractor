/**
 * Icon set.
 *
 * The project ships no icon package, and adding one for a dozen glyphs would
 * be a large dependency for a small job. Instead every icon is an inline SVG
 * on the same 24×24 grid, drawn with a 1.6px stroke in `currentColor`, so they
 * align optically and inherit colour and opacity from their container.
 */

type IconProps = {
  className?: string;
  strokeWidth?: number;
};

function base(className = 'h-4 w-4', strokeWidth = 1.6) {
  return {
    className,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  };
}

export function SunIcon({ className, strokeWidth }: IconProps) {
  return (
    <svg {...base(className, strokeWidth)}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

export function MoonIcon({ className, strokeWidth }: IconProps) {
  return (
    <svg {...base(className, strokeWidth)}>
      <path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.2 8.2 0 1 0 10.2 10.2Z" />
    </svg>
  );
}

export function SearchIcon({ className, strokeWidth }: IconProps) {
  return (
    <svg {...base(className, strokeWidth)}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </svg>
  );
}

export function HistoryIcon({ className, strokeWidth }: IconProps) {
  return (
    <svg {...base(className, strokeWidth)}>
      <path d="M3.5 9A9 9 0 1 1 3 12.5" />
      <path d="M3 4.5V9h4.5" />
      <path d="M12 7.5V12l3 1.8" />
    </svg>
  );
}

export function UserIcon({ className, strokeWidth }: IconProps) {
  return (
    <svg {...base(className, strokeWidth)}>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 19.5c1.2-3.4 3.8-5 7-5s5.8 1.6 7 5" />
    </svg>
  );
}

export function DownloadIcon({ className, strokeWidth }: IconProps) {
  return (
    <svg {...base(className, strokeWidth)}>
      <path d="M12 3.5v11" />
      <path d="m7.5 10.5 4.5 4.5 4.5-4.5" />
      <path d="M4.5 18.5v1a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-1" />
    </svg>
  );
}

export function BookIcon({ className, strokeWidth }: IconProps) {
  return (
    <svg {...base(className, strokeWidth)}>
      <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11v16H5.5A1.5 1.5 0 0 1 4 18.5Z" />
      <path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H13v16h5.5a1.5 1.5 0 0 0 1.5-1.5Z" />
    </svg>
  );
}

export function CloseIcon({ className, strokeWidth }: IconProps) {
  return (
    <svg {...base(className, strokeWidth)}>
      <path d="m6 6 12 12M18 6 6 18" />
    </svg>
  );
}

export function AlertIcon({ className, strokeWidth }: IconProps) {
  return (
    <svg {...base(className, strokeWidth)}>
      <path d="M12 4.5 21 19.5H3Z" />
      <path d="M12 10v4" />
      <path d="M12 17h.01" />
    </svg>
  );
}

export function CheckIcon({ className, strokeWidth }: IconProps) {
  return (
    <svg {...base(className, strokeWidth)}>
      <path d="m5 12.5 4.5 4.5L19 7" />
    </svg>
  );
}

export function ChevronIcon({ className, strokeWidth }: IconProps) {
  return (
    <svg {...base(className, strokeWidth)}>
      <path d="m8 10 4 4 4-4" />
    </svg>
  );
}

export function ArrowRightIcon({ className, strokeWidth }: IconProps) {
  return (
    <svg {...base(className, strokeWidth)}>
      <path d="M4.5 12h15" />
      <path d="m13.5 6 6 6-6 6" />
    </svg>
  );
}

export function SpinnerIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className ?? 'h-4 w-4'} aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

/** Small three-dot indicator used for "running" and "queued" states. */
export function EllipsisIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className ?? 'h-3.5 w-3.5'} aria-hidden>
      <circle cx="5" cy="12" r="2" />
      <circle cx="12" cy="12" r="2" />
      <circle cx="19" cy="12" r="2" />
    </svg>
  );
}

/**
 * Brand mark: a navy hexagonal cell holding an extraction arrow, with a green
 * circuit trace climbing out of it for the multi-hop growth the product does.
 *
 * Loaded from public/logo.svg rather than inlined, so the artwork is a real
 * file that can be replaced, downloaded and reused elsewhere. Two variants are
 * shipped because an <img> cannot read the page's CSS variables: the navy is
 * lifted in the Dark file so it does not disappear against a near-black canvas.
 *
 * The switch is driven by the `dark` class that app/layout.tsx writes onto
 * <html>, not a Tailwind `dark:` variant — this project's Tailwind config uses
 * the default media strategy, which would ignore the manual theme toggle.
 */
export function LogoMark({ className = 'h-8 w-8' }: { className?: string }) {
  return (
    <>
      <img
        src="/logo.svg"
        alt=""
        width={64}
        height={64}
        className={`logo-art logo-art--light ${className}`}
      />
      <img
        src="/logo-dark.svg"
        alt=""
        width={64}
        height={64}
        className={`logo-art logo-art--dark ${className}`}
      />
    </>
  );
}
