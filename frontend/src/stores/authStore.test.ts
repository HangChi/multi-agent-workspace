/** authStore.init 幂等性：并发/重复调用只能触发一次会话恢复。
 *
 * 背景（2026-11-17 审计）：App.tsx 原先在 render body 里直接调 init()，
 * StrictMode 下启动即执行两次；而 init 没有幂等守卫，两条链路在 401 后会
 * 同时打 /auth/refresh —— refresh token 是一次性消费的，可能让最后落盘的
 * 那对 token 含已作废的 refresh，下次刷新就把用户踢下线。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api/endpoints'
import { useAuthStore } from './authStore'

const USER = { id: 'u1', email: 'a@b.com', role: 'member' as const, status: 'active' }

beforeEach(() => {
  useAuthStore.setState({
    accessToken: 'access-1',
    refreshToken: 'refresh-1',
    user: null,
    initialized: false,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('authStore.init', () => {
  it('并发调用只请求一次 /auth/me', async () => {
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

  it('已初始化后再次调用不再发请求', async () => {
    const me = vi.spyOn(api, 'fetchMe').mockResolvedValue(USER)

    await useAuthStore.getState().init()
    await useAuthStore.getState().init()

    expect(me).toHaveBeenCalledTimes(1)
  })

  it('无 token 时直接置 initialized，不发请求', async () => {
    const me = vi.spyOn(api, 'fetchMe')
    useAuthStore.setState({ accessToken: null, refreshToken: null, initialized: false })

    await useAuthStore.getState().init()

    expect(me).not.toHaveBeenCalled()
    expect(useAuthStore.getState().initialized).toBe(true)
  })

  it('并发 401 时只刷新一次 token', async () => {
    const me = vi
      .spyOn(api, 'fetchMe')
      .mockRejectedValueOnce(new Error('401'))
      .mockResolvedValue(USER)
    const refresh = vi
      .spyOn(api, 'refreshToken')
      .mockResolvedValue({ access: 'access-2', refresh: 'refresh-2' })

    await Promise.all([useAuthStore.getState().init(), useAuthStore.getState().init()])

    expect(me).toHaveBeenCalledTimes(2) // 首次 401 + 刷新后再取一次
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(useAuthStore.getState().accessToken).toBe('access-2')
  })
})
