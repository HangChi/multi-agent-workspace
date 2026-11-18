/**
 * 平台差异的唯一来源：Mac 用 ⌘，Windows/Linux 用 Ctrl。
 * 命令面板的快捷键提示（Topbar / CommandPalette footer）共用这里，
 * 避免一处写 ⌘ 一处写 Ctrl 的漂移。
 */

const IS_MAC =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '')

/** 展示用：Mac → "⌘"；其他 → "Ctrl"（后跟空格由调用方决定）。 */
export const MOD_KEY_LABEL = IS_MAC ? '⌘' : 'Ctrl'

/** 快捷键组合提示，如 "⌘K" / "Ctrl K"。 */
export function modCombo(key: string): string {
  return IS_MAC ? `${MOD_KEY_LABEL}${key}` : `${MOD_KEY_LABEL} ${key}`
}
