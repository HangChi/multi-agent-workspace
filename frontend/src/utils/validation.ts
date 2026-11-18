/**
 * 跨页复用的轻量校验（避免 Login/Register 各写一份正则后悄悄漂移）。
 */

/** 邮箱：一刀切够用即可——真正以用户身份存在与否由后端裁决。 */
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function isEmail(value: string): boolean {
  return EMAIL_RE.test(value.trim())
}
