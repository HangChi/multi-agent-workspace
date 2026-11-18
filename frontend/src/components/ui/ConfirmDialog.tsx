/**
 * 确认弹窗（替代 window.confirm）：原生 confirm 无法定制样式、阻塞主线程、
 * 移动端体验差，这里统一换成 Promise 风格的确认框。
 *
 * 用法：
 *   const confirm = useConfirm()
 *   if (await confirm({ title: '删除 Agent', description: '...', tone: 'danger' })) { ... }
 *
 * 细节约定：
 * - 打开时焦点落在「取消」（安全默认）：误触 Enter 不会直接执行破坏性操作；
 * - 连续 confirm()：前一个未处置的请求按「取消」结算并 resolve，
 *   否则调用方 await 的 Promise 永不落地（静默挂起最难排查）。
 */

import { useCallback, useMemo, useRef, useState } from 'react'
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
  /** state 的镜像：confirm 回调里要能读到「上一个未结算的请求」 */
  const pendingRef = useRef<{ resolve: (value: boolean) => void } | null>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)

  const confirm = useCallback<ConfirmFn>(
    (options) =>
      new Promise<boolean>((resolve) => {
        // 上一个请求还没处置：按取消结算，别让它的 Promise 永远挂着
        pendingRef.current?.resolve(false)
        const entry = { options, resolve }
        pendingRef.current = entry
        setPending(entry)
      }),
    []
  )

  const settle = useCallback((value: boolean) => {
    pendingRef.current?.resolve(value)
    pendingRef.current = null
    setPending(null)
  }, [])

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
        // 安全默认：焦点先落在「取消」，Enter 不会误执行破坏性操作
        initialFocus={cancelRef}
        footer={
          <>
            <Button ref={cancelRef} variant="secondary" size="md" onClick={() => settle(false)}>
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
