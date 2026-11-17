/**
 * 主题模式（浅色 / 深色 / 跟随系统）。
 *
 * 实现要点：
 * - 只存「模式」，实际深浅由 resolveDark() 推出来，系统切换时自动跟随；
 * - 直接操作 <html class="dark">，与 Tailwind darkMode: 'class' 对齐；
 * - 首屏防闪白在 index.html 的内联脚本里做，这里只负责运行期切换。
 */

import { useEffect } from 'react'
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type ThemeMode = 'light' | 'dark' | 'system'

const THEME_STORAGE_KEY = 'aw_theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

function systemPrefersDark(): boolean {
  return window.matchMedia(DARK_QUERY).matches
}

export function resolveDark(mode: ThemeMode): boolean {
  return mode === 'dark' || (mode === 'system' && systemPrefersDark())
}

/** 把主题落到 DOM 上（同时影响原生控件配色） */
export function applyTheme(mode: ThemeMode): void {
  const dark = resolveDark(mode)
  const root = document.documentElement
  root.classList.toggle('dark', dark)
  root.style.colorScheme = dark ? 'dark' : 'light'
}

interface ThemeState {
  mode: ThemeMode
  setMode: (mode: ThemeMode) => void
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      mode: 'system',
      setMode: (mode) => {
        applyTheme(mode)
        set({ mode })
      },
    }),
    {
      name: THEME_STORAGE_KEY,
      partialize: (s) => ({ mode: s.mode }),
      // 水合后再落一次 DOM，避免首屏脚本被清空 class 的极端情况
      onRehydrateStorage: () => (state) => {
        if (state) applyTheme(state.mode)
      },
    }
  )
)

/** 挂在 App 顶层：应用主题 + 跟随系统变化 */
export function useThemeSync(): void {
  const mode = useThemeStore((s) => s.mode)

  useEffect(() => {
    applyTheme(mode)
    if (mode !== 'system') return
    const mq = window.matchMedia(DARK_QUERY)
    const onChange = () => applyTheme('system')
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [mode])
}
