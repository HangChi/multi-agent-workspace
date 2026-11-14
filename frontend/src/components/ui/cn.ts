/** 极简 className 拼接（避免为几处条件样式引入 clsx 依赖）。 */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}
