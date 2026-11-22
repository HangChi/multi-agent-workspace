/**
 * 通知渠道页（018）的契约级测试。
 *
 * 只钉两条最容易走错的：
 * 1. 事件「全不勾 = 全部」这个语义必须有文案说明（用户以为不勾就是不订阅）；
 * 2. 勾选的事件原样提交给后端（不在前端做二次加工）。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { ConfirmProvider } from '../../components/ui/ConfirmDialog'
import { ToastProvider } from '../../components/ui/Toast'

const channels = vi.hoisted(() => ({ items: [] as unknown[] }))
const createMock = vi.hoisted(() => vi.fn(async () => ({})))

vi.mock('../../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/endpoints')>()),
  fetchChannels: vi.fn(async () => channels.items),
  fetchEventTypes: vi.fn(async () => [
    { value: 'run_completed', label: '运行结束' },
    { value: 'dependency_ready', label: '前置完成、可以开工' },
  ]),
  createChannel: createMock,
  updateChannel: vi.fn(),
  deleteChannel: vi.fn(),
  testChannel: vi.fn(),
  resetChannelSecret: vi.fn(),
  fetchChannelDeliveries: vi.fn(async () => []),
}))

import { NotificationChannelsPage } from '../NotificationChannelsPage'

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

describe('NotificationChannelsPage', () => {
  it('没有通道时给出空态与入口', async () => {
    channels.items = []
    wrap(<NotificationChannelsPage />)
    expect(await screen.findByText('还没有通知渠道')).toBeInTheDocument()
  })

  it('新建时说明「全不勾 = 全部事件」，并原样提交勾选项', async () => {
    channels.items = []
    wrap(<NotificationChannelsPage />)
    await screen.findByText('还没有通知渠道')

    fireEvent.click(screen.getByRole('button', { name: '新建通道' }))
    expect(await screen.findByText(/全不勾 = 订阅全部事件/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Webhook 地址'), {
      target: { value: 'https://hooks.example.com/aw' },
    })
    fireEvent.click(screen.getByText('运行结束'))
    fireEvent.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith({
        url: 'https://hooks.example.com/aw',
        event_types: ['run_completed'],
      })
    })
  })
})
