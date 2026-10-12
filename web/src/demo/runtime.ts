/**
 * 静态演示站运行时。
 *
 * 构建时通过 VITE_DEMO_MODE=1 打开；打开后：
 * - 不登录，直接进入工作台；
 * - 所有 GET 从本地快照读取，写操作一律拦截；
 * - 媒体 / 封面走本地静态文件，不发起任何网络请求。
 */

export const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === '1'

// 演示站可能挂在落地页的子路径下（如 /demo/），统一走 Vite 的 BASE_URL。
const BASE = import.meta.env.BASE_URL || '/'
const SNAPSHOT_PREFIX = `${BASE}demo-data`

type Snapshot = Record<string, unknown>

let snapshotPromise: Promise<Snapshot> | null = null

export function loadSnapshot(): Promise<Snapshot> {
  if (!snapshotPromise) {
    snapshotPromise = fetch(`${SNAPSHOT_PREFIX}/snapshot.json`).then((resp) => {
      if (!resp.ok) throw new Error(`演示数据加载失败：HTTP ${resp.status}`)
      return resp.json() as Promise<Snapshot>
    })
  }
  return snapshotPromise
}

/** 从快照中按路径取数据；支持 `{参数}` 形式的简单路径匹配。 */
export function resolveSnapshot(snapshot: Snapshot, path: string): unknown {
  const clean = path.split('?')[0].replace(/\/+$/, '').replace(/^\/api/, '') || '/'
  if (Object.prototype.hasOwnProperty.call(snapshot, clean)) return snapshot[clean]
  for (const key of Object.keys(snapshot)) {
    const normalizedKey = key.replace(/^\/api/, '') || '/'
    if (!normalizedKey.includes('{')) continue
    const pattern = new RegExp('^' + normalizedKey.replace(/\{[^}]+\}/g, '([^/]+)') + '$')
    const match = clean.match(pattern)
    if (!match) continue
    const value = snapshot[key]
    if (value && typeof value === 'object' && match[1] && match[1] in (value as Record<string, unknown>)) {
      return (value as Record<string, unknown>)[match[1]]
    }
    return value
  }
  return undefined
}

export function demoReadOnlyMessage() {
  return '演示站为只读展示，数据不可修改'
}

export function mediaSrc(assetId: number | string) {
  return DEMO_MODE ? `${BASE}demo-data/media/${assetId}.mp4` : `/api/assets/${assetId}/media`
}

export function coverSrc(assetId: number | string) {
  return DEMO_MODE ? `${BASE}demo-data/media/${assetId}.png` : `/api/assets/${assetId}/cover`
}

/**
 * 演示模式下只允许同源站内资源。
 *
 * 静态演示站不能依赖外部图床/头像服务：那些地址要么需要登录、要么会过期，
 * 还会让“纯静态”变成对外发起请求。演示模式下外部 URL 一律丢弃，
 * 由调用方回落到文字/占位符。
 */
export function localAssetUrl(url?: string | null): string {
  if (!url) return ''
  if (!DEMO_MODE) return url
  return url.startsWith('/') ? url : ''
}
