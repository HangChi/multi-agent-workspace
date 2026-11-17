/** 确认弹窗的上下文与读取 hook（与 Provider 分文件，见 react-refresh 规则）。 */

import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'

export interface ConfirmOptions {
  title: string
  description?: ReactNode
  confirmText?: string
  cancelText?: string
  tone?: 'danger' | 'warning' | 'primary'
}

export type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>

export const ConfirmContext = createContext<ConfirmFn | null>(null)

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext)
  if (!ctx) {
    throw new Error('useConfirm 必须在 <ConfirmProvider> 内使用')
  }
  return ctx
}
