/**
 * 调度页前端测试（T049）。
 *
 * 这里只钉四条**契约级**行为，不写「渲染有没有崩」这类空断言：
 *
 * 1. 后端把 cron 错误定位在 `detail.field = "cron_expr"` 上，前端必须把
 *    它挂到 cron 输入框（aria-describedby 指向的那条消息），而不是只弹一条
 *    谁也不知道该改哪的横幅。契约 §1 第 3/4 步的 422 若在界面上退化成
 *    通用提示，那套字段定位就等于白做。
 * 2. 「日期 + 星期」取交集这件事只写在 spec 里没人看得见，必须落在用户
 *    填 cron 的那个位置 —— 这是 FR-004 唯一能被用户感知的落点。
 * 3. viewer 在后端对所有 schedules 端点都是 403，前端就不该给他任何写入口。
 * 4. 四种触发结果的措辞必须彼此可辨（`missed` 与 `skipped_busy` 混在一起
 *    显示就等于没有历史），且 `fired` 能跳到它建出的那条需求。
 */

import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ComponentProps } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { fetchRequests, fetchScheduleTriggers } from '../api/endpoints'
import { ScheduleForm } from '../components/schedule/ScheduleForm'
import { TriggerHistory } from '../components/schedule/TriggerHistory'
import { ConfirmProvider } from '../components/ui/ConfirmDialog'
import { ToastProvider } from '../components/ui/Toast'
import { BoardPage } from '../pages/BoardPage'
import { SchedulesPage } from '../pages/SchedulesPage'

// vi.mock 的工厂在模块求值时就跑，此时普通 const/let 还在 TDZ 里。
// 用 vi.hoisted 把可变角色提到 mock 之前，才能让每个用例改自己的身份。
const auth = vi.hoisted(() => ({ role: 'member' as 'admin' | 'member' | 'viewer' }))

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'u1', email: 'ops@example.test', role: auth.role, status: 'active' },
    accessToken: 'token',
    initialized: true,
    isAuthenticated: true,
    isAdmin: auth.role === 'admin',
    isMember: auth.role !== 'viewer',
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
  }),
}))

vi.mock('../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/endpoints')>()),
  fetchSchedules: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  fetchScheduleTriggers: vi.fn(),
  fetchRequests: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  fetchRequest: vi.fn(),
  fetchAgents: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  fetchGroups: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  previewCron: vi.fn().mockResolvedValue({ next_runs: [], error: null }),
}))

const AGENTS = [{ id: 'agent-1', name: '晨报 Agent', status: 'active' as const }]
const GROUPS = [{ id: 'group-1', name: '发布组', status: 'active' as const }]

/**
 * 统一渲染壳：QueryClient + Toast/Confirm 两个 Provider + Router。
 *
 * Router 是必需的——页面里的返回链接与历史抽屉里的「查看需求」都是
 * `<Link>`，缺了 Router 上下文会直接抛错而不是渲染出无效链接。
 */
function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <ConfirmProvider>{ui}</ConfirmProvider>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

function renderForm(props: Partial<ComponentProps<typeof ScheduleForm>> = {}) {
  const onSubmit = vi.fn()
  wrap(
    <ScheduleForm
      agents={AGENTS}
      groups={GROUPS}
      onSubmit={onSubmit}
      onCancel={vi.fn()}
      {...props}
    />
  )
  return { onSubmit }
}

describe('ScheduleForm · cron 错误定位', () => {
  it('把后端的 cron 错误挂到 cron 输入框上', () => {
    renderForm({
      error: '周期表达式不合法：分钟必须在 0-59 之间',
      errorField: 'cron_expr',
    })

    const input = screen.getByLabelText(/周期表达式/)
    expect(input).toHaveAttribute('aria-invalid', 'true')

    // 只断言「页面上出现了这段文字」是不够的：通用横幅也能满足。
    // 必须顺着 aria-describedby 走，证明这条消息确实从属于该字段。
    const describedBy = input.getAttribute('aria-describedby')
    expect(describedBy, 'cron 输入框必须用 aria-describedby 关联错误消息').toBeTruthy()
    expect(document.getElementById(describedBy!)?.textContent).toContain(
      '周期表达式不合法：分钟必须在 0-59 之间'
    )
  })

  it('错误属于别的字段时不污染 cron 输入框', () => {
    renderForm({ error: '目标不可用', errorField: 'target' })

    const input = screen.getByLabelText(/周期表达式/)
    expect(input).not.toHaveAttribute('aria-invalid', 'true')
  })
})

describe('ScheduleForm · 交集语义提示', () => {
  it('在填 cron 的地方写明日期与星期取交集', () => {
    renderForm()

    // 文案不锁死整句，但「交集」与「两者都满足」必须出现——
    // 少了任何一半，用户仍会以为是「1 号或周一」的并集。
    const hint = screen.getByText(/交集/)
    expect(hint.textContent).toMatch(/两者都满足/)
  })
})

describe('SchedulesPage · viewer 无写入口', () => {
  it('viewer 看不到新建规则入口', async () => {
    auth.role = 'viewer'
    try {
      wrap(<SchedulesPage />)
      await waitFor(() => expect(screen.getByText(/无访问权限|403/)).toBeInTheDocument())
      expect(screen.queryByRole('button', { name: /新建规则/ })).not.toBeInTheDocument()
    } finally {
      auth.role = 'member'
    }
  })

  it('member 能看到新建规则入口', async () => {
    auth.role = 'member'
    wrap(<SchedulesPage />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /新建规则/ })).toBeInTheDocument()
    )
  })
})

