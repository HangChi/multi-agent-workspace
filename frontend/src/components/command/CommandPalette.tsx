/**
 * 命令面板（⌘K / Ctrl+K）：全局跳转 + 常用动作。
 *
 * 为什么值得单独做：模块变多之后，「去某个页面」要挪鼠标到顶栏、
 * 「找某个 Agent」要在列表里翻页。主流工作台（Linear / Height / Raycast）
 * 的解法都是命令面板：一个快捷键把「导航 + 搜索 + 动作」合到一起。
 *
 * 实现要点：
 * - 快捷键全局唤起，Esc 关闭，↑↓ 选择，Enter 执行；
 * - 数据源：静态路由 + Agent / 组 / 需求三类实体，打开时并行拉取；
 * - 键盘操作走 combobox + listbox/option 语义（aria-activedescendant
 *   播报当前项），屏幕阅读器能跟着光标走；
 * - 焦点陷阱 / 焦点归还 / 滚动锁定复用 useFocusTrap（与 Modal 同一实现）；
 * - 只读搜索，不做任何写操作之外的副作用（主题切换除外，那是本地状态）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { fetchAgents, fetchGroups, fetchRequests } from '../../api/endpoints'
import { Icon } from '../ui/Icons'
import type { IconName } from '../ui/Icons'
import { Skeleton } from '../ui/Skeleton'
import { cn } from '../ui/cn'
import { useThemeStore } from '../../stores/themeStore'
import { useFocusTrap } from '../../hooks/useFocusTrap'
import { modCombo } from '../../utils/platform'
import { MAIN_NAV, SETTINGS_NAV } from '../layout/nav'

interface Item {
  id: string
  label: string
  hint?: string
  group: string
  icon: IconName
  run: () => void
}

interface Props {
  open: boolean
  onClose: () => void
}

const LISTBOX_ID = 'command-palette-listbox'

export function CommandPalette({ open, onClose }: Props) {
  const navigate = useNavigate()
  const setMode = useThemeStore((s) => s.setMode)
  const [q, setQ] = useState('')
  const [cursor, setCursor] = useState(0)
  const panelRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // 焦点陷阱 + 焦点归还 + 滚动锁定（与 Modal 同一套语义）。
  // initialFocus 传输入框：默认聚焦面板会让读屏与键盘用户
  // 多按一次 Tab 才能开始打字
  useFocusTrap(open, onClose, panelRef, inputRef)

  // 打开时才拉数据：面板不常驻，没必要为它提前发三个请求
  const enabled = open
  const { data: agents, isLoading: agentsLoading } = useQuery({
    queryKey: ['agents', 'active', 'all'],
    queryFn: () => fetchAgents({ status: 'active', all: true }),
    enabled,
    staleTime: 60_000,
  })
  const { data: groups, isLoading: groupsLoading } = useQuery({
    queryKey: ['groups', 'all'],
    queryFn: () => fetchGroups({ all: true }),
    enabled,
    staleTime: 60_000,
  })
  const { data: requests, isLoading: requestsLoading } = useQuery({
    queryKey: ['requests'],
    queryFn: () => fetchRequests(),
    enabled,
    staleTime: 30_000,
  })

  useEffect(() => {
    if (open) {
      setQ('')
      setCursor(0)
      // 焦点由 useFocusTrap 的 initialFocus（输入框）负责，这里只重置状态
    }
  }, [open])

  const go = useCallback(
    (to: string) => {
      navigate(to)
      onClose()
    },
    [navigate, onClose]
  )

  const items = useMemo<Item[]>(() => {
    const navItems: Item[] = [...MAIN_NAV, ...SETTINGS_NAV].map((n) => ({
      id: 'nav:' + n.to,
      label: n.label,
      hint: n.to,
      group: '页面',
      icon: n.icon,
      run: () => go(n.to),
    }))

    const actionItems: Item[] = [
      {
        id: 'act:new-request',
        label: '新建需求',
        hint: '在看板创建一张卡片',
        group: '动作',
        icon: 'plus',
        run: () => go('/'),
      },
      {
        id: 'act:new-agent',
        label: '新建 Agent',
        hint: '配置提示词、模型与工具',
        group: '动作',
        icon: 'bot',
        run: () => go('/agents'),
      },
      {
        id: 'act:theme',
        label: '切换主题',
        hint: '浅色 / 深色',
        group: '动作',
        icon: 'moon',
        run: () => {
          const dark = document.documentElement.classList.contains('dark')
          setMode(dark ? 'light' : 'dark')
          onClose()
        },
      },
    ]

    const agentItems: Item[] = (agents?.items ?? []).map((a) => ({
      id: 'agent:' + a.id,
      label: a.name,
      hint: a.model,
      group: 'Agent',
      icon: 'bot',
      run: () => go('/agents'),
    }))

    const groupItems: Item[] = (groups?.items ?? []).map((g) => ({
      id: 'group:' + g.id,
      label: g.name,
      hint: `${g.members.length} 个成员`,
      group: 'Agent 组',
      icon: 'users',
      run: () => go('/groups'),
    }))

    const requestItems: Item[] = (requests?.items ?? []).slice(0, 40).map((r) => ({
      id: 'req:' + r.id,
      label: r.title,
      hint: r.status,
      group: '需求',
      icon: 'file',
      run: () => go('/?request=' + r.id),
    }))

    return [...navItems, ...actionItems, ...agentItems, ...groupItems, ...requestItems]
    // eslint-disable-next-line react-hooks/exhaustive-deps -- go/setMode 已在上方用 useCallback 稳定
  }, [agents, groups, requests, go, setMode])

  const filtered = useMemo(() => {
    const like = q.trim().toLowerCase()
    if (!like) return items
    return items.filter(
      (i) => i.label.toLowerCase().includes(like) || (i.hint ?? '').toLowerCase().includes(like)
    )
  }, [items, q])

  // 搜索结果变化时把光标收回第一项，否则可能停在一个不存在的索引上
  useEffect(() => {
    setCursor(0)
  }, [q])

  // 让选中项始终在可视区域内
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>('[data-selected="true"]')
    el?.scrollIntoView({ block: 'nearest' })
  }, [cursor, filtered.length])

  if (!open) return null

  const grouped: { group: string; items: Item[] }[] = []
  for (const item of filtered) {
    const last = grouped[grouped.length - 1]
    if (last && last.group === item.group) last.items.push(item)
    else grouped.push({ group: item.group, items: [item] })
  }

  const activeId = filtered[cursor] ? `cmd-opt-${filtered[cursor].id}` : undefined

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setCursor((c) => (filtered.length === 0 ? 0 : (c + 1) % filtered.length))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setCursor((c) => (filtered.length === 0 ? 0 : (c - 1 + filtered.length) % filtered.length))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const item = filtered[cursor]
      if (item) item.run()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    }
  }

  let flatIndex = -1

  return (
    <div className="fixed inset-0 z-palette flex items-start justify-center px-4 pt-[12vh]">
      <div
        className="absolute inset-0 backdrop-tint animate-fade-in"
        // onMouseDown 而非 onClick：在面板内按下、在遮罩上松手不应误关
        // （与 Modal 的遮罩范式一致）
        onMouseDown={onClose}
        aria-hidden="true"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className="relative w-full max-w-[560px] overflow-hidden rounded-2xl border border-line bg-surface shadow-pop animate-pop-in"
      >
        <div className="flex items-center gap-2.5 border-b border-line-subtle px-4">
          <Icon name="search" className="h-4 w-4 shrink-0 text-ink-faint" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索页面、Agent、组、需求，或输入动作…"
            aria-label="搜索命令"
            // combobox 语义：读屏跟随 aria-activedescendant 播报当前高亮项
            role="combobox"
            aria-expanded="true"
            aria-controls={LISTBOX_ID}
            aria-autocomplete="list"
            aria-activedescendant={activeId}
            className="h-12 w-full bg-transparent text-body text-ink outline-none placeholder:text-ink-faint"
          />
          <kbd className="shrink-0 rounded border border-line bg-surface-muted px-1.5 py-0.5 text-2xs font-medium text-ink-faint">
            Esc
          </kbd>
        </div>

        {filtered.length === 0 ? (
          <p className="px-3 py-8 text-center text-ui text-ink-faint">
            没有匹配「{q}」的结果
          </p>
        ) : (
          <div
            ref={listRef}
            id={LISTBOX_ID}
            className="max-h-[52vh] overflow-y-auto p-1.5"
            role="listbox"
            aria-label="命令结果"
          >
            {grouped.map((section) => (
              // group 承载分区语义；标题 p 对读屏隐藏（名字已进 aria-label）
              <div key={section.group} role="group" aria-label={section.group}>
                <p
                  aria-hidden="true"
                  className="px-2.5 py-1.5 text-micro font-semibold uppercase tracking-[0.1em] text-ink-faint"
                >
                  {section.group}
                </p>
                {section.items.map((item) => {
                  flatIndex += 1
                  const selected = flatIndex === cursor
                  return (
                    <button
                      key={item.id}
                      type="button"
                      id={`cmd-opt-${item.id}`}
                      role="option"
                      aria-selected={selected}
                      data-selected={selected}
                      onMouseEnter={() => setCursor(flatIndex)}
                      onClick={item.run}
                      className={cn(
                        'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors',
                        selected ? 'bg-brand-50 text-accent dark:bg-brand-500/10' : 'text-ink-soft'
                      )}
                    >
                      <Icon
                        name={item.icon}
                        className={cn(
                          'h-4 w-4 shrink-0',
                          selected ? 'text-brand-500' : 'text-ink-faint'
                        )}
                      />
                      <span className="min-w-0 flex-1 truncate text-ui font-medium">
                        {item.label}
                      </span>
                      {item.hint && (
                        <span className="shrink-0 max-w-[40%] truncate text-micro text-ink-faint">
                          {item.hint}
                        </span>
                      )}
                      {selected && (
                        <span className="shrink-0 text-micro font-medium text-brand-500">回车</span>
                      )}
                    </button>
                  )
                })}
              </div>
            ))}

            {/* 实体分区在途时给骨架：打开瞬间三个请求并发，没有占位会让面板
                「先矮一下再长回来」。搜索过滤中不显示（结果已在收敛）。 */}
            {!q.trim() &&
              [
                { loading: agentsLoading, label: 'Agent' },
                { loading: groupsLoading, label: 'Agent 组' },
                { loading: requestsLoading, label: '需求' },
              ]
                .filter((s) => s.loading)
                .map((s) => (
                  <div key={`sk-${s.label}`} role="group" aria-label={s.label}>
                    <p
                      aria-hidden="true"
                      className="px-2.5 py-1.5 text-micro font-semibold uppercase tracking-[0.1em] text-ink-faint"
                    >
                      {s.label}
                    </p>
                    <CommandSectionSkeleton rows={2} />
                  </div>
                ))}
          </div>
        )}

        <div className="flex items-center gap-3 border-t border-line-subtle bg-surface-muted/60 px-4 py-2 text-micro text-ink-faint">
          <span className="flex items-center gap-1">
            <kbd className="rounded border border-line bg-surface px-1">↑</kbd>
            <kbd className="rounded border border-line bg-surface px-1">↓</kbd>
            选择
          </span>
          <span className="flex items-center gap-1">
            <kbd className="rounded border border-line bg-surface px-1">↵</kbd>
            打开
          </span>
          <span className="hidden sm:inline">{modCombo('K')} 随时唤起</span>
          <span className="ml-auto tabular-nums">{filtered.length} 项</span>
        </div>
      </div>
    </div>
  )
}

/** 分区骨架：查询在途时在该分区位置显示的占位行。 */
function CommandSectionSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-1.5 px-2.5 py-1">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-8" />
      ))}
    </div>
  )
}
