/** 认证状态（Zustand）：access token 只在内存，refresh 由 HttpOnly cookie 承载。
 *
 * 为什么不持久化 token：
 * - refresh token 原先存 localStorage，任何 XSS 都能读走 7 天有效的凭证；
 *   现在它在 HttpOnly cookie 里，JS 读不到（后端 `/auth/login|refresh` 下发）。
 * - access token 放内存：刷新页面后内存丢失，用 cookie 静默换一对即可，
 *   代价是一次 `/auth/refresh` 请求，换来的是「XSS 拿不到长期凭证」。
 *
 * 持久化只保留 `user`（首屏不闪空白），不保留任何 token。
 */

import axios from 'axios'
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import * as api from '../api/endpoints'

const TOKEN_STORAGE_KEY = 'aw_tokens'

/**
 * 进行中的初始化（模块级）。
 *
 * React StrictMode 下 effect 会跑两次，`initialized` 置位前会有两条并发 init：
 * 两边都去换 token → 服务端看到两次刷新（refresh 一次性消费 + 30s 宽限），
 * 后落盘的那对里可能含已作废的 refresh，下次刷新就把用户踢下线。
 */
let initPromise: Promise<void> | null = null

interface AuthState {
  accessToken: string | null
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
      user: null,
      initialized: false,

      // 只留 access：refresh 在 cookie 里，前端不该也不需要有它
      setTokens: (tokens) => set({ accessToken: tokens.access }),

      login: async (email, password) => {
        const { user, tokens } = await api.login({ email, password })
        set({ user, accessToken: tokens.access })
      },

      register: async (payload) => {
        const { user, tokens } = await api.register(payload)
        set({ user, accessToken: tokens.access })
      },

      logout: () => {
        // 服务端撤销 refresh 并清 cookie；失败不影响本地登出
        void axios.post('/api/v1/auth/logout', {}, { withCredentials: true }).catch(() => undefined)
        set({ user: null, accessToken: null })
      },

      /** 应用启动时恢复会话（幂等，可重复调用）。 */
      init: async () => {
        if (get().initialized) return
        if (initPromise) return initPromise

        initPromise = (async () => {
          // 内存里已有 access（同一次会话内切页面）：验一次即可
          if (get().accessToken) {
            try {
              const user = await api.fetchMe()
              set({ user, initialized: true })
              return
            } catch {
              // access 过期 → 走下面的 cookie 刷新
            }
          }

          // 页面刚加载：内存空，用 HttpOnly cookie 换一对
          try {
            const tokens = await api.refreshToken()
            set({ accessToken: tokens.access })
            const user = await api.fetchMe()
            set({ user, initialized: true })
          } catch {
            set({ user: null, accessToken: null, initialized: true })
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
      // 只持久化用户信息，绝不持久化 token
      partialize: (s) => ({ user: s.user }),
      version: 2,
      // v1 把 access/refresh 存在 localStorage 里；升级时清掉，避免旧 token 被信任
      migrate: (persisted, version) => {
        if (version < 2) {
          return { user: (persisted as { user?: api.UserOut } | null)?.user ?? null }
        }
        return persisted as { user: api.UserOut | null }
      },
    }
  )
)
