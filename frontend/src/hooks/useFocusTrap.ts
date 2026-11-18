/**
 * 弹层焦点管理（从 Modal 提取，命令面板/未来自绘弹层复用）。
 *
 * 提供四件事，均为 WCAG 2.1 SC 2.1.2（键盘焦点不泄漏）的要求：
 * 1. Tab / Shift+Tab 在面板内循环；
 * 2. 打开时焦点进入面板，关闭时归还触发元素；
 * 3. 背景滚动锁定，并补偿滚动条宽度（Windows 桌面端打开弹窗时
 *    背景内容不会横向跳 ~10px）；
 * 4. 弹层栈：多处弹层叠放（如确认框压在弹窗上）时，只有栈顶响应 Esc，
 *    且 Tab 陷阱只作用于栈顶——避免一次 Esc 关两层、焦点被下层抢回。
 *
 * 面板需要 tabIndex={-1} 才能接收初始焦点。
 */

import { useEffect } from 'react'
import type { RefObject } from 'react'

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** 后进先出的弹层栈：每个激活的 useFocusTrap 各占一格。 */
const stack: symbol[] = []

export function useFocusTrap(
  active: boolean,
  onClose: () => void,
  panelRef: RefObject<HTMLElement>
) {
  useEffect(() => {
    if (!active) return

    const entry = Symbol('layer')
    stack.push(entry)
    const isTop = () => stack[stack.length - 1] === entry

    /** 打开前的焦点持有者：关闭时还回去，避免下次 Tab 从文档开头开始 */
    const previouslyFocused = document.activeElement as HTMLElement | null

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        // 只让栈顶响应：确认框压在弹窗上时，Esc 只关确认框
        if (!isTop()) return
        onClose()
        return
      }
      if (e.key !== 'Tab' || !isTop()) return
      const panel = panelRef.current
      if (!panel) return
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el.getClientRects().length > 0
      )
      if (focusable.length === 0) {
        e.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      const inside = panel.contains(active)
      if (!e.shiftKey && (!inside || active === last)) {
        e.preventDefault()
        first.focus()
      } else if (e.shiftKey && (!inside || active === first || active === panel)) {
        e.preventDefault()
        last.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)

    // 滚动锁定 + 滚动条宽度补偿
    const prevOverflow = document.body.style.overflow
    const prevPadding = document.body.style.paddingRight
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    document.body.style.overflow = 'hidden'
    if (scrollbarWidth > 0) document.body.style.paddingRight = `${scrollbarWidth}px`

    // 焦点进入面板，避免读屏停留在背后的页面；延时一帧等挂载稳定
    const timer = window.setTimeout(() => panelRef.current?.focus(), 0)

    return () => {
      const i = stack.indexOf(entry)
      if (i >= 0) stack.splice(i, 1)
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = prevOverflow
      document.body.style.paddingRight = prevPadding
      window.clearTimeout(timer)
      // 焦点归还：仅当触发元素仍在文档中才还，避免抢走用户已主动转移的焦点
      if (previouslyFocused?.isConnected) previouslyFocused.focus()
    }
  }, [active, onClose, panelRef])
}
