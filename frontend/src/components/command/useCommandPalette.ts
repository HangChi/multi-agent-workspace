/** 命令面板开关的读取端（与 Provider 分开，避免 fast-refresh 警告）。 */

import { createContext, useContext } from 'react'

export interface CommandState {
  open: boolean
  setOpen: (open: boolean) => void
  toggle: () => void
}

export const CommandContext = createContext<CommandState | null>(null)

export function useCommandPalette(): CommandState {
  const ctx = useContext(CommandContext)
  if (!ctx) throw new Error('useCommandPalette 必须在 CommandProvider 内使用')
  return ctx
}
