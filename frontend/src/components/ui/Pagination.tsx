/**
 * 分页器：列表页底部统一的翻页控件。
 *
 * 设计约定：
 * - 受控组件：page / pageSize / total 全由外部传入，自身不存状态
 * - total 是**过滤后的全量条数**（后端已保证），总页数由它算出来
 * - 只有一页时整体不渲染 —— 一个永远禁用的翻页条只会增加噪音
 * - 总数很大时展示「共 N 条」，帮用户判断是否需要继续翻
 * - 无障碍：nav 地标 + aria-current="page" + 翻页后朗读当前页
 *   （纯 div + 两个按钮，读屏用户不知道这是分页、也不知道在第几页）；
 * - 页数多时给数字页 + 省略号：审计日志这类几百页的场景，
 *   只有上/下页意味着要点的次数正比于目标页
 */

import { Button } from './Button'
import { cn } from './cn'

interface PaginationProps {
  /** 当前页，从 1 开始 */
  page: number
  /** 每页条数 */
  pageSize: number
  /** 过滤后的全量条数 */
  total: number
  /** 翻页回调（收到的是目标页，已做边界钳制） */
  onChange: (page: number) => void
  /** 数据加载中：禁用按钮，避免连点造成请求乱序 */
  loading?: boolean
  className?: string
}

/**
 * 总页数（至少 1，空列表也算 1 页）。
 *
 * 不对外导出：本文件同时导出组件，再导出一个函数会触发
 * react-refresh/only-export-components（热更新会把模块当非组件处理）。
 * 测试通过组件行为间接覆盖，无需单独引用。
 */
function totalPagesOf(total: number, pageSize: number): number {
  if (pageSize <= 0) return 1
  return Math.max(1, Math.ceil(total / pageSize))
}

/**
 * 数字页序列：首尾 + 当前页左右各一，其余折成省略号。
 * 返回 number 或 '…'；不超过 7 个元素，窄屏也放得下。
 */
function pageItems(current: number, totalPages: number): (number | 'ellipsis')[] {
  if (totalPages <= 5) return Array.from({ length: totalPages }, (_, i) => i + 1)
  const pages = new Set<number>([1, totalPages, current, current - 1, current + 1])
  const sorted = [...pages].filter((p) => p >= 1 && p <= totalPages).sort((a, b) => a - b)
  const out: (number | 'ellipsis')[] = []
  let prev = 0
  for (const p of sorted) {
    if (p - prev > 1) out.push('ellipsis')
    out.push(p)
    prev = p
  }
  return out
}

export function Pagination({
  page,
  pageSize,
  total,
  onChange,
  loading,
  className,
}: PaginationProps) {
  const totalPages = totalPagesOf(total, pageSize)

  // 只有一页时整体不渲染 —— 一个永远禁用的翻页条只会增加噪音。
  // 空列表由 EmptyState 负责表达，也不需要分页器。
  if (totalPages <= 1) return null

  const go = (next: number) => {
    const clamped = Math.min(totalPages, Math.max(1, next))
    if (clamped !== page) onChange(clamped)
  }

  const items = pageItems(page, totalPages)

  return (
    <nav
      aria-label="分页"
      className={cn(
        'flex flex-wrap items-center justify-between gap-2 border-t border-line-subtle pt-3',
        className
      )}
    >
      <p className="text-xs text-ink-muted">
        第 <span className="font-medium text-ink-soft">{page}</span> / {totalPages} 页
        <span className="mx-1.5 text-ink-faint">·</span>共{' '}
        <span className="font-medium text-ink-soft">{total}</span> 条
        <span className="mx-1.5 text-ink-faint">·</span>
        每页 {pageSize} 条
      </p>
      <div className="flex items-center gap-1.5">
        <Button
          size="sm"
          variant="secondary"
          leftIcon="chevron-left"
          disabled={page <= 1 || loading}
          onClick={() => go(page - 1)}
        >
          上一页
        </Button>

        {/* 数字页：>5 页才出现，否则纯噪音 */}
        {totalPages > 5 && (
          <div className="hidden items-center gap-0.5 sm:flex">
            {items.map((it, i) =>
              it === 'ellipsis' ? (
                <span key={`e${i}`} className="px-1 text-xs text-ink-faint" aria-hidden="true">
                  …
                </span>
              ) : (
                <button
                  key={it}
                  type="button"
                  onClick={() => go(it)}
                  disabled={loading}
                  aria-current={it === page ? 'page' : undefined}
                  aria-label={`第 ${it} 页`}
                  className={cn(
                    'h-8 min-w-8 rounded-lg px-2 text-xs font-medium tabular-nums transition-colors',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45',
                    'disabled:cursor-not-allowed disabled:opacity-50',
                    it === page
                      ? 'bg-brand-50 text-accent ring-1 ring-inset ring-brand-100 dark:bg-brand-500/10'
                      : 'text-ink-muted hover:bg-surface-subtle hover:text-ink'
                  )}
                >
                  {it}
                </button>
              )
            )}
          </div>
        )}

        <Button
          size="sm"
          variant="secondary"
          rightIcon="chevron-right"
          disabled={page >= totalPages || loading}
          onClick={() => go(page + 1)}
        >
          下一页
        </Button>
      </div>

      {/* 翻页朗读：按钮上的 aria-current 之外，再给读屏一个明确的当前位置 */}
      <p className="sr-only" role="status">
        当前第 {page} 页，共 {totalPages} 页
      </p>
    </nav>
  )
}
