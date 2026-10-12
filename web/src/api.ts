import axios from 'axios'
import { ElMessage } from 'element-plus'
import { DEMO_MODE, demoReadOnlyMessage, loadSnapshot, resolveSnapshot } from './demo/runtime'

export const api = axios.create({ baseURL: '/' })

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
    config.adapter = async () => ({
      data: value,
      status: 200,
      statusText: 'OK',
      headers: { 'x-total-count': String(Array.isArray(value) ? value.length : 1) },
      config,
    })
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

export interface PlatformInfo {
  platform: string
  label: string
  credential_kind: string
  supports_qr: boolean
  credential_fields: { key: string; label: string; required: boolean }[]
}

export interface Account {
  id: number
  platform: string
  platform_label: string
  display_name: string
  external_id: string
  status: string
  status_message: string
  last_checked_at: string | null
  expires_at: string | null
  storage_root_id: number | null
  storage_root_name: string
  storage_root_path: string
  credential_preview: Record<string, string>
}
