/**
 * 附件上传组件（T048）：拖拽/点选 → 预签名 POST 直传对象存储。
 *
 * 细节：
 * - 多文件**顺序**上传（共享一个直连接口，并发会互相抢带宽且失败难归因）；
 * - XHR 而非 fetch：upload.onprogress 能给真实百分比，indeterminate 条
 *    只会让用户盯着一个永远猜不到终点的动画；
 * - 错误统一 ErrorBanner inline（设计系统组件），不手写红字；
 * - 本地预检 + 服务端/存储端双层限额（20MB），任一层拒绝都有明确文案。
 */

import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../../api/client'
import { AttachmentMeta, requestAttachmentUpload } from '../../api/endpoints'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Icon } from '../ui/Icons'
import { cn } from '../ui/cn'

const MAX_SIZE = 20 * 1024 * 1024 // 20MB

interface Props {
  requestId: string
  onUploaded?: (attachments?: AttachmentMeta[]) => void
}

export function AttachmentUploader({ requestId, onUploaded }: Props) {
  const qc = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  /** 上传进度：已完成数 / 总数 + 当前文件百分比 */
  const [progress, setProgress] = useState<{ done: number; total: number; pct: number } | null>(
    null
  )

  const uploadMut = useMutation({
    mutationFn: async (files: File[]) => {
      for (let i = 0; i < files.length; i++) {
        const file = files[i]
        // 1. 本地预校验（服务端仍会在对象存储层强制同一上限，这里只是少跑一趟）
        if (file.size > MAX_SIZE) {
          throw new Error(
            `「${file.name}」超过 20MB 限制（${(file.size / 1024 / 1024).toFixed(1)}MB）`
          )
        }
        // 2. 申请 POST Policy 上传凭据
        const put = await requestAttachmentUpload(requestId, {
          filename: file.name,
          size: file.size,
          mime_type: file.type || 'application/octet-stream',
        })
        // 3. 表单直传对象存储。
        //    用 POST + FormData 而不是 PUT：只有 policy 的 content-length-range
        //    能让存储端自己拒绝超限上传，服务端不必信任客户端声明的大小。
        const form = new FormData()
        for (const [k, v] of Object.entries(put.upload_fields)) {
          form.append(k, v)
        }
        // 文件字段名必须是 file，且放在最后（S3/MinIO 表单约定）
        form.append('file', file)

        // XHR 换取真实上传进度（fetch 拿不到 upload 事件）
        await new Promise<void>((resolve, reject) => {
          const xhr = new XMLHttpRequest()
          xhr.open('POST', put.upload_url)
          xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
              setProgress({ done: i, total: files.length, pct: Math.round((e.loaded / e.total) * 100) })
            }
          }
          xhr.onload = () =>
            xhr.status >= 200 && xhr.status < 300
              ? resolve()
              : reject(
                  new Error(
                    xhr.status === 400
                      ? `「${file.name}」超过大小限制或类型不符，上传被对象存储拒绝`
                      : `「${file.name}」上传失败（对象存储返回 ${xhr.status}）`
                  )
                )
          xhr.onerror = () => reject(new Error(`「${file.name}」上传时网络中断`))
          xhr.send(form)
        })
        setProgress({ done: i + 1, total: files.length, pct: 100 })
      }
    },
    onSuccess: () => {
      setError(null)
      setProgress(null)
      void qc.invalidateQueries({ queryKey: ['requests'] })
      onUploaded?.()
      if (inputRef.current) inputRef.current.value = ''
    },
    onError: (err) => {
      setProgress(null)
      setError(extractError(err))
      if (inputRef.current) inputRef.current.value = ''
    },
  })

  const startUpload = (files: FileList | null) => {
    const list = Array.from(files ?? []).filter(Boolean)
    if (list.length === 0) return
    setError(null)
    setProgress({ done: 0, total: list.length, pct: 0 })
    void uploadMut.mutateAsync(list).catch(() => {
      /* 错误已进 onError，这里只吞掉未处理 rejection */
    })
  }

  return (
    <div>
      <label
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          startUpload(e.dataTransfer.files)
        }}
        className={cn(
          'flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-5 text-center transition-colors duration-150',
          dragOver
            ? 'border-brand-400 bg-brand-50/80'
            : 'border-line-strong bg-surface-muted/60 hover:border-brand-300 hover:bg-brand-50/40',
          uploadMut.isPending && 'pointer-events-none opacity-70'
        )}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          onChange={(e) => startUpload(e.target.files)}
          disabled={uploadMut.isPending}
          className="sr-only"
          aria-label="选择要上传的附件"
        />
        <span className="mb-1.5 flex h-8 w-8 items-center justify-center rounded-lg bg-surface text-accent shadow-sm ring-1 ring-inset ring-line">
          <Icon name="upload" className="h-4 w-4" />
        </span>
        <span className="text-xs font-medium text-ink-soft">
          {dragOver ? '松开即可上传' : '点击选择文件，或拖拽到此处（可多选）'}
        </span>
        <span className="mt-0.5 text-micro text-ink-faint">
          单个文件不超过 20MB，多文件将依次上传
        </span>
      </label>

      {progress && uploadMut.isPending && (
        <div className="mt-2">
          <div className="h-1 overflow-hidden rounded-full bg-line">
            <div
              className="h-full rounded-full bg-brand-500 transition-[width] duration-200"
              style={{ width: `${progress.pct}%` }}
            />
          </div>
          <p className="mt-1 text-micro text-ink-muted" role="status">
            正在上传第 {progress.done + 1} / {progress.total} 个（{progress.pct}%）
          </p>
        </div>
      )}

      {error && <ErrorBanner error={error} variant="inline" className="mt-2" />}
    </div>
  )
}
