/**
 * 错误边界：拦住渲染期异常，避免整页白屏。
 *
 * 为什么需要：React 18 里任何一个组件在渲染阶段抛错，React 会卸载整棵
 * 组件树 —— 用户看到的是纯白页面，且控制台报错不会自动同步到任何地方，
 * 线上表现为「点了某个页面就全白，刷新才好」。
 *
 * 用法：
 *   // 根级：包住整个应用，兜住布局之外的意外
 *   <ErrorBoundary variant="page" onError={report}>
 *     <AppRouter />
 *   </ErrorBoundary>
 *
 *   // 区块级：包住路由出口，出错时导航栏仍可用
 *   <ErrorBoundary variant="section" resetKey={location.key}>
 *     <Outlet />
 *   </ErrorBoundary>
 *
 * ⚠️ 错误边界**只捕获渲染期、生命周期和构造函数中的异常**，捕获不到：
 * 事件处理器（onClick 等）、异步代码（Promise/setTimeout）、SSR、边界自身。
 * 这些路径请继续用 try/catch + ErrorBanner / Toast。
 *
 * 设计约定：
 * - 不依赖 Router / Toast / Query 等上层 Provider —— 那些本身可能就是崩溃源
 * - section 变体只用普通 <a>，不用 <Link>，保证路由层出问题时仍可跳转
 * - resetKey 变化时自动复位，否则用户切走再切回来仍是错误页
 */

import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

import {
  isChunkLoadError,
  readReloadFlag,
  shouldAttemptReload,
  writeReloadFlag,
} from '../../utils/chunkReload'
import { cn } from './cn'

interface ErrorBoundaryProps {
  children: ReactNode
  /** page=整页兜底（根级），section=区块兜底（保留外层导航） */
  variant?: 'page' | 'section'
  /** 变化即复位错误状态。通常传 location.key，让路由切换自动恢复 */
  resetKey?: unknown
  /** 上报回调（埋点 / 日志）。默认只打 console.error */
  onError?: (error: Error, info: ErrorInfo) => void
  /** 自定义标题，默认按变体取 */
  title?: string
}

interface ErrorBoundaryState {
  error: Error | null
  /** 本次错误是否为懒加载 chunk 失效（发版后旧 chunk 404）。 */
  chunkFailed: boolean
}

const DEFAULT_TITLE = {
  page: '页面出错了',
  section: '这部分内容加载失败',
} as const

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null, chunkFailed: false }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error, chunkFailed: isChunkLoadError(error) }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 先落日志：即使 onError 自己抛错，也至少留下原始堆栈
    console.error('[ErrorBoundary] 渲染异常', error, info.componentStack)
    this.props.onError?.(error, info)

    // 懒加载 chunk 失效（发版后旧文件名 404）：自动刷新一次即可拿到新版本。
    // 冷却判断防止刷新后仍失败时无限 reload —— 那会变成白屏闪烁。
    if (isChunkLoadError(error) && shouldAttemptReload(readReloadFlag(), Date.now())) {
      writeReloadFlag()
      window.location.reload()
    }
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps) {
    // 路由切换（resetKey 变化）时自动复位，否则切走再切回仍是错误页
    if (this.state.error !== null && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null, chunkFailed: false })
    }
  }

  private handleReset = () => {
    this.setState({ error: null, chunkFailed: false })
  }

  private handleReload = () => {
    window.location.reload()
  }

  render() {
    const { error, chunkFailed } = this.state
    if (!error) return this.props.children

    const { variant = 'section', title } = this.props
    // chunk 失效的文案要和「渲染炸了」区分开：用户只需刷新，不是系统坏了
    const heading = chunkFailed ? '检测到新版本' : (title ?? DEFAULT_TITLE[variant])
    const detail = import.meta.env.DEV ? this.describe(error) : null

    const body = (
      <div
        role="alert"
        className={cn(
          'flex flex-col items-center rounded-2xl border border-rose-200 bg-rose-50/60 px-5 py-8 text-center',
          'dark:border-rose-500/25 dark:bg-rose-500/10',
          variant === 'page' && 'mx-auto w-full max-w-md shadow-card'
        )}
      >
        <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-rose-100 text-rose-600 ring-1 ring-inset ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30">
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" aria-hidden="true">
            <path
              d="M12 8.5v4.2M12 16.2h.01M10.3 3.9 2.5 17.4A1.9 1.9 0 0 0 4.2 20.3h15.6a1.9 1.9 0 0 0 1.7-2.9L13.7 3.9a1.9 1.9 0 0 0-3.4 0Z"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>

        <p className="text-base font-semibold text-ink">{heading}</p>
        <p className="mt-1 max-w-sm text-sm leading-relaxed text-ink-muted">
          {chunkFailed
            ? '应用可能已经更新，当前页面引用的资源已失效。刷新一下即可继续使用。'
            : '界面渲染时遇到了意外错误，已中断本次渲染以免影响其他内容。'}
          {!chunkFailed && variant === 'section' ? '你可以切换页面，或按下面的按钮重试。' : ''}
        </p>

        {detail && (
          <pre className="mt-3 max-h-40 w-full overflow-auto rounded-lg bg-surface-inset/80 p-3 text-left text-2xs leading-relaxed text-ink-muted">
            {detail}
          </pre>
        )}

        <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
          {/* chunk 失效时重试无效（模块已经被打包成新文件名，重新 import 还是 404），
              所以把「刷新页面」提为主按钮，避免用户反复点一个注定失败的按钮。 */}
          {chunkFailed ? (
            <button
              type="button"
              onClick={this.handleReload}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-brand-600 px-3 text-sm font-medium text-white shadow-card transition-colors hover:bg-brand-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2"
            >
              刷新页面
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={this.handleReset}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-brand-600 px-3 text-sm font-medium text-white shadow-card transition-colors hover:bg-brand-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2"
              >
                重试
              </button>
              <button
                type="button"
                onClick={this.handleReload}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 text-sm font-medium text-ink-soft shadow-card transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45"
              >
                刷新页面
              </button>
            </>
          )}
          {variant === 'section' && (
            <a
              href="/"
              className="inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-medium text-ink-muted transition-colors hover:text-ink"
            >
              返回看板
            </a>
          )}
        </div>
      </div>
    )

    if (variant === 'page') {
      return (
        <div className="flex min-h-screen items-center justify-center bg-canvas px-4">{body}</div>
      )
    }
    return <div className="py-10">{body}</div>
  }

  /** 开发环境展示摘要：线上不暴露堆栈（可能含路径/内部标识）。 */
  private describe(error: Error): string {
    return `${error.name}: ${error.message}`
  }
}
