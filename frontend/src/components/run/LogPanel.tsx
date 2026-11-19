/**
 * 运行日志面板（T072 细化）：实时日志的查看体验对齐主流 log viewer
 * （GitHub Actions / Vercel / Linear）。
 *
 * 此前的缺口：
 * - auto-scroll 永远钉底：用户上滚读历史时，新日志一到就被拽回去；
 * - 200 行截断无任何提示：复制/搜索时不知道看到的是残缺日志；
 * - 无级别过滤、无搜索、无「复制全部」、行首无时间戳；
 * - ANSI 转义序列未处理：Agent 输出带颜色控制符时直接显示乱码；
 * - 「清空」不可撤销地丢弃内存中的全部日志。
 */

import { useEffect, useMemo, useRef, useState } from 'react'

import { Button, IconButton } from '../ui/Button'
import { Icon } from '../ui/Icons'
import { Input } from '../ui/Field'
import { SegmentedControl } from '../ui/Segmented'
import { cn } from '../ui/cn'

/** 日志行上限（与页面 state 的 slice(-199) 一致）：达到就要提示用户 */
export const LOG_LINE_CAP = 200

export interface LogEntry {
  line: string
  level: string
  /** 本地接收时刻（后端事件不带 ts，用接收时间兜底） */
  at: string
}

const LEVEL_OPTIONS = [
  { value: 'all' as const, label: '全部' },
  { value: 'info' as const, label: 'info' },
  { value: 'warn' as const, label: 'warn' },
  { value: 'error' as const, label: 'error' },
]

/** 日志级别配色（深色底上的浅色字） */
const LOG_LEVEL_CLASS: Record<string, string> = {
  info: 'text-slate-300',
  warn: 'text-amber-300',
  warning: 'text-amber-300',
  error: 'text-rose-300',
  debug: 'text-slate-500',
}

