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
 * Drawn inline rather than loaded as a bitmap so it stays crisp at any size,
 * costs no request, and recolours with the theme through --logo-ink and
 * --logo-accent. Swap in the supplied brand artwork when it is available and
 * this becomes a wrapper around an <img>.
 */
export function LogoMark({ className = 'h-8 w-8' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 64 64"
      className={className}
      fill="none"
      aria-hidden
      focusable="false"
    >
      {/* Outer cell: the hexagon, drawn as one closed path with rounded joins
          so the corners read soft at 20px as well as at 64px. */}
      <path
        d="M32 3.5 58.5 18v28L32 60.5 5.5 46V18Z"
        stroke="rgb(var(--logo-ink))"
        strokeWidth={5}
        strokeLinejoin="round"
      />
      {/* Inner offset cell, cut away on the lower left where the arrow exits. */}
      <path
        d="M40 13.5 55.5 22.5v18L40 49.5"
        stroke="rgb(var(--logo-ink))"
        strokeWidth={5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Extraction arrow, pointing down and out of the cell. */}
      <path
        d="M36 27 21 39"
        stroke="rgb(var(--logo-ink))"
        strokeWidth={6}
        strokeLinecap="round"
      />
      <path d="M13 45 30 41.5 22.5 29Z" fill="rgb(var(--logo-ink))" />
      {/* Circuit trace climbing out of the cell to the upper right. */}
      <path
        d="M34 44.5 50 29"
        stroke="rgb(var(--logo-accent))"
        strokeWidth={5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M42.5 26.5 53.5 25 51.5 36"
        stroke="rgb(var(--logo-accent))"
        strokeWidth={5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Solder pads. */}
      <rect x="30" y="20" width="7" height="7" rx="1.5" fill="rgb(var(--logo-accent))" />
      <rect x="43" y="39" width="7" height="7" rx="1.5" fill="rgb(var(--logo-accent))" />
      <rect x="49" y="13" width="6" height="6" rx="1.5" fill="rgb(var(--logo-accent))" />
    </svg>
  );
}
