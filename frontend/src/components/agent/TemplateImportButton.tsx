/** 「导入模板」按钮（R5）。

 * 此前这段逻辑在 `AgentTemplatesPage` 里逐字复制了两遍（列表顶部一处、内置
 * 模板区右侧一处），含三条容易漏的规则：
 *
 * 1. `e.target.value = ''` 必须在读 `files` 之后立刻重置——否则同一个文件
 *    连选两次不会触发 onChange（值没变），用户会以为「第二次导入没反应」。
 * 2. `JSON.parse` 失败要走 `onError` 而不是抛出去——用户选错文件是常态。
 * 3. 导入的是**私有模板**，提示语必须说清「成为你的私有模板」，否则用户会
 *    以为导入了团队共享。
 *
 * 顺带修掉一个键盘可达性缺陷：原来的 `<input type="file" className="hidden">`
 * 被 `display: none` 移除出无障碍树，键盘用户既无法 Tab 到它，也无法用
 * 屏幕阅读器找到它——「导入模板」这四个字只对鼠标用户存在。改 `sr-only`
 * 后它仍在树上，Tab 可达、聚焦时可见焦点环。
 */

import { useRef } from 'react'

import { extractError } from '../../api/client'
import { importAgentTemplate } from '../../api/endpoints'
import type { TemplateExportPayload } from '../../api/endpoints'
import { useToast } from '../ui/useToast'
import { Icon } from '../ui/Icons'

export interface TemplateImportButtonProps {
  /** 导入成功后调用（通常是 invalidate 模板列表缓存）。 */
  onImported: () => void
  /** 导入失败时把可读错误交给页面统一展示（页面已有 ErrorBanner）。 */
  onError: (message: string) => void
}

export function TemplateImportButton({
  onImported,
  onError,
}: TemplateImportButtonProps) {
  const toast = useToast()
  const inputRef = useRef<HTMLInputElement>(null)

  return (
    <>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-ink-soft hover:bg-surface-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
      >
        <Icon name="plus" className="h-3.5 w-3.5" />
        导入模板
      </button>
      {/* sr-only 而不是 hidden：见文件头第 3 条 */}
      <input
        ref={inputRef}
        type="file"
        accept="application/json,.json"
        aria-label="选择要导入的模板 JSON 文件"
        className="sr-only"
        onChange={(e) => {
          const file = e.target.files?.[0]
          // 先重置 value 再 await：同一个文件连选两次也要能触发
          e.target.value = ''
          if (!file) return
          void (async () => {
            try {
              const text = await file.text()
              const payload = JSON.parse(text) as TemplateExportPayload
              await importAgentTemplate(payload)
              toast.success('已导入为你的私有模板')
              onImported()
            } catch (err) {
              onError(extractError(err))
            }
          })()
        }}
      />
    </>
  )
}