/** ANSI 转义序列兜底剥离：后端不保证已清洗，前端显示层不能出现乱码 */
function stripAnsi(text: string): string {
  // eslint-disable-next-line no-control-regex
  return text.replace(/\u001b\[[0-9;]*[A-Za-z]/g, '')
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--:--:--'
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

interface Props {
  logs: LogEntry[]
  /** 实时连接状态文案与颜色由父级决定（连接态这里只展示） */
  socketLabel: string
  socketConnected: boolean
  onClear: () => void
}

export function LogPanel({ logs, socketLabel, socketConnected, onClear }: Props) {
  const [follow, setFollow] = useState(true)
  const [level, setLevel] = useState<'all' | 'info' | 'warn' | 'error'>('all')
  const [q, setQ] = useState('')
  const boxRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(() => {
    const like = q.trim().toLowerCase()
    return logs.filter((log) => {
      if (level !== 'all' && log.level !== level && !(level === 'warn' && log.level === 'warning'))
        return false
      if (like && !stripAnsi(log.line).toLowerCase().includes(like)) return false
      return true
    })
  }, [logs, level, q])

  // follow 模式：仅当用户本来就贴在底部时才自动滚动；
  // 上滚即暂停跟随（不抢夺阅读位置），出现「跳到最新」按钮。
  useEffect(() => {
    if (!follow) return
    const box = boxRef.current
    if (box) box.scrollTop = box.scrollHeight
  }, [filtered.length, follow])

  const onScroll = () => {
    const box = boxRef.current
    if (!box) return
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 24
    setFollow(atBottom)
  }

  const copyAll = async () => {
    const text = filtered
      .map((l) => `${formatTime(l.at)} [${l.level}] ${stripAnsi(l.line)}`)
      .join('\n')
    try {
      await window.navigator.clipboard.writeText(text)
    } catch {
      // 剪贴板不可用（非 HTTPS / 权限）：静默失败，用户可手动选择
    }
  }

  const truncated = logs.length >= LOG_LINE_CAP

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-[#0b1220] shadow-card">
      {/* 工具行：计数 / 连接态 / 过滤 / 搜索 / 复制 / 清空 */}
      <div className="flex flex-wrap items-center gap-2 border-b border-white/10 px-3 py-2">
        <span className="flex items-center gap-1.5 text-micro font-medium text-slate-200">
          <Icon name="file" className="h-3.5 w-3.5 text-slate-400" />
          运行日志
          <span className="rounded bg-white/10 px-1.5 py-0.5 text-micro tabular-nums text-slate-300">
            {filtered.length}
            {filtered.length !== logs.length && ` / ${logs.length}`}
          </span>
        </span>

        <span className="flex items-center gap-1.5 text-micro text-slate-500">
          <span
            className={cn(
              'h-1.5 w-1.5 rounded-full',
              socketConnected ? 'bg-emerald-400' : 'bg-slate-600'
            )}
          />
          {socketLabel}
        </span>

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <SegmentedControl
            options={LEVEL_OPTIONS}
            value={level}
            onChange={setLevel}
            ariaLabel="按级别过滤日志"
            className="scale-95 origin-right [&_button]:text-slate-300 [&_button[aria-checked=true]]:bg-white/15 [&_button[aria-checked=true]]:text-white"
          />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索日志"
            leftIcon="search"
            controlSize="sm"
            aria-label="搜索日志"
            wrapperClassName="w-32"
            className="border-white/15 bg-white/5 text-slate-200 placeholder:text-slate-500 focus:border-brand-400"
            rightSlot={
              q ? (
                <IconButton
                  icon="x"
                  label="清除日志搜索"
                  size="xs"
                  onClick={() => setQ('')}
                  className="text-slate-400 hover:bg-white/10 hover:text-slate-200"
                />
              ) : undefined
            }
          />
          <IconButton
            icon="copy"
            label="复制全部日志"
            size="sm"
            onClick={() => void copyAll()}
            className="text-slate-400 hover:bg-white/10 hover:text-slate-200"
          />
          {logs.length > 0 && (
            <IconButton
              icon="trash"
              label="清空日志"
              size="sm"
              onClick={onClear}
              className="text-slate-400 hover:bg-white/10 hover:text-rose-300"
            />
          )}
        </div>
      </div>

      {/* 截断提示：200 行上限必须可见，否则用户以为看到的是全部 */}
      {truncated && (
        <p className="border-b border-white/10 bg-amber-500/10 px-3 py-1.5 text-micro text-amber-200">
          仅保留最近 {LOG_LINE_CAP} 行（更早的输出已滚出缓冲）；完整日志请在服务端日志系统查看。
        </p>
      )}

      <div
        ref={boxRef}
        onScroll={onScroll}
        className="max-h-72 overflow-y-auto px-3 py-2.5 font-mono text-xs leading-relaxed"
      >
        {filtered.length === 0 ? (
          <p className="py-4 text-center text-slate-500">
            {logs.length === 0
              ? '等待日志…'
              : `没有匹配${level !== 'all' ? `级别「${level}」` : ''}${q.trim() ? `关键字「${q.trim()}」` : ''}的日志`}
          </p>
        ) : (
          filtered.map((log, i) => (
            <p key={`${log.at}-${i}`} className="flex gap-2 py-0.5">
              <span className="shrink-0 select-none tabular-nums text-slate-600">
                {formatTime(log.at)}
              </span>
              <span className="shrink-0 select-none text-slate-600">
                {String(i + 1).padStart(3, '0')}
              </span>
              <span className="shrink-0 uppercase opacity-70">{log.level.padEnd(5, ' ')}</span>
              <span
                className={cn(
                  'min-w-0 whitespace-pre-wrap break-words',
                  LOG_LEVEL_CLASS[log.level] ?? 'text-slate-300'
                )}
              >
                {stripAnsi(log.line)}
              </span>
            </p>
          ))
        )}
      </div>

      {/* 不在底部时给出明确的回底入口 */}
      {!follow && filtered.length > 0 && (
        <div className="flex justify-center border-t border-white/10 py-1.5">
          <Button
            size="xs"
            variant="secondary"
            leftIcon="arrow-down"
            onClick={() => {
              setFollow(true)
              const box = boxRef.current
              if (box) box.scrollTop = box.scrollHeight
            }}
            className="border-white/15 bg-white/10 text-slate-200 hover:bg-white/15"
          >
            跳到最新
          </Button>
        </div>
      )}
    </div>
  )
}
