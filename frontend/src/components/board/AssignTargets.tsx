/** 分配目标区（T056 + 007 + 014）：Agent / 组 / **员工**三类拖放目标。
 *
 * 版式取舍（014 重写的起因）：
 * - 旧版把三类目标混在一行横滚里。验收脚本攒下的测试账号把同事芯片
 *   撑到 300+，那一行变成永远滚不到头的墙——「这里能拖」的信息被彻底
 *   淹没（用户 2026-11-18 的截图现场）。数据清理（cleanup_test_data.py）
 *   解决存量，这个组件解决增量：**按类型分行 + 折叠 + 过滤**。
 * - 同事行带过滤框：账号再多也是搜一下的事，不再靠滚；
 * - 每行默认只露 8 个，超出折成「+N 展开」——执行目标（Agent/组）
 *   通常个位数，同事行才是大户；
 * - 拖拽语义不变：useDroppable 的 id 与 title 与原版一致（s12 的 B4
 *   依赖 `[title*='拖到这里分配给']`），折叠只是不渲染，不改变契约。
 */

import { useDroppable } from '@dnd-kit/core'
import { useEffect, useMemo, useRef, useState } from 'react'

import { Agent, Group, RoutingStrategy, UserOption } from '../../api/endpoints'
import { IconButton } from '../ui/Button'
import { Icon } from '../ui/Icons'
import { cn } from '../ui/cn'

const STRATEGY_LABELS: Record<RoutingStrategy, string> = {
  direct: '直接',
  fan_out: '广播',
  pipeline: '流水线',
  router: '路由器',
}

/** 每类目标默认露出的芯片数，超出折叠。 */
const VISIBLE_CAP = 8

function TargetChip({
  id,
  kind,
  name,
  meta,
  isGroup,
  initials,
}: {
  id: string
  kind: 'agent' | 'group' | 'user'
  name: string
  meta: string
  isGroup?: boolean
  initials?: string
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `${kind}:${id}`, data: { kind, id } })

  return (
    <div
      ref={setNodeRef}
      title={`拖到这里${kind === 'user' ? '分派给' : '分配给'}「${name}」 · ${meta}`}
      className={cn(
        'flex h-8 shrink-0 items-center gap-1.5 rounded-md border pl-1 pr-2 transition-colors duration-150',
        isOver
          ? 'border-brand-400 bg-brand-50 shadow-sm dark:bg-brand-500/10'
          : 'border-line bg-surface hover:border-line-strong hover:bg-surface-muted'
      )}
    >
      {isGroup ? (
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-500/25">
          <Icon name="users" className="h-3.5 w-3.5" />
        </span>
      ) : (
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-gradient-to-br from-sky-500 to-brand-600 text-micro font-semibold text-white">
          {initials}
        </span>
      )}
      <span className="max-w-[9rem] truncate text-ui font-medium text-ink">{name}</span>
      {isOver && <Icon name="arrow-down" className="h-3.5 w-3.5 shrink-0 text-brand-500" />}
    </div>
  )
}

interface Props {
  agents: Agent[]
  groups: Group[]
  /** 007：在职员工（分派落点）。 */
  users: UserOption[]
}

