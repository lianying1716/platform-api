import axios from 'axios'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import {
  DEMO_MODE,
  demoReadOnlyMessage,
  loadSnapshot,
  paginateSnapshot,
  resolveSnapshot,
} from './demo/runtime'
import {
  apiRoutes,
  type AccountOut,
  type ApiOperation,
  type ApiPathParams,
  type ApiQueryParams,
  type ApiRequestBodies,
  type ApiRequiredBodyOperations,
  type ApiResponses,
  type ApiTotalHeaderOperations,
  type PlatformInfo as GeneratedPlatformInfo,
} from './api.generated'

export const api = axios.create({ baseURL: '/' })

type RouteParams = Record<string, string | number>
type BodyFor<Op extends ApiOperation> = Op extends keyof ApiRequestBodies
  ? ApiRequestBodies[Op]
  : undefined
type PathFor<Op extends ApiOperation> = Op extends keyof ApiPathParams ? ApiPathParams[Op] : RouteParams
type QueryFor<Op extends ApiOperation> = Op extends keyof ApiQueryParams ? ApiQueryParams[Op] : RouteParams
type CallParams<Op extends ApiOperation> = PathFor<Op> & QueryFor<Op>
type CallOptions<Op extends ApiOperation> = Op extends keyof ApiRequiredBodyOperations
  ? { body: BodyFor<Op>; signal?: AbortSignal }
  : { body?: BodyFor<Op>; signal?: AbortSignal }

/**
 * Resolve a FastAPI operation ID to its current URL. Path parameters are
 * substituted from ``params`` and removed from the query string; missing
 * parameters fail before a request can hit the backend.
 */
function operationUrl(operation: ApiOperation, params: RouteParams): { url: string; query: RouteParams } {
  const route = apiRoutes[operation]
  const query = { ...params }
  const url = route.path.replace(/\{([^}]+)\}/g, (_match, name: string) => {
    const value = query[name]
    if (value === undefined || value === '') {
      throw new Error(`缺少接口参数 ${name}：${operation}`)
    }
    delete query[name]
    return encodeURIComponent(String(value))
  })
  return { url, query }
}

type OperationResult<Op extends ApiOperation> = Op extends keyof ApiResponses
  ? ApiResponses[Op]
  : unknown

/**
 * Call a generated FastAPI operation. Unlike a hand-written URL, the operation
 * ID and every path parameter are checked by TypeScript, and the response type
 * follows the backend schema automatically.
 */
export async function apiCall<Op extends ApiOperation>(
  operation: Op,
  params: CallParams<Op> = {} as CallParams<Op>,
  options: CallOptions<Op> = {} as CallOptions<Op>,
): Promise<OperationResult<Op>> {
  const { url, query } = operationUrl(operation, params as RouteParams)
  const { data } = await api.request<OperationResult<Op>>({
    url,
    method: apiRoutes[operation].method,
    params: Object.keys(query).length ? query : undefined,
    data: options.body,
    signal: options.signal,
  })
  return data
}

/** For list operations whose full total is in X-Total-Count. */
export async function apiCallWithMeta<Op extends ApiTotalHeaderOperations & ApiOperation>(
  operation: Op,
  params: CallParams<Op> = {} as CallParams<Op>,
  options: { signal?: AbortSignal } = {},
): Promise<{
  data: Op extends keyof ApiResponses ? ApiResponses[Op] : unknown
  total: number
}> {
  const { url, query } = operationUrl(operation as ApiOperation, params as RouteParams)
  const response = await api.request<Op extends keyof ApiResponses ? ApiResponses[Op] : unknown>({
    url,
    method: apiRoutes[operation as ApiOperation].method,
    params: Object.keys(query).length ? query : undefined,
    signal: options.signal,
  })
  const raw = response.headers['x-total-count']
  if (raw === undefined || raw === null || raw === '') {
    throw new Error(`接口 ${operation} 未返回 X-Total-Count，无法确认列表总数`)
  }
  const total = Number(raw)
  if (!Number.isInteger(total) || total < 0) {
    throw new Error(`接口 ${operation} 返回了非法 X-Total-Count：${String(raw)}`)
  }
  return { data: response.data, total }
}

