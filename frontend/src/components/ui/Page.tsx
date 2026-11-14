/**
 * 页面容器：统一「页边距 + 区块间距 + 最大宽度」三件事。
 *
 * 之前各页面各自写 mx-auto / mb-5 / mt-3，导致同类页面留白不一致。
 * 现在所有页面根节点都用 <Page>，间距只在这里定义一次：
 * - 外层留白由 AppLayout 的 main 提供（px-4 / sm:px-6 / lg:px-8）
 * - 页面内区块间距：space-y-4，大屏 space-y-5
 * - 宽度档位：full（看板/列表）、medium（详情）、narrow（表单/结果页）
 */

import type { ReactNode } from 'react'

import { cn } from './cn'

const WIDTHS = {
  full: '',
  wide: 'max-w-6xl',
  medium: 'max-w-4xl',
  narrow: 'max-w-2xl',
} as const

interface PageProps {
  children: ReactNode
  width?: keyof typeof WIDTHS
  className?: string
}

export function Page({ children, width = 'full', className }: PageProps) {
  return (
    <div className={cn('mx-auto w-full space-y-4 sm:space-y-5', WIDTHS[width], className)}>
      {children}
    </div>
  )
}
