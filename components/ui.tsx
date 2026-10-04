/**
 * Shared UI primitives.
 *
 * These are class-name helpers rather than components: every control in the app
 * is a plain <button>/<input>/<a>, and keeping them plain means styling lives
 * with the markup while the *specification* of a control — its height, radius,
 * focus ring, disabled behaviour — is written exactly once, here.
 */

/** Conditional class names, falsy entries dropped. */
export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

/* ── Buttons ───────────────────────────────────────────────────────────────
   One height (36px), one radius, one focus treatment. The primary variant is
   the only filled-cyan control in the product, so "the main action" is never
   ambiguous. */

const BTN_BASE =
  'inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium ' +
  'transition-colors duration-150 disabled:pointer-events-none disabled:opacity-55';

export const btnPrimary = cx(
  BTN_BASE,
  'bg-accent-solid text-on-accent hover:bg-accent-hover'
);

export const btnSecondary = cx(
  BTN_BASE,
  'border border-line-strong bg-surface text-fg hover:bg-surface-2'
);

export const btnGhost = cx(BTN_BASE, 'text-fg-muted hover:bg-surface-2 hover:text-fg');

/* ── Segmented control ──────────────────────────────────────────────────────
   Used for query type and hop depth: a small set of mutually exclusive
   options, so a radio group is the honest control. */

export const segmentWrap = 'flex gap-1 rounded-lg border border-line bg-surface-2 p-1';

const segmentItem =
  'flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors disabled:opacity-55';

export function segmentItemClass(active: boolean): string {
  return cx(
    segmentItem,
    active
      ? 'bg-surface text-fg shadow-sm ring-1 ring-line'
      : 'text-fg-muted hover:text-fg'
  );
}

/* ── Status ────────────────────────────────────────────────────────────────
   One pill per status, used in the progress header and in search history. */

export type StatusKey = 'queued' | 'running' | 'completed' | 'failed';

const STATUS_TONE: Record<StatusKey, string> = {
  queued: 'text-warning bg-warning-soft border-warning-line',
  running: 'text-accent bg-accent-soft border-accent-line',
  completed: 'text-success bg-success-soft border-success-line',
  failed: 'text-danger bg-danger-soft border-danger-line',
};

export const STATUS_LABEL: Record<StatusKey, string> = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
};

/* ── Inline messages ───────────────────────────────────────────────────────
   Notices, warnings and errors inside a panel. */

export function statusKey(value: string): StatusKey {
  return (['queued', 'running', 'completed', 'failed'] as const).includes(
    value as StatusKey
  )
    ? (value as StatusKey)
    : 'queued';
}

export function statusPill(status: string): string {
  const key = statusKey(status);
  return cx(
    'inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide',
    STATUS_TONE[key]
  );
}

export function note(
  tone: 'info' | 'success' | 'warning' | 'danger'
): string {
  const tones = {
    info: 'border-info-line bg-info-soft text-info',
    success: 'border-success-line bg-success-soft text-success',
    warning: 'border-warning-line bg-warning-soft text-warning',
    danger: 'border-danger-line bg-danger-soft text-danger',
  } as const;
  return cx('rounded-lg border px-3 py-2 text-xs leading-relaxed', tones[tone]);
}
