/**
 * 品牌标识。
 *
 * 图形语义：一个「A」（Agent）由三段路径连成，顶点与两端各有一个节点 ——
 * 既像字母 A，也像「一个需求被路由到多个 Agent」的编排拓扑；
 * 节点用实心圆、路径用圆头描边，16px 下仍然清晰。
 *
 * 颜色固定用品牌蓝渐变，浅色/深色主题下都保持同一识别度，
 * 因此这里不引用主题令牌（logo 不随主题变色是常规做法）。
 */

import { useId } from 'react'

import { cn } from './cn'

interface LogoMarkProps {
  /** 像素尺寸（正方形） */
  size?: number
  className?: string
  /** 无障碍名称；纯装饰场景传空字符串 */
  title?: string
}

export function LogoMark({ size = 32, className, title = 'Agent 工作台' }: LogoMarkProps) {
  // useId 带冒号，直接拼进 url(#...) 会有转义问题，先清掉
  const uid = useId().replace(/:/g, '')
  const gradientId = `aw-logo-${uid}`
  const sheenId = `aw-logo-sheen-${uid}`

  return (
    <svg
      viewBox="0 0 32 32"
      width={size}
      height={size}
      className={cn('shrink-0', className)}
      role={title ? 'img' : 'presentation'}
      aria-label={title || undefined}
      aria-hidden={title ? undefined : true}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#38bdf8" />
          <stop offset="52%" stopColor="#2563eb" />
          <stop offset="100%" stopColor="#1d4ed8" />
        </linearGradient>
        <linearGradient id={sheenId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.28" />
          <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
        </linearGradient>
      </defs>

      <rect width="32" height="32" rx="9.5" fill={`url(#${gradientId})`} />
      <rect width="32" height="32" rx="9.5" fill={`url(#${sheenId})`} />

      {/* A 的两条腿：从顶点向两个 Agent 节点分叉 */}
      <path
        d="M10.4 23.6 16 9.4l5.6 14.2"
        fill="none"
        stroke="#fff"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* A 的横杠 */}
      <path d="M12.5 18.2h7" stroke="#fff" strokeWidth="2.1" strokeLinecap="round" opacity="0.95" />

      {/* 两个端点节点：路由的落点 */}
      <circle cx="10.4" cy="23.6" r="2.35" fill="#fff" />
      <circle cx="21.6" cy="23.6" r="2.35" fill="#fff" />
    </svg>
  )
}

/** 标识 + 文字组合，用于顶栏与登录页。 */
export function LogoWordmark({
  size = 30,
  className,
  textClassName,
}: {
  size?: number
  className?: string
  textClassName?: string
}) {
  return (
    <span className={cn('flex items-center gap-2.5', className)}>
      <LogoMark size={size} />
      <span className={cn('flex items-baseline gap-1 text-[15px] leading-none', textClassName)}>
        <span className="font-semibold tracking-tight text-ink">Agent</span>
        <span className="font-normal text-ink-muted">工作台</span>
      </span>
    </span>
  )
}
