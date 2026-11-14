/** refreshAccessToken 单元测试：token 静默续期与并发去重。
 *
 * 背景：access token 仅 30 分钟，此前 401 直接 logout → 用户被踢回登录页、
 * 表单数据丢失（有 refresh token 却只在应用启动时用过一次）。
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

function setSession(access: string | null, refresh: string | null) {
  useAuthStore.setState({
    accessToken: access,
    refreshToken: refresh,
    user: null,
    initialized: true,
  })
}

beforeEach(() => {
  __resetRefreshState()
  postMock.mockReset()
  setSession(null, null)
})

describe('refreshAccessToken', () => {
  it('无 refresh token 时返回 null 并清空会话', async () => {
    setSession('old-access', null)
    await expect(refreshAccessToken()).resolves.toBeNull()
    expect(useAuthStore.getState().accessToken).toBeNull()
  })

  it('刷新成功：返回新 access 并写回 store', async () => {
    setSession('expired-access', 'valid-refresh')
    postMock.mockResolvedValue({ data: { tokens: NEW_TOKENS } })

    await expect(refreshAccessToken()).resolves.toBe('new-access')
    expect(postMock).toHaveBeenCalledWith('/api/v1/auth/refresh', {
      refresh_token: 'valid-refresh',
    })
    expect(useAuthStore.getState().accessToken).toBe('new-access')
    expect(useAuthStore.getState().refreshToken).toBe('new-refresh')
  })

  it('刷新失败（refresh 也过期）：登出，不再抛错', async () => {
    setSession('expired-access', 'stale-refresh')
    postMock.mockRejectedValue(new Error('401'))

    await expect(refreshAccessToken()).resolves.toBeNull()
    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(useAuthStore.getState().refreshToken).toBeNull()
  })

  it('并发调用只发一次刷新请求（刷新风暴防护）', async () => {
    setSession('expired-access', 'valid-refresh')
    let resolvePost: (v: unknown) => void = () => {}
    postMock.mockReturnValue(
      new Promise((resolve) => {
        resolvePost = resolve
      }),
    )

    const results = Promise.all([
      refreshAccessToken(),
      refreshAccessToken(),
      refreshAccessToken(),
    ])
    expect(postMock).toHaveBeenCalledTimes(1)

    resolvePost({ data: { tokens: NEW_TOKENS } })
    await expect(results).resolves.toEqual(['new-access', 'new-access', 'new-access'])
    expect(postMock).toHaveBeenCalledTimes(1)
  })

  it('刷新完成后可再次刷新（状态已重置）', async () => {
    setSession('expired-access', 'valid-refresh')
    postMock.mockResolvedValue({ data: { tokens: NEW_TOKENS } })

    await refreshAccessToken()
    await refreshAccessToken()
    expect(postMock).toHaveBeenCalledTimes(2)
  })
})
