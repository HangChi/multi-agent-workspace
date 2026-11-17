/** Toast 的上下文与读取 hook。
 *
 * 为什么单独一个文件：`react-refresh/only-export-components` 要求「组件文件只导出
 * 组件」。Provider 与 hook 混在一个文件里会让热更新退化成整页刷新，也让
 * 「这个文件到底导出什么」变得含糊。
 */

import { createContext, useContext } from 'react'

export type ToastTone = 'success' | 'error' | 'info'

export interface ToastApi {
  success: (title: string, description?: string) => void
  error: (title: string, description?: string) => void
  info: (title: string, description?: string) => void
}

export const ToastContext = createContext<ToastApi | null>(null)

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    throw new Error('useToast 必须在 <ToastProvider> 内使用')
  }
  return ctx
}
