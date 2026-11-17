/**
 * 认证页外壳：左侧品牌说明（大屏）+ 右侧表单区。
 *
 * 小屏隐藏品牌区，只在表单上方保留一枚标识，避免内容被挤到首屏之外。
 */

import type { ReactNode } from 'react'

import { Icon } from '../ui/Icons'
import type { IconName } from '../ui/Icons'
import { LogoMark, LogoWordmark } from '../ui/Logo'
import { ThemeToggle } from '../ui/ThemeToggle'

const FEATURES: { icon: IconName; title: string; desc: string }[] = [
  {
    icon: 'target',
    title: '拖拽即分配',
    desc: '把需求卡片拖到 Agent 或组上，选好策略立刻执行。',
  },
  {
    icon: 'layers',
    title: '四种编排策略',
    desc: '直接路由、广播、流水线、智能路由，覆盖不同协作形态。',
  },
  {
    icon: 'activity',
    title: '全链路可观测',
    desc: '步骤、耗时、成本与日志实时推送，失败可重试。',
  },
]

interface AuthShellProps {
  title: string
  subtitle: string
  children: ReactNode
  footer?: ReactNode
}

export function AuthShell({ title, subtitle, children, footer }: AuthShellProps) {
  return (
    <div className="grid min-h-screen bg-surface lg:grid-cols-[1.05fr_1fr]">
      {/* 品牌区 */}
      <aside className="relative hidden overflow-hidden bg-slate-950 lg:flex lg:flex-col lg:justify-between">
        <div
          aria-hidden="true"
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(circle at 18% 12%, rgba(37,99,235,0.42), transparent 55%), radial-gradient(circle at 82% 82%, rgba(56,189,248,0.26), transparent 52%)',
          }}
        />
        <div
          aria-hidden="true"
          className="absolute inset-0 opacity-[0.06]"
          style={{
            backgroundImage:
              'linear-gradient(to right, #ffffff 1px, transparent 1px), linear-gradient(to bottom, #ffffff 1px, transparent 1px)',
            backgroundSize: '44px 44px',
            maskImage: 'radial-gradient(ellipse at 50% 40%, black, transparent 78%)',
          }}
        />

        <div className="relative z-10 p-10 xl:p-14">
          <div className="flex items-center gap-2.5">
            <LogoMark size={36} />
            <span className="text-[15px] font-semibold tracking-tight text-white">
              Agent 工作台
            </span>
          </div>
        </div>

        <div className="relative z-10 px-10 xl:px-14">
          <h2 className="max-w-md text-3xl font-semibold leading-snug tracking-tight text-white">
            让需求自己找到
            <br />
            合适的 Agent
          </h2>
          <p className="mt-3 max-w-md text-sm leading-relaxed text-slate-300/90">
            从一张需求卡片出发，分配、编排、执行、复盘，全流程在一个工作台里完成。
          </p>

          <ul className="mt-9 space-y-4">
            {FEATURES.map((f) => (
              <li key={f.title} className="flex items-start gap-3">
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/[0.07] text-sky-200 ring-1 ring-inset ring-white/10">
                  <Icon name={f.icon} className="h-4 w-4" />
                </span>
                <span>
                  <span className="block text-sm font-medium text-white">{f.title}</span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-slate-400">
                    {f.desc}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div className="relative z-10 p-10 text-xs text-ink-muted xl:p-14">
          © {new Date().getFullYear()} Agent 工作台 · 内部协作平台
        </div>
      </aside>

      {/* 表单区：移动端靠上对齐（避免垂直居中把标题挤到屏幕中部），大屏再居中 */}
      <main className="relative flex min-h-screen flex-col justify-start px-5 pb-10 pt-14 sm:px-8 lg:justify-center lg:py-10">
        <div className="absolute right-4 top-4 sm:right-6 sm:top-6">
          <ThemeToggle />
        </div>
        <div className="mx-auto w-full max-w-[400px]">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <LogoWordmark size={34} />
          </div>

          <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
          <p className="mt-1.5 text-sm text-ink-muted">{subtitle}</p>

          <div className="mt-7">{children}</div>

          {footer && <div className="mt-6 text-center text-sm text-ink-muted">{footer}</div>}
        </div>
      </main>
    </div>
  )
}
