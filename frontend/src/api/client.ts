/** API 客户端：axios 实例 + token 注入 + 401 登出。 */

import axios from 'axios'

import { useAuthStore } from '../stores/authStore'

export const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
})

// 请求拦截：注入 Bearer token
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截：401 时清空会话并跳登录
apiClient.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error.response?.status === 401) {
      useAuthStore.getState().logout()
    }
    return Promise.reject(error)
  },
)

/** 统一错误提取：`{"error": {"code": "...", "message": "..."}}` */
export function extractError(error: unknown): string {
  const data = (error as { response?: { data?: { error?: { message?: string } } } })
    ?.response?.data
  return data?.error?.message ?? '请求失败，请稍后重试'
}
