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
      // 1. 本地预校验（服务端仍会在对象存储层强制同一上限，这里只是少跑一趟）
      if (file.size > MAX_SIZE) {
        throw new Error(`文件超过 20MB 限制（${(file.size / 1024 / 1024).toFixed(1)}MB）`)
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

      const resp = await fetch(put.upload_url, { method: 'POST', body: form })
      if (!resp.ok) {
        throw new Error(
          resp.status === 400
            ? '文件超过大小限制或类型不符，上传被对象存储拒绝'
            : '上传对象存储失败'
        )
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
          uploadMut.isPending && 'pointer-events-none opacity-70'
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
        <span className="mt-0.5 text-micro text-ink-faint">单个文件不超过 20MB</span>
      </label>

      {uploadMut.isPending && (
        <div className="mt-2">
          <div className="h-1 overflow-hidden rounded-full bg-line">
            <div className="h-full w-1/3 rounded-full bg-brand-500 animate-indeterminate" />
          </div>
          <p className="mt-1 text-micro text-ink-muted">正在上传…</p>
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
