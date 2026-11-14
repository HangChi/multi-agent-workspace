/** 附件上传组件（T048）：选择文件 → 申请预签名 PUT URL → 直传 MinIO。 */

import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../../api/client'
import { AttachmentMeta, requestAttachmentUpload } from '../../api/endpoints'

const MAX_SIZE = 20 * 1024 * 1024 // 20MB

interface Props {
  requestId: string
  onUploaded?: (attachments?: AttachmentMeta[]) => void
}

export function AttachmentUploader({ requestId, onUploaded }: Props) {
  const qc = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)

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

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      void uploadMut.mutateAsync(file)
    }
  }

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        onChange={handleChange}
        disabled={uploadMut.isPending}
        className="block w-full text-sm text-slate-500 file:mr-3 file:rounded-lg file:border-0 file:bg-blue-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-blue-700 hover:file:bg-blue-100"
      />
      {uploadMut.isPending && (
        <p className="mt-1 text-xs text-slate-500">上传中...</p>
      )}
      {error && <p className="mt-1 text-xs text-red-500">{error}</p>}
    </div>
  )
}
