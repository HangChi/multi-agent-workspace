/** 命令面板 Provider：持有开关状态并监听 ⌘K / Ctrl+K。 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { CommandContext } from './useCommandPalette'
import type { CommandState } from './useCommandPalette'

export function CommandProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)

  const toggle = useCallback(() => setOpen((v) => !v), [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        toggle()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggle])

  const value = useMemo<CommandState>(() => ({ open, setOpen, toggle }), [open, toggle])
  return <CommandContext.Provider value={value}>{children}</CommandContext.Provider>
}
