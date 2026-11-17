/** ErrorBoundary 测试：捕获渲染异常、复位、变体差异与上报回调。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

import { ErrorBoundary } from './ErrorBoundary'

/** 受控炸弹：boom=true 时在渲染期抛错。 */
function Bomb({ boom }: { boom: boolean }) {
  if (boom) throw new Error('渲染炸了')
  return <p>正常内容</p>
}

describe('ErrorBoundary', () => {
  it('子组件正常渲染时不接管', () => {
    render(
      <ErrorBoundary>
        <Bomb boom={false} />
      </ErrorBoundary>
    )
    expect(screen.getByText('正常内容')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('捕获渲染期异常并展示兜底界面', () => {
    // React 会把捕获到的错误打到 console.error，测试里静音避免污染输出
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      render(
        <ErrorBoundary>
          <Bomb boom />
        </ErrorBoundary>
      )
      const alert = screen.getByRole('alert')
      expect(alert).toHaveTextContent('这部分内容加载失败')
      expect(screen.queryByText('正常内容')).toBeNull()
    } finally {
      spy.mockRestore()
    }
  })

  it('section 变体保留「返回看板」入口，page 变体不提供', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      const { unmount } = render(
        <ErrorBoundary variant="section">
          <Bomb boom />
        </ErrorBoundary>
      )
      expect(screen.getByText('返回看板')).toBeInTheDocument()
      unmount()

      render(
        <ErrorBoundary variant="page">
          <Bomb boom />
        </ErrorBoundary>
      )
      expect(screen.getByText('页面出错了')).toBeInTheDocument()
      expect(screen.queryByText('返回看板')).toBeNull()
    } finally {
      spy.mockRestore()
    }
  })

  it('onError 收到原始异常', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const onError = vi.fn()
    try {
      render(
        <ErrorBoundary onError={onError}>
          <Bomb boom />
        </ErrorBoundary>
      )
      expect(onError).toHaveBeenCalledTimes(1)
      expect(onError.mock.calls[0][0]).toBeInstanceOf(Error)
      expect((onError.mock.calls[0][0] as Error).message).toBe('渲染炸了')
    } finally {
      spy.mockRestore()
    }
  })

  it('点「重试」后若子组件不再抛错则恢复正常', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      // 用一个可变的外部开关模拟「数据修好了」
      let boom = true
      function Flaky() {
        if (boom) throw new Error('临时故障')
        return <p>已恢复</p>
      }
      render(
        <ErrorBoundary>
          <Flaky />
        </ErrorBoundary>
      )
      expect(screen.getByRole('alert')).toBeInTheDocument()

      boom = false
      fireEvent.click(screen.getByText('重试'))
      expect(screen.getByText('已恢复')).toBeInTheDocument()
    } finally {
      spy.mockRestore()
    }
  })

  it('resetKey 变化时自动复位', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      const { rerender } = render(
        <ErrorBoundary resetKey="a">
          <Bomb boom />
        </ErrorBoundary>
      )
      expect(screen.getByRole('alert')).toBeInTheDocument()

      // resetKey 变了，且子组件这次不再抛错 → 应回到正常内容
      rerender(
        <ErrorBoundary resetKey="b">
          <Bomb boom={false} />
        </ErrorBoundary>
      )
      expect(screen.queryByRole('alert')).toBeNull()
      expect(screen.getByText('正常内容')).toBeInTheDocument()
    } finally {
      spy.mockRestore()
    }
  })

  describe('懒加载 chunk 失效', () => {
    /** 模拟发版后旧 chunk 404 的异常（真实浏览器措辞）。 */
    function ChunkBomb(): React.ReactNode {
      throw new TypeError(
        'Failed to fetch dynamically imported module: http://x/assets/BoardPage-abc.js'
      )
    }

    beforeEach(() => {
      window.sessionStorage.clear()
    })

    it('展示「检测到新版本」而不是通用渲染错误文案', () => {
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
      // 预置刷新标记，避免真的触发 location.reload（jsdom 不支持）
      window.sessionStorage.setItem('aw_chunk_reload_at', String(Date.now()))
      try {
        render(
          <ErrorBoundary variant="section">
            <ChunkBomb />
          </ErrorBoundary>
        )
        const alert = screen.getByRole('alert')
        expect(alert).toHaveTextContent('检测到新版本')
        expect(alert).toHaveTextContent('刷新一下即可继续使用')
        // 不应出现误导性的通用文案
        expect(alert).not.toHaveTextContent('界面渲染时遇到了意外错误')
      } finally {
        spy.mockRestore()
      }
    })

    it('chunk 失效时不出现注定失败的「重试」按钮', () => {
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
      window.sessionStorage.setItem('aw_chunk_reload_at', String(Date.now()))
      try {
        render(
          <ErrorBoundary variant="section">
            <ChunkBomb />
          </ErrorBoundary>
        )
        // 模块已打包成新文件名，重新 import 仍是 404 → 重试按钮无意义
        expect(screen.queryByText('重试')).toBeNull()
        expect(screen.getByText('刷新页面')).toBeInTheDocument()
      } finally {
        spy.mockRestore()
      }
    })

    it('冷却期内不再自动刷新（防死循环）', () => {
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const reload = vi.fn()
      const original = window.location.reload
      // jsdom 的 location.reload 不可写，用 defineProperty 覆盖
      Object.defineProperty(window, 'location', {
        configurable: true,
        value: { ...window.location, reload },
      })
      // 刚刷新过 —— 说明上一次刷新没解决问题，这次不能再刷
      window.sessionStorage.setItem('aw_chunk_reload_at', String(Date.now()))
      try {
        render(
          <ErrorBoundary>
            <ChunkBomb />
          </ErrorBoundary>
        )
        expect(reload).not.toHaveBeenCalled()
      } finally {
        Object.defineProperty(window, 'location', {
          configurable: true,
          value: { ...window.location, reload: original },
        })
        window.sessionStorage.clear()
        spy.mockRestore()
      }
    })
  })
})
