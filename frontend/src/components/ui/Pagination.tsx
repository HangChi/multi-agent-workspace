/**
 * 分页器：列表页底部统一的翻页控件。
 *
 * 与 AdminPage 里内联的分页 UI 保持同一视觉语言（第 X / Y 页 · 每页 N 条），
 * 抽出来避免每加一个列表页就复制一遍。
 *
 * 设计约定：
 * - 受控组件：page / pageSize / total 全由外部传入，自身不存状态
 * - total 是**过滤后的全量条数**（后端已保证），总页数由它算出来
 * - 只有一页时整体不渲染 —— 一个永远禁用的翻页条只会增加噪音
 * - 总数很大时展示「共 N 条」，帮用户判断是否需要继续翻
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

  return (
    <div
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
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          leftIcon="chevron-left"
          disabled={page <= 1 || loading}
          onClick={() => go(page - 1)}
        >
          上一页
        </Button>
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
    </div>
  )
}
