/** refreshAccessToken：token 静默续期与并发去重。
 *
 * 背景：access token 仅 30 分钟，401 直接 logout 会把用户踢回登录页。
 * refresh token 现在放在 HttpOnly cookie 里（前端读不到），所以这里断言：
 * **不带请求体**、带 withCredentials，且成功后只写回 access。
 *
 * 为什么强调「不带」：后端 body 是 `RefreshRequest | None` 且 refresh_token 必填，
 * 发 `{}` 会被校验成 422，读 cookie 的分支进不去 —— 表现为一刷新就掉线。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }))

vi.mock('axios', () => {
  const instance = Object.assign(vi.fn(), {
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  })
  return { default: { create: () => instance, post: postMock } }
})

import { __resetRefreshState, refreshAccessToken } from './client'
import { useAuthStore } from '../stores/authStore'

const NEW_TOKENS = { access: 'new-access', refresh: 'new-refresh' }

function setSession(access: string | null) {
  useAuthStore.setState({ accessToken: access, user: null, initialized: true })
}

beforeEach(() => {
  __resetRefreshState()
  postMock.mockReset()
  setSession(null)
})

describe('refreshAccessToken', () => {
  it('用 cookie 刷新：不带请求体、带 withCredentials', async () => {
    setSession('expired-access')
    postMock.mockResolvedValue({ data: { tokens: NEW_TOKENS } })

    await expect(refreshAccessToken()).resolves.toBe('new-access')

    expect(postMock).toHaveBeenCalledWith('/api/v1/auth/refresh', undefined, {
      withCredentials: true,
    })
    expect(useAuthStore.getState().accessToken).toBe('new-access')
  })

  it('刷新失败：登出且不抛错', async () => {
    setSession('expired-access')
    postMock.mockRejectedValue(new Error('401'))
    const logoutSpy = vi.spyOn(useAuthStore.getState(), 'logout')

    await expect(refreshAccessToken()).resolves.toBeNull()
    expect(logoutSpy).toHaveBeenCalled()
    expect(useAuthStore.getState().accessToken).toBeNull()
  })

  it('并发 401 只发一次刷新请求', async () => {
    setSession('expired-access')
    postMock.mockResolvedValue({ data: { tokens: NEW_TOKENS } })

    const results = await Promise.all([
      refreshAccessToken(),
      refreshAccessToken(),
      refreshAccessToken(),
    ])

    expect(results).toEqual(['new-access', 'new-access', 'new-access'])
    expect(postMock).toHaveBeenCalledTimes(1)
  })

  it('刷新结束后可再次刷新（状态被重置）', async () => {
    setSession('expired-access')
    postMock.mockResolvedValue({ data: { tokens: NEW_TOKENS } })

    await refreshAccessToken()
    await refreshAccessToken()

    expect(postMock).toHaveBeenCalledTimes(2)
  })
})
