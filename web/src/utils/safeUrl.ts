/** Return a URL only when it is an absolute http(s) address.
 *
 * Platform-provided home pages and article links are untrusted.  Rendering a
 * ``javascript:`` or ``data:`` URL would execute in the admin origin even when
 * the surrounding HTML is otherwise safe.
 */
export function safeExternalUrl(value?: string | null): string {
  if (!value) return ''
  try {
    const parsed = new URL(value, window.location.origin)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : ''
  } catch {
    return ''
  }
}
