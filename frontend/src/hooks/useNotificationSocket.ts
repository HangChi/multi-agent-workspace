/** 站内通知实时订阅（015）：/ws/notifications。
 *
 * 与 useRunSocket 的分工：那个盯「一个 Run 的事件流」，这个盯「发给我的通知」。
 * 因此没有终态、不主动关闭，只做「连上 → 收到就回调 → 断了退避重连」。
 *
 * 几个刻意的选择：
 * - token 走子协议（`['aw.bearer', token]`），不进 URL——与 useRunSocket 同一
 *   理由：query 会落进 nginx 的 access/error 日志。
 * - 收到事件**不直接改缓存**，只回调让调用方 invalidate。通知的权威数据在
 *   后端（已读状态、分页、跨端一致），本地拼一条出来必然与列表对不上。
 * - 保留轮询作为兜底：WS 在反代超时、休眠唤醒、公司网络下都可能静默断开，
 *   而铃铛「不更新」比「晚 30 秒更新」更难被发现。
 */

import { useEffect, useRef } from 'react'

import { useAuthStore } from '../stores/authStore'

const BEARER_PROTOCOL = 'aw.bearer'
const MAX_RETRIES = 5
const BASE_DELAY_MS = 1000
/** 心跳：远小于反代 300s 空闲超时 */
const HEARTBEAT_MS = 25_000

function wsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/ws/notifications`
}

export function useNotificationSocket(onEvent: () => void): void {
  const accessToken = useAuthStore((s) => s.accessToken)
  const handlerRef = useRef(onEvent)
  handlerRef.current = onEvent

  useEffect(() => {
    if (!accessToken) return undefined

    let closed = false
    let retries = 0
    let socket: WebSocket | null = null
    let heartbeat: ReturnType<typeof setInterval> | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null

    const cleanup = () => {
      if (heartbeat) clearInterval(heartbeat)
      heartbeat = null
      if (retryTimer) clearTimeout(retryTimer)
      retryTimer = null
      if (socket) {
        socket.onopen = null
        socket.onmessage = null
        socket.onclose = null
        socket.onerror = null
        socket.close()
        socket = null
      }
    }

    const connect = () => {
      if (closed) return
      socket = new WebSocket(wsUrl(), [BEARER_PROTOCOL, accessToken])

      socket.onopen = () => {
        retries = 0
        heartbeat = setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'ping' }))
          }
        }, HEARTBEAT_MS)
      }

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(String(event.data)) as { type?: string }
          // ready/pong 是协议消息，不是通知
          if (payload.type === 'notification') handlerRef.current()
        } catch {
          // 非法 JSON 直接忽略：这条通道只承载通知，坏包不值得重连
        }
      }

      socket.onclose = () => {
        if (heartbeat) {
          clearInterval(heartbeat)
          heartbeat = null
        }
        if (closed) return
        retries += 1
        if (retries > MAX_RETRIES) return // 放弃：轮询兜底仍然在跑
        const delay = Math.min(BASE_DELAY_MS * 2 ** (retries - 1), 30_000)
        retryTimer = setTimeout(connect, delay)
      }
    }

    connect()
    return () => {
      closed = true
      cleanup()
    }
  }, [accessToken])
}
