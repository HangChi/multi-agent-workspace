/** ErrorBanner 组件测试：渲染变体、关闭与重试回调。 */

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

import { ErrorBanner } from './ErrorBanner'

const apiError = {
  response: { data: { error: { code: 'FORBIDDEN', message: '无权限执行此操作' } } },
}

describe('ErrorBanner', () => {
  it('banner 变体渲染错误消息', () => {
    render(<ErrorBanner error={apiError} />)
    expect(screen.getByRole('alert')).toHaveTextContent('无权限执行此操作')
  })

  it('无错误时不渲染', () => {
    const { container } = render(<ErrorBanner error={null} />)
    expect(container.innerHTML).toBe('')
  })

  it('inline 变体输出紧凑红字', () => {
    render(<ErrorBanner error={apiError} variant="inline" />)
    const p = screen.getByRole('alert')
    expect(p).toHaveTextContent('无权限执行此操作')
    expect(p.tagName).toBe('P')
  })

  it('onDismiss 点击后调用回调', () => {
    const onDismiss = vi.fn()
    render(<ErrorBanner error={apiError} onDismiss={onDismiss} />)
    fireEvent.click(screen.getByLabelText('关闭错误提示'))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('retry 回调渲染重试按钮并可点击', () => {
    const retry = vi.fn()
    render(<ErrorBanner error={apiError} retry={retry} />)
    fireEvent.click(screen.getByText('重试'))
    expect(retry).toHaveBeenCalledTimes(1)
  })
})
