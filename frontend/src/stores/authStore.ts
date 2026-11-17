/** 认证状态（Zustand，T025）：token 持久化 + 登录/登出/初始化。 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import * as api from '../api/endpoints'

const TOKEN_STORAGE_KEY = 'aw_tokens'

/**
 * 进行中的初始化（模块级）。
 *
 * React StrictMode 下 effect 会跑两次，`initialized` 置位前会有两条并发 init：
 * 两边都拿旧 access token 请求 /me → 都 401 → 同时调 /auth/refresh。
 * 而 refresh token 是一次性消费的（服务端 30s 宽限），两个标签页式并发刷新
 * 可能让后落盘的那对 token 里含已作废的 refresh，下次刷新就把用户踢下线。
 */
let initPromise: Promise<void> | null = null

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  user: api.UserOut | null
  initialized: boolean
  login: (email: string, password: string) => Promise<void>
  register: (payload: api.RegisterRequest) => Promise<void>
  logout: () => void
  init: () => Promise<void>
  setTokens: (tokens: api.TokenPair) => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      initialized: false,

      setTokens: (tokens) => set({ accessToken: tokens.access, refreshToken: tokens.refresh }),

      login: async (email, password) => {
        const { user, tokens } = await api.login({ email, password })
        set({ user, accessToken: tokens.access, refreshToken: tokens.refresh })
      },

      register: async (payload) => {
        const { user, tokens } = await api.register(payload)
        set({ user, accessToken: tokens.access, refreshToken: tokens.refresh })
      },

      logout: () => {
        set({ user: null, accessToken: null, refreshToken: null })
        localStorage.removeItem(TOKEN_STORAGE_KEY)
      },

      /** 应用启动时：有 token 则拉取 me 恢复会话（幂等，可重复调用）。 */
      init: async () => {
        if (get().initialized) return
        if (initPromise) return initPromise

        initPromise = (async () => {
          const { accessToken, refreshToken } = get()
          if (!accessToken || !refreshToken) {
            set({ initialized: true })
            return
          }
          try {
            const user = await api.fetchMe()
            set({ user, initialized: true })
          } catch {
            // token 失效 → 尝试刷新；失败则登出
            try {
              const tokens = await api.refreshToken(refreshToken)
              set({ accessToken: tokens.access, refreshToken: tokens.refresh })
              const user = await api.fetchMe()
              set({ user, initialized: true })
            } catch {
              get().logout()
              set({ initialized: true })
            }
          }
        })()

        try {
          await initPromise
        } finally {
          initPromise = null
        }
      },
    }),
    {
      name: TOKEN_STORAGE_KEY,
      partialize: (s) => ({
        accessToken: s.accessToken,
        refreshToken: s.refreshToken,
        user: s.user,
      }),
    }
  )
)
