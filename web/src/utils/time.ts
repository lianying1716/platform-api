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
