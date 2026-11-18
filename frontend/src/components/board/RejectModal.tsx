/** 退回原因弹窗（007）：reject 的 reason 必填——「已退回却查不出理由」
 * 的记录没有审计价值（contracts §5）。 */

import { useState } from 'react'

import { WorkRequest } from '../../api/endpoints'
import { Button } from '../ui/Button'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Textarea } from '../ui/Field'
import { Modal } from '../ui/Modal'

interface Props {
  request: WorkRequest
  submitting: boolean
  error: string | null
  onConfirm: (reason: string) => void
  onCancel: () => void
}

export function RejectModal({ request, submitting, error, onConfirm, onCancel }: Props) {
  const [reason, setReason] = useState('')
  const trimmed = reason.trim()

  return (
    <Modal
      open
      onClose={onCancel}
      size="md"
      title="退回需求"
      description={`「${request.title}」将回到创建者手里，请说明原因。`}
      footer={
        <>
          <Button variant="secondary" onClick={onCancel}>
            取消
          </Button>
          <Button
            variant="primary"
            leftIcon="x-circle"
            loading={submitting}
            disabled={!trimmed}
            onClick={() => onConfirm(trimmed)}
          >
            {submitting ? '退回中…' : '确认退回'}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Textarea
          label="退回原因"
          required
          rows={3}
          maxLength={2000}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="例如：这周排期已满，建议下周再启动"
          labelExtra={<span className="tabular-nums text-ink-faint">{reason.length}/2000</span>}
        />
        {error && <ErrorBanner error={error} className="mt-0" />}
      </div>
    </Modal>
  )
}