/** 一行目标：标签 + 计数 + 芯片（可折叠）+ 可选过滤框。 */
function TargetRow({
  label,
  hint,
  items,
  filter,
}: {
  label: string
  hint: string
  /** 每项自带 key（调用方 map 时带上）。 */
  items: React.ReactNode[]
  filter?: React.ReactNode
}) {
  const [expanded, setExpanded] = useState(false)
  const shown = expanded ? items : items.slice(0, VISIBLE_CAP)
  const hidden = items.length - shown.length

  // 右侧渐隐只在「真的还能横滑」时出现：常驻渐隐会在不可滚时也挡一片内容
  const scrollRef = useRef<HTMLDivElement>(null)
  const [scrollable, setScrollable] = useState(false)
  const checkScrollable = () => {
    const el = scrollRef.current
    if (!el) return
    setScrollable(el.scrollWidth > el.clientWidth && el.scrollLeft < el.scrollWidth - el.clientWidth - 1)
  }
  // 挂载 / 展开收起 / 条目变化时各量一次：只靠 onScroll 会漏掉初始态
  useEffect(checkScrollable, [items.length, expanded])

  return (
    <div className="flex items-center gap-2">
      <span className="flex w-[5.5rem] shrink-0 items-center gap-1 text-micro font-medium text-ink-faint">
        {label}
        <span className="tabular-nums">({items.length})</span>
      </span>
      {filter}
      <div className="relative min-w-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={checkScrollable}
          role="group"
          aria-label={`${label}拖放目标`}
          className="scrollbar-none flex items-center gap-1.5 overflow-x-auto py-0.5 pr-6"
        >
          {items.length === 0 ? <span className="text-micro text-ink-faint">{hint}</span> : shown}
          {hidden > 0 && (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="flex h-8 shrink-0 items-center rounded-md border border-dashed border-line px-2 text-micro font-medium text-ink-muted transition-colors hover:border-line-strong hover:text-ink-soft"
            >
              +{hidden} 展开
            </button>
          )}
          {expanded && items.length > VISIBLE_CAP && (
            <button
              type="button"
              onClick={() => setExpanded(false)}
              className="flex h-8 shrink-0 items-center rounded-md px-2 text-micro font-medium text-ink-faint transition-colors hover:text-ink-soft"
            >
              收起
            </button>
          )}
        </div>
        {/* 右侧渐隐：提示这一排还能横向滑（仅可滑时） */}
        {scrollable && (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 right-0 z-10 w-6 bg-gradient-to-l from-canvas to-transparent"
          />
        )}
      </div>
    </div>
  )
}

export function AssignTargets({ agents, groups, users }: Props) {
  const [userFilter, setUserFilter] = useState('')

  const filteredUsers = useMemo(() => {
    const q = userFilter.trim().toLowerCase()
    if (!q) return users
    return users.filter((u) => u.email.toLowerCase().includes(q))
  }, [users, userFilter])

  if (agents.length === 0 && groups.length === 0 && users.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-line px-3 py-2 text-ui text-ink-faint">
        还没有可分配的 Agent、组或同事 —— 到「Agent 管理」创建一个，或等同事加入后再回来拖。
      </p>
    )
  }

  return (
    <section aria-label="分配目标" className="space-y-1.5">
      {/* 同事：拖 = 转交处理责任 */}
      <TargetRow
        label="同事"
        hint="还没有同事"
        items={filteredUsers.map((u) => (
          <TargetChip
            key={u.id}
            id={u.id}
            kind="user"
            name={u.email.split('@')[0]}
            meta={u.role === 'admin' ? '管理员 · 转交处理责任' : '同事 · 转交处理责任'}
            initials={u.email.slice(0, 1).toUpperCase()}
          />
        ))}
        filter={
          <span className="flex h-8 shrink-0 items-center gap-1 rounded-md border border-line bg-surface px-2">
            <Icon name="search" className="h-3.5 w-3.5 text-ink-faint" />
            <input
              value={userFilter}
              onChange={(e) => setUserFilter(e.target.value)}
              placeholder="搜同事"
              aria-label="筛选分配同事"
              className="w-24 bg-transparent text-ui text-ink outline-none placeholder:text-ink-faint"
            />
            {userFilter && (
              <IconButton
                icon="x"
                label="清除同事筛选"
                size="xs"
                onClick={() => setUserFilter('')}
              />
            )}
          </span>
        }
      />

      {/* Agent：拖 = 启动执行 */}
      <TargetRow
        label="Agent"
        hint="还没有 Agent"
        items={agents.map((a) => (
          <TargetChip
            key={a.id}
            id={a.id}
            kind="agent"
            name={a.name}
            meta={`${a.model} · ${a.tools.length} 个工具`}
            initials={a.name.slice(0, 1)}
          />
        ))}
      />

      {/* 组：拖 = 启动执行（按组的路由策略） */}
      <TargetRow
        label="组"
        hint="还没有组"
        items={groups.map((g) => (
          <TargetChip
            key={g.id}
            id={g.id}
            kind="group"
            name={g.name}
            meta={`${STRATEGY_LABELS[g.routing_strategy]} · ${g.members.length} 个成员`}
            isGroup
          />
        ))}
      />
    </section>
  )
}
