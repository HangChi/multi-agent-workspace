/// <reference types="vite/client" />

/**
 * Vite 客户端环境类型声明。
 *
 * 项目 `tsconfig.json` 的 `types` 白名单里没有 `vite/client`，缺了它
 * `import.meta.env` 会被 TS 判为 `Property 'env' does not exist on ImportMeta`。
 *
 * 目前前端没有自定义 `VITE_*` 变量（API 走同源 `/api/v1`，由 nginx 反代或
 * vite dev proxy 转发）。若将来要加，记得只有 `VITE_` 前缀才会注入浏览器包，
 * 并在下面的 `ImportMetaEnv` 里补声明。
 */
