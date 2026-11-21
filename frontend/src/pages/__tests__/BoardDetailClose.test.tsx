/**
 * 看板详情弹层的关闭行为（docs/code-audit-2026-11-21.md D2）。
 *
 * 背景：详情弹层的数据源优先级是 `detail ?? inList ?? deepLinkRequest`。
 * 从通知/分享链接进入（`?request=<id>`）时 detail 恒为 null，实际渲染的是
 * inList / deepLinkRequest。此时若「关闭」只做 `setDetail(null)`，currentDetail
 * 依然取到 inList → Modal 继续渲染 → 用户点关闭画面纹丝不动，只能靠遮罩或 ESC。
 *
 * 本测试锁两件事：
 * 1. 深链打开后，footer 的「关闭」能真正关掉弹层；
 * 2. 关闭时把 URL 上的 ?request= 一并清掉（否则刷新又弹出来）。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { WorkRequest } from '../../api/endpoints'
import { ConfirmProvider } from '../../components/ui/ConfirmDialog'
import { ToastContext, type ToastApi } from '../../components/ui/useToast'
import { useAuthStore } from '../../stores/authStore'
import { BoardPage } from '../BoardPage'

vi.mock('../../api/endpoints', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/endpoints')>()
  return {
    ...actual,
    fetchRequests: vi.fn(),
    fetchRequest: vi.fn(),
    fetchAgents: vi.fn(),
    fetchGroups: vi.fn(),
    fetchProjects: vi.fn(),
    fetchUserOptions: vi.fn(),
    createRequest: vi.fn(),
  }
})

import {
  fetchAgents,
  fetchGroups,
  fetchProjects,
  fetchRequest,
  fetchRequests,
  fetchUserOptions,
} from '../../api/endpoints'

const REQUEST: WorkRequest = {
  id: 'req-deeplink',
  owner_id: 'owner-1',
  title: '深链进来的需求',
  description: null,
  expected_output: null,
  priority: 'normal',
  status: 'completed',
  attachments: [],
  created_at: '2026-11-18T00:00:00Z',
  updated_at: '2026-11-18T00:00:00Z',
} as WorkRequest

const toast: ToastApi = {
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
}

/** 记录当前 URL，供断言「关闭时清了 ?request=」。 */
let currentSearch = ''
function LocationProbe() {
  const location = useLocation()
  currentSearch = location.search
  return null
}

/** footer 的「关闭」按钮。
 *
 * Modal 右上角的 X 也是 aria-label="关闭"，两个同名。这里取**有文本内容**的那个
 * （X 是纯图标按钮，textContent 为空）——正是 D2 出问题的 footer 按钮。
 */
function clickFooterClose() {
  const buttons = screen.getAllByRole('button', { name: '关闭' })
  const footerButton = buttons.find((b) => b.textContent?.trim() === '关闭')
  if (!footerButton) throw new Error('找不到 footer 的「关闭」按钮')
  fireEvent.click(footerButton)
}

function renderBoard(initialEntries: string[]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ToastContext.Provider value={toast}>
        <ConfirmProvider>
          <MemoryRouter initialEntries={initialEntries}>
            <LocationProbe />
            <BoardPage />
          </MemoryRouter>
        </ConfirmProvider>
      </ToastContext.Provider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  currentSearch = ''
  // 已登录（看板需要 user 判断归属）
  useAuthStore.setState({
    accessToken: 'token',
    user: { id: 'owner-1', email: 'owner@example.test', role: 'member' } as never,
  })
  vi.mocked(fetchRequests).mockResolvedValue({ items: [], total: 0 } as never)
  vi.mocked(fetchRequest).mockResolvedValue(REQUEST)
  vi.mocked(fetchAgents).mockResolvedValue({ items: [], total: 0 } as never)
  vi.mocked(fetchGroups).mockResolvedValue({ items: [], total: 0 } as never)
  vi.mocked(fetchProjects).mockResolvedValue({ items: [], total: 0 } as never)
  vi.mocked(fetchUserOptions).mockResolvedValue({ items: [], total: 0 } as never)
})

describe('BoardPage 深链详情弹层', () => {
  it('深链不在列表里：弹层打开，点「关闭」能关掉并清掉 URL 参数', async () => {
    // 列表为空 → 需求只能靠详情接口拿到，正是 detail 为 null 的路径
    renderBoard(['/?request=req-deeplink'])

    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('深链进来的需求')
    expect(currentSearch).toContain('request=req-deeplink')

    clickFooterClose()

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
    })
    // URL 参数也要清掉，否则刷新会再次弹出
    expect(currentSearch).not.toContain('request=req-deeplink')
  })

  it('深链命中的需求在列表里：点「关闭」同样能关掉', async () => {
    // inList 命中的分支——这与上一条的 currentDetail 来源不同
    vi.mocked(fetchRequests).mockResolvedValue({ items: [REQUEST], total: 1 } as never)
    renderBoard(['/?request=req-deeplink'])

    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('深链进来的需求')

    clickFooterClose()

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
    })
  })
})
