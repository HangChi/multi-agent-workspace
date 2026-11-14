/** 运行实时事件订阅（T072，US5）：/ws/runs/{id} WebSocket 客户端。
 *
 * - 连接时带上 access token（?token=），服务端鉴权
 * - 按事件类型回调：status_change / step_new / step_update / log_line /
 *   cost_update / needs_input / run_completed / run_failed
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

function wsUrl(runId: string, token: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const base = `${proto}://${window.location.host}`
  return `${base}/ws/runs/${runId}?token=${encodeURIComponent(token)}`
}

export function useRunSocket(
  runId: string | null | undefined,
  onEvent: (event: RunSocketEvent) => void,
  enabled = true,
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
    let retries = retriesRef.current

    const cleanup = () => {
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
      if (closedByUnmount) return
      const ws = new WebSocket(wsUrl(runId, accessToken))
      wsRef.current = ws

      ws.onopen = () => {
        retries = 0
        retriesRef.current = 0
        setConnected(true)
      }

      ws.onmessage = (msg) => {
        try {
          const event = JSON.parse(msg.data as string) as RunSocketEvent
          onEventRef.current(event)
        } catch {
          // 忽略无法解析的消息
        }
      }

      ws.onclose = () => {
        setConnected(false)
        if (closedByUnmount) return
        if (retries < MAX_RETRIES) {
          retries += 1
          retriesRef.current = retries
          const delay = BASE_DELAY_MS * 2 ** (retries - 1)
          timerRef.current = window.setTimeout(connect, delay)
        }
      }

      ws.onerror = () => {
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
