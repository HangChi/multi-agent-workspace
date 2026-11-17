/**
 * 确认弹窗（替代 window.confirm）：原生 confirm 无法定制样式、阻塞主线程、
 * 移动端体验差，这里统一换成 Promise 风格的确认框。
 *
 * 用法：
 *   const confirm = useConfirm()
 *   if (await confirm({ title: '删除 Agent', description: '...', tone: 'danger' })) { ... }
 */

import { useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { Button } from './Button'
import { IconTile } from './Card'
import { Icon } from './Icons'
import { Modal } from './Modal'
import { ConfirmContext } from './useConfirm'
import type { ConfirmFn, ConfirmOptions } from './useConfirm'

export type { ConfirmOptions } from './useConfirm'

const TONE_META = {
  danger: { variant: 'danger', tile: 'rose', icon: 'alert-triangle' },
  warning: { variant: 'warning', tile: 'amber', icon: 'alert-triangle' },
  primary: { variant: 'primary', tile: 'brand', icon: 'info' },
} as const

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<{
    options: ConfirmOptions
    resolve: (value: boolean) => void
  } | null>(null)

  const confirm = useCallback<ConfirmFn>(
    (options) => new Promise<boolean>((resolve) => setPending({ options, resolve })),
    []
  )

  const settle = useCallback(
    (value: boolean) => {
      pending?.resolve(value)
      setPending(null)
    },
    [pending]
  )

  const meta = useMemo(() => TONE_META[pending?.options.tone ?? 'primary'], [pending?.options.tone])

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Modal
        open={!!pending}
        onClose={() => settle(false)}
        size="sm"
        title={pending?.options.title}
        description={pending?.options.description}
        icon={
          <IconTile tone={meta.tile} size="md">
            <Icon name={meta.icon} className="h-[18px] w-[18px]" />
          </IconTile>
        }
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => settle(false)}>
              {pending?.options.cancelText ?? '取消'}
            </Button>
            <Button variant={meta.variant} size="md" onClick={() => settle(true)}>
              {pending?.options.confirmText ?? '确认'}
            </Button>
          </>
        }
      />
    </ConfirmContext.Provider>
  )
}
