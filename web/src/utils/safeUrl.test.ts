/**
 * Run with: node_modules/.bin/esbuild src/utils/safeUrl.test.ts --bundle \
 *           --platform=node --format=esm --outfile=/tmp/safeUrl.test.mjs && node /tmp/safeUrl.test.mjs
 */
import { safeExternalUrl } from './safeUrl'

Object.defineProperty(globalThis, 'window', { value: { location: { origin: 'http://127.0.0.1:9100' } } })

const cases: Array<[string | null, string]> = [
  ['https://www.bilibili.com/video/BV1', 'https://www.bilibili.com/video/BV1'],
  ['http://example.com/a', 'http://example.com/a'],
  ['javascript:alert(1)', ''],
  ['data:text/html,<script>alert(1)</script>', ''],
  ['file:///etc/passwd', ''],
  ['', ''],
  [null, ''],
]

for (const [input, expected] of cases) {
  const got = safeExternalUrl(input)
  if (got !== expected) {
    throw new Error(`safeExternalUrl(${JSON.stringify(input)}) = ${got}, expected ${expected}`)
  }
}
console.log(`safeExternalUrl OK (${cases.length} cases)`)
