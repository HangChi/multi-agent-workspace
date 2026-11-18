/** 应用入口：QueryClient + 全局 Provider（Toast/确认框）+ Router + 启动时恢复会话。 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEffect } from 'react'
import { BrowserRouter } from 'react-router-dom'

import { CommandPalette } from './components/command/CommandPalette'
import { CommandProvider } from './components/command/CommandProvider'
import { useCommandPalette } from './components/command/useCommandPalette'
import { ConfirmProvider } from './components/ui/ConfirmDialog'
import { ErrorBoundary } from './components/ui/ErrorBoundary'
import { ToastProvider } from './components/ui/Toast'
import { AppRouter } from './router'
import { useAuthStore } from './stores/authStore'
import { useThemeSync } from './stores/themeStore'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
})

/** 命令面板挂在 Provider 内层：它需要读同一个 open 状态 */
function CommandLayer() {
  const { open, setOpen } = useCommandPalette()
  return <CommandPalette open={open} onClose={() => setOpen(false)} />
}

export default function App() {
  // 启动时恢复会话。必须放在 effect 里：写在 render body 里会每次渲染都执行，
  // 而 init 内部要打 /auth/me（401 后还要消耗 refresh token）。
  useEffect(() => {
    void useAuthStore.getState().init()
  }, [])
  // 主题：应用当前模式并跟随系统变化
  useThemeSync()

  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ConfirmProvider>
          {/* 根级兜底：放在最外层，连 Router 自身崩了也能给出一张可交互的错误页
              （所以内部只用 <a> 不用 <Link>）。区块级兜底在 AppLayout 里。 */}
          <ErrorBoundary variant="page">
            <CommandProvider>
              <BrowserRouter>
                <AppRouter />
                {/* 命令面板要读 Provider 的开关、又要用 Router 的 navigate，
                    所以放在两者之内、Routes 之外 */}
                <CommandLayer />
              </BrowserRouter>
            </CommandProvider>
          </ErrorBoundary>
        </ConfirmProvider>
      </ToastProvider>
    </QueryClientProvider>
  )
}
