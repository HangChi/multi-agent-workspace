/** 命令面板 Provider：持有开关状态并监听 ⌘K / Ctrl+K。 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { CommandContext } from './useCommandPalette'
import type { CommandState } from './useCommandPalette'

/** 页面上是否有打开中的模态弹层（Modal / 命令面板自身）。 */
function hasOpenDialog(): boolean {
  return document.querySelector('[role="dialog"][aria-modal="true"]') !== null
}

export function CommandProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)

  const toggle = useCallback(() => setOpen((v) => !v), [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === 'k' && (e.metaKey || e.ctrlKey)) {
        // 弹层打开中不唤起：会叠在确认框/表单弹窗上面，两层逃逸都受影响
        if (hasOpenDialog()) return
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
