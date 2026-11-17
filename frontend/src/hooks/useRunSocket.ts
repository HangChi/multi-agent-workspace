/** 运行实时事件订阅（T072，US5）：/ws/runs/{id} WebSocket 客户端。
 *
 * - 连接时把 access token 作为**子协议**携带：`new WebSocket(url, ['aw.bearer', token])`
 *   —— 浏览器无法自定义握手头，早期实现把 token 塞进 `?token=`，于是它会进
 *   nginx access/error 日志。子协议只在握手头里，服务端只回显 `aw.bearer`。
 * - 按事件类型回调：status_change / step_new / step_update / log_line /
 *   cost_update / needs_input / run_completed / run_failed
 * - 心跳保活：每 25s 发 ping（nginx proxy_read_timeout 300s，空闲会被掐断）
 * - 终态事件到达后主动关闭，不再重连（Run 已结束，连接无意义）
 * - 断线自动重连（指数退避，最多 5 次）；组件卸载时关闭
 */

import { useEffect, useRef, useState } from 'react'

import { useAuthStore } from '../stores/authStore'

export interface RunSocketEvent {
  type: string
  [key: string]: unknown
}

const MAX_RETRIES = 5
const BASE_DELAY_MS = 1000
/** 心跳间隔：远小于反代 300s 空闲超时 */
const HEARTBEAT_MS = 25_000
/** 子协议名：服务端据此识别「第二个元素是 access token」，且只回显这一个名字 */
const BEARER_PROTOCOL = 'aw.bearer'

/** 终态事件：到达后 Run 已结束，无需保持连接 */
const TERMINAL_EVENTS = new Set(['run_completed', 'run_failed', 'run_cancelled'])

function wsUrl(runId: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/ws/runs/${runId}`
}

export function useRunSocket(
  runId: string | null | undefined,
  onEvent: (event: RunSocketEvent) => void,
  enabled = true
): { connected: boolean } {
  const accessToken = useAuthStore((s) => s.accessToken)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const retriesRef = useRef(0)
  const timerRef = useRef<number | null>(null)
  const onEventRef = useRef(onEvent)

  // 回调最新引用（避免 useEffect 重建连接）
  onEventRef.current = onEvent

  useEffect(() => {
    if (!runId || !accessToken || !enabled) {
      setConnected(false)
      return
    }

    let closedByUnmount = false
    /** 已收到终态事件：主动关闭后不应重连 */
    let terminal = false
    let retries = retriesRef.current
    let heartbeat: number | null = null

    const stopHeartbeat = () => {
      if (heartbeat !== null) {
        window.clearInterval(heartbeat)
        heartbeat = null
      }
    }

    const cleanup = () => {
      stopHeartbeat()
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
      if (wsRef.current) {
        wsRef.current.onclose = null
        wsRef.current.onmessage = null
        wsRef.current.onerror = null
        wsRef.current.close()
        wsRef.current = null
      }
    }

    const connect = () => {
      if (closedByUnmount || terminal) return
      const ws = new WebSocket(wsUrl(runId), [BEARER_PROTOCOL, accessToken])
      wsRef.current = ws

      ws.onopen = () => {
        retries = 0
        retriesRef.current = 0
        setConnected(true)
        // 心跳：服务端回 pong，同时刷新反代的空闲计时
        stopHeartbeat()
        heartbeat = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }))
          }
        }, HEARTBEAT_MS)
      }

      ws.onmessage = (msg) => {
        try {
          const event = JSON.parse(msg.data as string) as RunSocketEvent
          onEventRef.current(event)
          if (TERMINAL_EVENTS.has(event.type)) {
            terminal = true
            stopHeartbeat()
            ws.close(1000, 'run-terminal')
          }
        } catch {
          // 忽略无法解析的消息
        }
      }

      ws.onclose = () => {
        stopHeartbeat()
        setConnected(false)
        if (closedByUnmount || terminal) return
        if (retries < MAX_RETRIES) {
          retries += 1
          retriesRef.current = retries
          const delay = BASE_DELAY_MS * 2 ** (retries - 1)
          timerRef.current = window.setTimeout(connect, delay)
        }
      }

      ws.onerror = () => {
        stopHeartbeat()
        ws.close()
      }
    }

    connect()

    return () => {
      closedByUnmount = true
      cleanup()
    }
  }, [runId, accessToken, enabled])

  return { connected }
}
