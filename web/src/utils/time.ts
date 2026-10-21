/**
 * Backend timestamps are naive UTC ISO strings (SQLAlchemy stores
 * ``datetime.utcnow()``), so ``new Date(value)`` interprets them as *local*
 * time and shifts every display by the browser's UTC offset.
 *
 * Explicit offsets/Z are already correct and must not be rewritten.  Values
 * without any timezone marker are treated as UTC.
 */
const HAS_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i

export function parseUtc(value: string | null | undefined): Date | null {
  if (!value) return null
  const raw = String(value).trim()
  if (!raw) return null
  const date = new Date(HAS_ZONE.test(raw) ? raw : `${raw}Z`)
  return Number.isNaN(date.getTime()) ? null : date
}

export function utcMs(value: string | null | undefined): number | null {
  const date = parseUtc(value)
  return date ? date.getTime() : null
}

function format(
  value: string | null | undefined,
  options: Intl.DateTimeFormatOptions,
  fallback: string,
): string {
  const date = parseUtc(value)
  return date ? date.toLocaleString('zh-CN', options) : fallback
}

/** Full local date and time, used for logs and exact timestamps. */
export function formatDateTime(value: string | null | undefined, fallback = '—'): string {
  return format(value, { hour12: false }, fallback)
}

/** Local calendar date without time. */
export function formatDate(value: string | null | undefined, fallback = '—'): string {
  return format(value, { year: 'numeric', month: '2-digit', day: '2-digit' }, fallback)
}

/** Compact local month/day/hour/minute label for cards and schedules. */
export function formatMonthDayTime(value: string | null | undefined, fallback = '—'): string {
  return format(
    value,
    { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false },
    fallback,
  )
}

/** Local clock label for logs and near-term queue schedules. */
export function formatClock(value: string | null | undefined, fallback = '—'): string {
  return format(value, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }, fallback)
}
