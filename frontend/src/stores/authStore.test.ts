/** authStore.init 幂等性 + 「token 不进 localStorage」。
 *
 * 背景（2026-11-17）：
 * - App.tsx 原先在 render body 里调 init()，StrictMode 下跑两次，可能并发刷新；
 * - refresh token 原先存 localStorage，XSS 可直接读走 → 现改由 HttpOnly cookie 承载，
 *   前端只把 access 放内存，持久化只留 user。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api/endpoints'
import { useAuthStore } from './authStore'

const USER = { id: 'u1', email: 'a@b.com', role: 'member' as const, status: 'active' }

beforeEach(() => {
  localStorage.clear()
  useAuthStore.setState({ accessToken: null, user: null, initialized: false })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('authStore.init', () => {
  it('内存里有 access：只验一次 /auth/me，不刷新', async () => {
    useAuthStore.setState({ accessToken: 'access-1' })
    const me = vi.spyOn(api, 'fetchMe').mockResolvedValue(USER)
    const refresh = vi.spyOn(api, 'refreshToken')

    await Promise.all([
      useAuthStore.getState().init(),
      useAuthStore.getState().init(),
      useAuthStore.getState().init(),
    ])

    expect(me).toHaveBeenCalledTimes(1)
    expect(refresh).not.toHaveBeenCalled()
    expect(useAuthStore.getState().initialized).toBe(true)
    expect(useAuthStore.getState().user).toEqual(USER)
  })

  it('页面刚加载（内存无 access）：用 cookie 换 token 再取用户', async () => {
    const refresh = vi
      .spyOn(api, 'refreshToken')
      .mockResolvedValue({ access: 'access-2', refresh: 'ignored' })
    const me = vi.spyOn(api, 'fetchMe').mockResolvedValue(USER)

    await useAuthStore.getState().init()

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(me).toHaveBeenCalledTimes(1)
    expect(useAuthStore.getState().accessToken).toBe('access-2')
    expect(useAuthStore.getState().user).toEqual(USER)
  })

  it('access 过期：先 /me 失败，再用 cookie 刷新一次', async () => {
    useAuthStore.setState({ accessToken: 'stale' })
    vi.spyOn(api, 'fetchMe')
      .mockRejectedValueOnce(new Error('401'))
      .mockResolvedValue(USER)
    const refresh = vi
      .spyOn(api, 'refreshToken')
      .mockResolvedValue({ access: 'access-3', refresh: 'ignored' })

    await Promise.all([useAuthStore.getState().init(), useAuthStore.getState().init()])

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(useAuthStore.getState().accessToken).toBe('access-3')
  })

  it('没有有效 cookie：静默结束，不留下脏 token', async () => {
    vi.spyOn(api, 'refreshToken').mockRejectedValue(new Error('401'))

    await useAuthStore.getState().init()

    expect(useAuthStore.getState().initialized).toBe(true)
    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('已初始化后再次调用不再发请求', async () => {
    useAuthStore.setState({ accessToken: 'access-1' })
    const me = vi.spyOn(api, 'fetchMe').mockResolvedValue(USER)

    await useAuthStore.getState().init()
    await useAuthStore.getState().init()

    expect(me).toHaveBeenCalledTimes(1)
  })
})

describe('token 持久化策略', () => {
  it('localStorage 里不出现任何 token', async () => {
    useAuthStore.setState({ accessToken: 'secret-access', user: USER })
    // 触发一次持久化写入
    await Promise.resolve()

    const raw = localStorage.getItem('aw_tokens') ?? ''
    expect(raw).not.toContain('secret-access')
    expect(raw).not.toContain('refresh')
  })
})
