import axios from 'axios'
import { ElMessage } from 'element-plus'

export const api = axios.create({ baseURL: '/' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const detail = error.response?.data?.detail
    if (error.response?.status === 401 && !location.hash.includes('/login')) {
      localStorage.removeItem('token')
      location.hash = '#/login'
    } else if (detail) {
      ElMessage.error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    } else {
      ElMessage.error('请求失败，请检查后端是否在运行')
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
  credential_preview: Record<string, string>
}