describe('TriggerHistory · 四种结果与跳转', () => {
  const BASE = {
    schedule_id: 's1',
    missed_count: 0,
    window_start: null,
    window_end: null,
    run_id: null,
  }

  it('四种结果各有可辨的文案，missed 带次数，fired 能跳到需求', async () => {
    vi.mocked(fetchScheduleTriggers).mockResolvedValue({
      items: [
        {
          ...BASE,
          id: 't1',
          planned_at: '2026-11-17T01:00:00Z',
          fired_at: '2026-11-17T01:00:03Z',
          result: 'fired',
          reason: null,
          request_id: 'req-1',
          run_id: 'run-1',
        },
        {
          ...BASE,
          id: 't2',
          planned_at: '2026-11-17T00:00:00Z',
          fired_at: null,
          result: 'missed',
          reason: '服务未运行，错过 25 次',
          missed_count: 25,
          window_start: '2026-11-16T22:00:00Z',
          window_end: '2026-11-17T00:00:00Z',
          request_id: null,
        },
        {
          ...BASE,
          id: 't3',
          planned_at: '2026-11-16T23:00:00Z',
          fired_at: '2026-11-16T23:00:01Z',
          result: 'skipped_busy',
          reason: '上次运行尚未结束',
          request_id: null,
        },
        {
          ...BASE,
          id: 't4',
          planned_at: '2026-11-16T22:00:00Z',
          fired_at: '2026-11-16T22:00:02Z',
          result: 'failed',
          reason: '目标已归档',
          request_id: null,
        },
      ],
      total: 4,
      counts: { fired: 1, missed: 1, skipped_busy: 1, failed: 1 },
    })

    wrap(<TriggerHistory open onClose={vi.fn()} scheduleId="s1" scheduleName="每日晨报" />)

    /**
     * ⚠️ 不能用 `getByText('已触发')` 做等待条件：筛选栏的四个按钮
     * 用的是同一套文案，那个断言会在数据到达之前就通过 —— 测试变成
     * 「按钮渲染了」而非「历史项渲染了」。改为等列表项出现。
     */
    const rows = await screen.findAllByRole('listitem')
    expect(rows).toHaveLength(4)
    const rowText = rows.map((r) => r.textContent ?? '').join('\n')

    // 四条 label 互不相同——混用就等于用户分不清「服务没跑」和「上次没跑完」
    for (const label of ['已触发', '已错过', '已跳过', '执行失败']) {
      expect(rowText).toContain(label)
    }

    // missed 的次数必须直接可见。契约原文的 reason 是「服务未运行，错过 25 次」，
    // 结构化提示是「合并 25 次」——同一事实的两种表达，一个给扫读一个给追溯。
    expect(rowText).toMatch(/25 次/)

    // 合并窗口必须可见：只给次数不告诉「从几点到几点」，用户无法判断影响范围
    expect(rowText).toMatch(/11\/16 22:00|11\/17 06:00/)

    // 可读原因按契约原文展示
    expect(rowText).toContain('目标已归档')

    // 只有 fired 带 request_id 时才有跳转，且指向需求详情
    const jump = screen.getByRole('link', { name: /查看需求/ })
    expect(jump).toHaveAttribute('href', '/?request=req-1')
  })

  it('没有需求可跳时不渲染跳转链接', async () => {
    vi.mocked(fetchScheduleTriggers).mockResolvedValue({
      items: [
        {
          ...BASE,
          id: 't9',
          planned_at: '2026-11-16T22:00:00Z',
          fired_at: '2026-11-16T22:00:02Z',
          result: 'failed',
          reason: '目标已删除',
          request_id: null,
        },
      ],
      total: 1,
      counts: { fired: 0, missed: 0, skipped_busy: 0, failed: 1 },
    })

    wrap(<TriggerHistory open onClose={vi.fn()} scheduleId="s1" scheduleName="每日晨报" />)

    const rows2 = await screen.findAllByRole('listitem')
    expect(rows2[0].textContent).toContain('执行失败')
    expect(rows2[0].textContent).toContain('目标已删除')
    expect(screen.queryByRole('link', { name: /查看需求/ })).not.toBeInTheDocument()
  })
})

describe('BoardPage · 深链打开需求', () => {
  /**
   * 「查看需求」跳的是 `/?request=<id>`。若看板不消费这个参数，
   * 那个链接就是死链——用户从历史抽屉点进来只会看到一块普通看板，
   * 需求既没被选中也没有任何提示。这条测试专门守这个闭环的最后一环。
   */
  it('按 ?request= 打开对应需求的详情', async () => {
    vi.mocked(fetchRequests).mockResolvedValue({ items: [], total: 0 })
    vi.mocked(fetchRequests).mockResolvedValue({
      items: [
        {
          id: 'req-1',
          owner_id: 'u1',
          title: '每日进度汇总',
          description: '汇总昨日完成与阻塞',
          expected_output: null,
          priority: 'normal',
          status: 'running',
          attachments: [],
          source_schedule_id: 's1',
          source_schedule_name: '每日晨报',
          created_at: '2026-11-17T01:00:00Z',
          updated_at: '2026-11-17T01:00:00Z',
        },
      ],
      total: 1,
    })

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/?request=req-1']}>
          <ToastProvider>
            <ConfirmProvider>
              <BoardPage />
            </ConfirmProvider>
          </ToastProvider>
        </MemoryRouter>
      </QueryClientProvider>
    )

    // 标题在卡片与弹层里都会出现，这里断言「来源」区块存在——
    // 它是详情弹层独有的，且正好验证 T054 的徽标确实渲染了
    await waitFor(() => expect(screen.getByText('来源')).toBeInTheDocument())
    expect(screen.getByText(/每日晨报/)).toBeInTheDocument()
  })
})