/**
 * Fetch a binary endpoint through the same generated operation registry.
 * Export/download buttons used to concatenate URLs by hand, which meant their
 * path and response mode were invisible to the contract generator.
 */
export async function apiDownload<Op extends ApiOperation>(
  operation: Op,
  params: CallParams<Op> = {} as CallParams<Op>,
  options: CallOptions<Op> = {} as CallOptions<Op>,
): Promise<Blob> {
  const { url, query } = operationUrl(operation, params as RouteParams)
  const { data } = await api.request<Blob>({
    url,
    method: apiRoutes[operation].method,
    params: Object.keys(query).length ? query : undefined,
    data: options.body,
    signal: options.signal,
    responseType: 'blob',
  })
  return data
}

if (DEMO_MODE) {
  // 演示站：GET 从本地快照读，写操作统一拦截；不产生任何网络请求。
  api.interceptors.request.use(async (config) => {
    const method = (config.method || 'get').toLowerCase()
    if (method !== 'get') {
      ElMessage.warning(demoReadOnlyMessage())
      return Promise.reject(new axios.Cancel('demo-readonly'))
    }
    const snapshot = await loadSnapshot()
    const value = resolveSnapshot(snapshot, config.url || '')
    if (value === undefined) {
      ElMessage.warning('演示数据未覆盖该页面')
      return Promise.reject(new axios.Cancel('demo-missing'))
    }
    const paged = paginateSnapshot(value, config.params as Record<string, unknown> | undefined)
    config.adapter = async () => {
      const headers: Record<string, string> = {}
      // Only list responses carry a logical total.  Adding a fake total=1 to
      // object responses would make generated list pages believe a single
      // object is a one-row page.
      if (Array.isArray(paged.data) && paged.total !== null) {
        headers['x-total-count'] = String(paged.total)
      }
      return { data: paged.data, status: 200, statusText: 'OK', headers, config }
    }
    return config
  })
}

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (axios.isCancel(error)) return Promise.reject(error)
    const detail = error.response?.data?.detail
    if (error.response?.status === 401 && !location.hash.includes('/login')) {
      localStorage.removeItem('token')
      location.hash = '#/login'
      ElMessage.error(typeof detail === 'string' ? detail : '登录已过期，请重新登录')
    } else if (detail) {
      ElMessage.error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    } else if (error.response) {
      // 请求已经抵达后端并拿到 HTTP 响应：这是接口错误，不是“后端没启动”。
      // 带上状态码和路径，避免 500/404 被误报成“后端连接不上”。
      const status = error.response.status
      const path = error.config?.url || ''
      ElMessage.error(`请求失败（HTTP ${status}）：${path}`)
    } else if (error.code === 'ECONNABORTED') {
      ElMessage.error('请求超时：后端响应过慢或正在处理重任务')
    } else {
      ElMessage.error('后端无法连接：请确认 platform-api 开发服务正在运行')
    }
    return Promise.reject(error)
  },
)

// The transport shape comes from FastAPI's OpenAPI document.  Keeping these
// aliases here gives pages a stable import while making a renamed or removed
// backend field a TypeScript error at `npm run typecheck`.
export type PlatformInfo = GeneratedPlatformInfo & {
  source_types: { value: string; label: string }[]
  download_qualities: { value: string; label: string }[]
}
// Pydantic defaults are optional in the transport schema but always present on
// a response.  Narrow them here once so pages do not need ``!`` assertions.
export type Account = AccountOut & Required<Pick<
  AccountOut,
  | 'platform_label'
  | 'storage_root_id'
  | 'storage_root_name'
  | 'storage_root_path'
  | 'sync_enabled'
  | 'credential_preview'
>>
