/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 本地 dev 的 API 入口（2026-11-22 修正）。
//
// 此前写死 http://127.0.0.1:8000，但 compose 里 api 服务只有 `expose: 8000`、
// **不发布到宿主机**（见 deploy/docker-compose.yml 该服务末尾那行注释），
// 于是宿主机 8000 根本没人监听，dev 下所有 /api 请求都是 500。
//
// 宿主机唯一的后端入口是 frontend(nginx) 的 8080：nginx 再把 /api 与 /ws
// 反代到 api:8000。所以默认目标改成 8080，dev 与生产走同一条链路，不用再
// 单独维护一份「代理垫片」。
//
// 单独用 uvicorn 在宿主机起后端（不经 compose）时，用环境变量覆盖：
//   AW_DEV_API=http://127.0.0.1:8000 npm run dev
const apiTarget = process.env.AW_DEV_API ?? 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
      // WebSocket 必须单独代理。useRunSocket 拼的是
      // ws://<当前 host>/ws/runs/{id}，dev 下 host 是 5173；
      // 原来只配了 '/api'（那条的 ws:true 只对 /api 路径生效），
      // /ws 落到 SPA fallback 返回 index.html，握手必然失败——
      // 表现为运行详情页收不到实时步骤，只能靠轮询兜底。
      '/ws': {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
