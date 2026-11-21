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
 *
 * 连接状态机（011 之后的细化）：页面要能区分「正在连 / 已连 / 重连中 /
 * 已放弃」——此前只有布尔 connected，「重连中」与「彻底失败」都显示成
 * 「未连接」，用户无法判断该等待还是该刷新。重连耗尽后调用方可用
 * reconnect() 手动再来一轮；connected 由 false→true 的跳变意味着
 * 断线期间的事件丢了，调用方应重拉一次详情补数。
 */

import { useEffect, useRef, useState } from 'react'

import { useAuthStore } from '../stores/authStore'

export interface RunSocketEvent {
  type: string
  [key: string]: unknown
}

export type SocketStatus = 'connecting' | 'connected' | 'reconnecting' | 'failed'

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
): {
  connected: boolean
  status: SocketStatus
  retryCount: number
  /** 重连耗尽后手动再来一轮（重置退避计数） */
  reconnect: () => void
} {
  const accessToken = useAuthStore((s) => s.accessToken)
  const [connected, setConnected] = useState(false)
  const [status, setStatus] = useState<SocketStatus>('connecting')
  const [retryCount, setRetryCount] = useState(0)
  const wsRef = useRef<WebSocket | null>(null)
  const retriesRef = useRef(0)
  const timerRef = useRef<number | null>(null)
  const onEventRef = useRef(onEvent)
  /**
   * token 放 ref：子协议只在**握手**时校验一次，已建立的连接不因续期失效。
   * 若把 accessToken 写进 effect 依赖，任何一次 401 静默刷新（axios 拦截器
   * setTokens）都会重建连接 -> connected 跳变 -> 调用方以为断线过而重拉详情、
   * 清空日志（docs/code-audit-2026-11-21.md D3 的连锁一半来自这里）。
   * 这里只读最新值用于建连，不触发 effect 重跑。
   */
  const tokenRef = useRef(accessToken)
  useEffect(() => {
    tokenRef.current = accessToken
  }, [accessToken])
  /** effect 内建立的连接函数：reconnect() 通过它手动触发新一轮 */
  const connectRef = useRef<(() => void) | null>(null)
  /** 是否有可用 token —— 用 ref 判断，避免 effect 依赖它 */
  const hasToken = accessToken !== null && accessToken !== undefined

  // 回调最新引用放 effect：写在 render body 里在 React 并发下可能撕裂
  useEffect(() => {
    onEventRef.current = onEvent
  }, [onEvent])

  useEffect(() => {
    if (!runId || !hasToken || !enabled) {
      setStatus('connecting')
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
      // 重试中的连接尝试要显示「重连中」而不是「连接中」
      setStatus(retries > 0 ? 'reconnecting' : 'connecting')
      // 用 ref 里的最新 token 建连（握手只校验一次，续期不影响已建连接）
      const ws = new WebSocket(wsUrl(runId), [BEARER_PROTOCOL, tokenRef.current ?? ''])
      wsRef.current = ws

      ws.onopen = () => {
        retries = 0
        retriesRef.current = 0
        setRetryCount(0)
        setStatus('connected')
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
          setRetryCount(retries)
          setStatus('reconnecting')
          const delay = BASE_DELAY_MS * 2 ** (retries - 1)
          timerRef.current = window.setTimeout(connect, delay)
        } else {
          // 退避耗尽：不再自动重连，等用户手动（页面此时应降级轮询）
          setStatus('failed')
        }
      }

      ws.onerror = () => {
        stopHeartbeat()
        ws.close()
      }
    }

    connectRef.current = connect
    connect()

    return () => {
      closedByUnmount = true
      connectRef.current = null
      cleanup()
    }
  }, [runId, enabled, hasToken])

  return {
    connected,
    status,
    retryCount,
    reconnect: () => {
      // 手动重连：重置退避计数，清掉在途计时器与旧 socket，立刻重连
      retriesRef.current = 0
      setRetryCount(0)
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
      if (wsRef.current) {
        wsRef.current.onclose = null
        wsRef.current.onerror = null
        wsRef.current.close()
        wsRef.current = null
      }
      connectRef.current?.()
    },
  }
}
