/** API 客户端：axios 实例 + token 注入 + 401 自动刷新重放。 */

import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'

import { useAuthStore } from '../stores/authStore'

export const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
})

/** 请求拦截：注入 Bearer token */
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

interface RetriableConfig extends InternalAxiosRequestConfig {
  /** 已重放过一次，避免无限循环 */
  _retried?: boolean
}

const REFRESH_URL = '/auth/refresh'

/** 进行中的刷新请求（并发 401 复用同一次刷新，避免刷新风暴与 refresh token 竞态） */
let refreshing: Promise<string | null> | null = null

async function requestNewAccessToken(): Promise<string | null> {
  const { refreshToken, setTokens, logout } = useAuthStore.getState()
  if (!refreshToken) {
    logout()
    return null
  }
  try {
    // 走裸 axios（不经 apiClient）：否则刷新失败又触发拦截器 → 递归
    const { data } = await axios.post<{ tokens: { access: string; refresh: string } }>(
      `/api/v1${REFRESH_URL}`,
      { refresh_token: refreshToken },
    )
    setTokens(data.tokens)
    return data.tokens.access
  } catch {
    logout()
    return null
  }
}

/**
 * 用 refresh token 换新 access token（并发去重）。
 *
 * 多个请求同时 401 时只发一次刷新，其余等待同一 Promise。
 */
export function refreshAccessToken(): Promise<string | null> {
  refreshing ??= requestNewAccessToken().finally(() => {
    refreshing = null
  })
  return refreshing
}

/** 重置内部刷新状态（仅测试用）。 */
export function __resetRefreshState(): void {
  refreshing = null
}

/** 响应拦截：401 → 刷新 token → 重放原请求；刷新失败才登出 */
apiClient.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError) => {
    const original = error.config as RetriableConfig | undefined
    const status = error.response?.status

    // access token 只有 30 分钟：过期时先尝试静默续期，而不是把用户踢回登录页
    const shouldRefresh =
      status === 401 &&
      original !== undefined &&
      !original._retried &&
      !(original.url ?? '').includes(REFRESH_URL)

    if (!shouldRefresh) {
      if (status === 401) {
        useAuthStore.getState().logout()
      }
      return Promise.reject(error)
    }

    original._retried = true
    const token = await refreshAccessToken()
    if (!token) {
      return Promise.reject(error)
    }

    original.headers.Authorization = `Bearer ${token}`
    return apiClient(original)
  },
)

/** 统一错误提取：`{"error": {"code": "...", "message": "..."}}` */
export function extractError(error: unknown): string {
  const data = (error as { response?: { data?: { error?: { message?: string } } } })
    ?.response?.data
  return data?.error?.message ?? '请求失败，请稍后重试'
}
