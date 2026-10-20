/**
 * Run with: node_modules/.bin/esbuild src/utils/time.test.ts --bundle --platform=node \
 *           --format=esm --outfile=/tmp/time.test.mjs && node /tmp/time.test.mjs
 */
import { parseUtc } from './time'

const cases: Array<[string | null, number | null]> = [
  // Naive backend UTC must not be reinterpreted as local time.
  ['2026-10-19T12:00:00', Date.UTC(2026, 9, 19, 12)],
  // Explicit zone markers must be preserved.
  ['2026-10-19T12:00:00Z', Date.UTC(2026, 9, 19, 12)],
  ['2026-10-19T20:00:00+08:00', Date.UTC(2026, 9, 19, 12)],
  ['', null],
  [null, null],
  ['not-a-date', null],
]

for (const [input, expected] of cases) {
  const got = parseUtc(input)?.getTime() ?? null
  if (got !== expected) {
    throw new Error(`parseUtc(${JSON.stringify(input)}) = ${got}, expected ${expected}`)
  }
}
console.log(`parseUtc OK (${cases.length} cases)`)
