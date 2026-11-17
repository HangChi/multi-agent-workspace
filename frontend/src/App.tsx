/** 应用入口：QueryClient + 全局 Provider（Toast/确认框）+ Router + 启动时恢复会话。 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'

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

export default function App() {
  // 启动时恢复会话
  void useAuthStore.getState().init()
  // 主题：应用当前模式并跟随系统变化
  useThemeSync()

  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ConfirmProvider>
          {/* 根级兜底：放在最外层，连 Router 自身崩了也能给出一张可交互的错误页
              （所以内部只用 <a> 不用 <Link>）。区块级兜底在 AppLayout 里。 */}
          <ErrorBoundary variant="page">
            <BrowserRouter>
              <AppRouter />
            </BrowserRouter>
          </ErrorBoundary>
        </ConfirmProvider>
      </ToastProvider>
    </QueryClientProvider>
  )
}
