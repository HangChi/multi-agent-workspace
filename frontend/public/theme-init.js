/**
 * 首屏防闪白：在样式与主脚本加载前就把 .dark 打到 <html> 上。
 *
 * 为什么是独立文件而不是 index.html 里的内联 <script>：
 * 内联脚本会逼 CSP 放开 `script-src 'unsafe-inline'`，那样 CSP 对 XSS 就基本
 * 失去意义（token 存在 localStorage，XSS 等于长期账号接管）。外链后可以用
 * 最严格的 `script-src 'self'`。
 *
 * 代价：这是 <head> 里的阻塞脚本，会多一次同源请求（无网络往返抖动时通常
 * 命中缓存/keep-alive，影响远小于闪白）。
 *
 * 读取的是 zustand persist 的结构 { state: { mode } }，与 themeStore 保持一致。
 */
(function () {
  try {
    var raw = localStorage.getItem('aw_theme')
    var mode = raw ? JSON.parse(raw).state.mode : 'system'
    var dark =
      mode === 'dark' ||
      ((mode === 'system' || !mode) &&
        window.matchMedia('(prefers-color-scheme: dark)').matches)
    if (dark) document.documentElement.classList.add('dark')
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  } catch (e) {
    /* 隐私模式等场景读不到 localStorage，忽略即可 */
  }
})()
