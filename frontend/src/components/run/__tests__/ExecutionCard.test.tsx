import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import { ExecutionCard } from '../ExecutionCard'

vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn() },
}))

describe('ExecutionCard', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows resource and failure badges, then loads artifacts and egress details', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        id: 'exec-1', run_id: 'run-1', step_seq: 2, language: 'python', status: 'failed',
        error_category: 'timeout', error_message: '执行超时，请减少任务规模。', exit_code: null,
        code: 'print(1)', code_sha256: 'sha', stdout: '', stderr: '', stdout_truncated: false,
        stdout_full_bytes: 0, duration_ms: 1200, cpu_ms: 400, mem_peak_bytes: 1048576,
        image: 'sandbox:latest', limits: { timeout_s: 1 },
        artifacts: [{ id: 'art-1', filename: 'result.csv', size_bytes: 2048 }],
        created_at: '2026-01-01T00:00:00Z', finished_at: null,
      },
    }).mockResolvedValueOnce({
      data: { items: [{ target_ip: '203.0.113.4', target_domain: null, port: 443, action: 'deny', bytes_out: 0, occurred_at: '2026-01-01T00:00:00Z' }] },
    })

    render(<ExecutionCard runId="run-1" execution={{ id: 'exec-1', step_seq: 2, language: 'python', status: 'failed', exit_code: null, duration_ms: 1200, stdout_truncated: false, artifacts: 1 }} />)

    fireEvent.click(screen.getByRole('button', { name: '查看代码与输出' }))
    await waitFor(() => expect(screen.getByText('超时')).toBeInTheDocument())
    expect(screen.getByText('超时')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('result.csv')).toBeInTheDocument())
    expect(screen.getByText('访问记录')).toBeInTheDocument()
    expect(screen.getByText(/203\.0\.113\.4/)).toBeInTheDocument()
    expect(screen.queryByRole('status', { name: '制品截断提示' })).not.toBeInTheDocument()
  })

  it.each([
    ['files', '制品数量达到上限'],
    ['file_size', '单个制品大小超过上限'],
    ['total_size', '制品总大小达到上限'],
    ['future_limit', '制品回收达到限制'],
  ])('shows artifact truncation separately from stdout: %s', async (reason, label) => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        id: 'exec-1', run_id: 'run-1', step_seq: 1, language: 'python', status: 'ok',
        exit_code: 0, code: 'print(1)', stdout: 'ok', stderr: '', stdout_truncated: false,
        duration_ms: 200, cpu_ms: null, mem_peak_bytes: null, image: 'sandbox', limits: {},
        artifacts: [], artifact_truncated: { reason, message: 'collector limit reached' },
      },
    }).mockResolvedValueOnce({ data: { items: [] } })
    render(<ExecutionCard runId="run-1" execution={{ id: 'exec-1', step_seq: 1, language: 'python', status: 'ok', exit_code: 0, duration_ms: 200, stdout_truncated: false, artifacts: 0 }} />)

    fireEvent.click(screen.getByRole('button', { name: '查看代码与输出' }))
    const warning = await screen.findByRole('status', { name: '制品截断提示' })
    expect(warning).toHaveTextContent('制品已截断')
    expect(warning).toHaveTextContent(label)
    expect(warning).toHaveTextContent('collector limit reached')
    expect(warning).toHaveTextContent('部分文件未回收')
    expect(screen.queryByText('输出已截断')).not.toBeInTheDocument()
  })

  it('refreshes a running execution detail', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { id: 'exec-1', run_id: 'run-1', step_seq: 1, language: 'python', status: 'running', error_category: null, error_message: null, exit_code: null, code: 'print(1)', code_sha256: 'sha', stdout: 'ok', stderr: '', stdout_truncated: false, stdout_full_bytes: 2, duration_ms: 200, cpu_ms: null, mem_peak_bytes: null, image: 'sandbox', limits: {}, artifacts: [], artifact_truncated: null, created_at: '2026-01-01T00:00:00Z', finished_at: null } }).mockResolvedValueOnce({ data: { items: [] } })
    render(<ExecutionCard runId="run-1" execution={{ id: 'exec-1', step_seq: 1, language: 'python', status: 'running', exit_code: null, duration_ms: 0, stdout_truncated: false, artifacts: 0 }} />)
    fireEvent.click(screen.getByRole('button', { name: '刷新执行' }))
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledTimes(2))
    await screen.findByText('ok')
    expect(screen.queryByRole('status', { name: '制品截断提示' })).not.toBeInTheDocument()
  })
})
