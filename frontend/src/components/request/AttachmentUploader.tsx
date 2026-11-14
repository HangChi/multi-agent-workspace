/** 附件上传组件（T048）：拖拽/点选 → 预签名 PUT 直传 MinIO。 */

import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../../api/client'
import { AttachmentMeta, requestAttachmentUpload } from '../../api/endpoints'
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

  const uploadMut = useMutation({
    mutationFn: async (file: File) => {
      // 1. 本地预校验
      if (file.size > MAX_SIZE) {
        throw new Error(`文件超过 20MB 限制（${(file.size / 1024 / 1024).toFixed(1)}MB）`)
      }
      // 2. 申请预签名 PUT URL
      const put = await requestAttachmentUpload(requestId, {
        filename: file.name,
        size: file.size,
        mime_type: file.type || 'application/octet-stream',
      })
      // 3. 直传 MinIO
      const resp = await fetch(put.presigned_put_url, {
        method: 'PUT',
        body: file,
      })
      if (!resp.ok) {
        throw new Error('上传对象存储失败')
      }
      return put
    },
    onSuccess: () => {
      setError(null)
      void qc.invalidateQueries({ queryKey: ['requests'] })
      onUploaded?.()
      if (inputRef.current) inputRef.current.value = ''
    },
    onError: (err) => setError(extractError(err)),
  })

  const startUpload = (file: File | undefined) => {
    if (!file) return
    setError(null)
    void uploadMut.mutateAsync(file).catch(() => {
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
          startUpload(e.dataTransfer.files?.[0])
        }}
        className={cn(
          'flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-5 text-center transition-colors duration-150',
          dragOver
            ? 'border-brand-400 bg-brand-50/80'
            : 'border-line-strong bg-surface-muted/60 hover:border-brand-300 hover:bg-brand-50/40',
          uploadMut.isPending && 'pointer-events-none opacity-70',
        )}
      >
        <input
          ref={inputRef}
          type="file"
          onChange={(e) => startUpload(e.target.files?.[0])}
          disabled={uploadMut.isPending}
          className="sr-only"
          aria-label="选择要上传的附件"
        />
        <span className="mb-1.5 flex h-8 w-8 items-center justify-center rounded-lg bg-surface text-accent shadow-sm ring-1 ring-inset ring-line">
          <Icon name="upload" className="h-4 w-4" />
        </span>
        <span className="text-xs font-medium text-ink-soft">
          {dragOver ? '松开即可上传' : '点击选择文件，或拖拽到此处'}
        </span>
        <span className="mt-0.5 text-2xs text-ink-faint">单个文件不超过 20MB</span>
      </label>

      {uploadMut.isPending && (
        <div className="mt-2">
          <div className="h-1 overflow-hidden rounded-full bg-line">
            <div className="h-full w-1/3 rounded-full bg-brand-500 animate-indeterminate" />
          </div>
          <p className="mt-1 text-2xs text-ink-muted">正在上传…</p>
        </div>
      )}

      {error && (
        <p className="mt-1.5 flex items-start gap-1 text-xs text-rose-600" role="alert">
          <Icon name="alert-circle" className="mt-px h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      )}
    </div>
  )
}
