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

/**
 * 头像色板：按用户/Agent 的 id 散列到一套固定的柔和渐变。
 *
 * 旧版所有人共用同一个「sky→brand」蓝渐变——一排芯片望去是七八个
 * 一模一样的蓝色方块，既单调也没法靠颜色区分人。散列到五套色相后
 * 每颗芯片自带「身份色」，扫读和点名都快得多。
 */
const AVATAR_GRADIENTS = [
  'from-sky-400 to-brand-500',
  'from-emerald-400 to-teal-500',
  'from-amber-400 to-orange-500',
  'from-rose-400 to-pink-500',
  'from-violet-400 to-purple-500',
] as const

function avatarGradient(seed: string): string {
  let hash = 0
  for (let i = 0; i < seed.length; i += 1) hash = (hash * 31 + seed.charCodeAt(i)) >>> 0
  return AVATAR_GRADIENTS[hash % AVATAR_GRADIENTS.length]
}

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
        'flex h-8 shrink-0 items-center gap-1.5 rounded-lg border pl-1 pr-2 transition-all duration-150',
        // 白底工具条上的芯片用浅灰底：纯白芯片会「消失」只剩边框
        isOver
          ? 'border-brand-400 bg-brand-50 shadow-card dark:bg-brand-500/15'
          : 'border-line/80 bg-surface-muted hover:border-line-strong hover:bg-surface hover:shadow-card'
      )}
    >
      {isGroup ? (
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-500/25">
          <Icon name="users" className="h-3.5 w-3.5" />
        </span>
      ) : (
        <span
          className={cn(
            'flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gradient-to-br text-micro font-semibold text-white shadow-sm',
            avatarGradient(id)
          )}
        >
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
  rowIcon,
  hintIcon,
  items,
  filter,
}: {
  label: string
  hint: string
  /** 行首图标与空态图标（调用方按目标类型给） */
  rowIcon: 'users' | 'bot' | 'user'
  hintIcon: 'plus' | 'search'
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
    <div className="flex items-center gap-2.5">
      {/* 标签列：图标 + 名称 + 计数胶囊。
          旧版是「同事 (6)」一串小灰字——行首没有锚点，三行并排看时
          层次糊成一片。加了图标和计数胶囊后每行自带「标题感」。 */}
      <span className="flex w-[5.75rem] shrink-0 items-center gap-1.5">
        <Icon name={rowIcon} className="h-3.5 w-3.5 shrink-0 text-ink-faint" />
        <span className="text-ui font-medium text-ink-soft">{label}</span>
        <span
          className={cn(
            'rounded-full px-1.5 text-2xs font-semibold tabular-nums',
            items.length > 0
              ? 'bg-surface-subtle text-ink-soft ring-1 ring-inset ring-line/70'
              : 'text-ink-faint'
          )}
        >
          {items.length}
        </span>
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
          {items.length === 0 ? (
            // 空态做成虚线「空位」 pill：读作「这里可以有，现在没有」，
            // 而不是一句没着落的灰字
            <span className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-dashed border-line px-2.5 text-micro text-ink-faint">
              <Icon name={hintIcon} className="h-3.5 w-3.5" />
              {hint}
            </span>
          ) : (
            shown
          )}
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
    <section aria-label="分配目标" className="divide-y divide-line-subtle">
      {/* 同事：拖 = 转交处理责任 */}
      <TargetRow
        label="同事"
        hint="还没有同事"
        rowIcon="user"
        hintIcon="plus"
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
          // 过滤框贴行尾右侧：夹在标签和芯片之间会把一行切成三段，
          // 挪到最右像「行尾工具」，视觉动线（标签 → 芯片 → 工具）顺得多
          <span className="ml-auto flex h-8 shrink-0 items-center gap-1 rounded-lg border border-line/80 bg-surface px-2 shadow-card">
            <Icon name="search" className="h-3.5 w-3.5 text-ink-faint" />
            <input
              value={userFilter}
              onChange={(e) => setUserFilter(e.target.value)}
              placeholder="搜同事"
              aria-label="筛选分配同事"
              className="w-20 bg-transparent text-ui text-ink outline-none placeholder:text-ink-faint"
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
        hint="到 Agent 管理创建"
        rowIcon="bot"
        hintIcon="plus"
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
        hint="到 Agent 组创建"
        rowIcon="users"
        hintIcon="plus"
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
