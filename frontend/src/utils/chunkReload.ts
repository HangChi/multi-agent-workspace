/**
 * 懒加载路由的 chunk 失效处理。
 *
 * 代码分割引入了一个原本不存在的失败模式：用户停留在旧页面时我们发布了
 * 新版本，旧页面再去拉 `import()` 的 chunk，文件名哈希已变 → 404。
 * 表现是「点某个菜单就报错」，用户以为系统坏了，其实刷新就好。
 *
 * 实测（chromium，把 BoardPage chunk 拦成 404）：
 *   TypeError: Failed to fetch dynamically imported module: /assets/BoardPage-xxx.js
 * 该异常会冒泡到 ErrorBoundary —— 但落到默认文案「界面渲染时遇到了意外错误」
 * 是误导性的，用户不会想到「刷新一下」。
 *
 * 处理策略：
 * 1. 识别出这是 chunk 失效，自动刷新一次（幂等重试，用户无感）
 * 2. 若刷新后仍然失败（说明不是版本问题），不再刷死循环，而是展示
 *    「检测到新版本」的专用文案，引导手动刷新
 *
 * 标记用 sessionStorage 而非模块变量：reload 会重建整个 JS 上下文，
 * 模块变量随之丢失，只有 storage 能跨刷新保留「已重试过」这个事实。
 */

const RELOAD_FLAG_KEY = 'aw_chunk_reload_at'

/** 两次自动刷新之间的最小间隔；小于它说明上次刷新没能解决问题。 */
const RELOAD_COOLDOWN_MS = 10_000

/**
 * 判断异常是否是「动态 import 的 chunk 拉不到」。
 *
 * 各浏览器措辞不一致，实测/已知的几种：
 * - Chrome/Edge: "Failed to fetch dynamically imported module: <url>"
 * - Firefox:     "error loading dynamically imported module"
 * - Safari:      "Importing a module script failed."
 * 发版后旧 chunk 也可能返回 HTML（404 页 / index.html），导致 MIME 校验失败：
 * - "Expected a JavaScript module script but the server responded with a MIME type of ..."
 */
export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  const msg = `${error.name}: ${error.message}`
  return /dynamically imported module|Importing a module script failed|MIME type of|ChunkLoadError/i.test(
    msg,
  )
}

/**
 * 是否应该为这次 chunk 失败触发一次自动刷新。
 *
 * @param lastReloadAt 上次自动刷新的时间戳（没有则传 0）
 * @param now          当前时间戳（便于测试注入）
 */
export function shouldAttemptReload(lastReloadAt: number, now: number): boolean {
  if (!lastReloadAt) return true
  // 时钟回拨（标记在未来）也允许重试，否则会永久卡住
  if (lastReloadAt > now) return true
  return now - lastReloadAt > RELOAD_COOLDOWN_MS
}

/** 读取上次自动刷新时间戳；读不到（隐私模式等）返回 0。 */
export function readReloadFlag(): number {
  try {
    const raw = window.sessionStorage.getItem(RELOAD_FLAG_KEY)
    const n = raw ? Number(raw) : 0
    return Number.isFinite(n) ? n : 0
  } catch {
    return 0
  }
}

/** 记录本次自动刷新时间戳。 */
export function writeReloadFlag(now: number = Date.now()): void {
  try {
    window.sessionStorage.setItem(RELOAD_FLAG_KEY, String(now))
  } catch {
    /* 存不进去就退化为「只刷新一次」，下次进入会再试一次 */
  }
}

/** 加载成功后清除标记，让下次发版仍能自动刷新。 */
export function clearReloadFlag(): void {
  try {
    window.sessionStorage.removeItem(RELOAD_FLAG_KEY)
  } catch {
    /* 忽略 */
  }
}
