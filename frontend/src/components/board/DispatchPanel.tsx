/** 分派面板（007 T034）：详情弹层里的「这件活现在在谁手上」。
 *
 * 三块内容，对应 US1/US2/US6：
 * - 当前接收人（pending/accepted 时显示；created 时提示未分派）；
 * - 接收 / 退回按钮（仅当前接收人可见接收；接收人或创建者可见退回）；
 * - 经手链时间线（dispatch/reassign/accept/reject 正序，含退回原因）。
 *
 * 按钮可见性由 props 显式给出而不是自己推断身份——当前用户是谁、
 * 是不是接收人，只有详情数据的调用方（BoardPage）清楚，组件不重复推导。
 */

import { DispatchRecord, WorkRequest } from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Icon } from '../ui/Icons'

const ACTION_META: Record<
  DispatchRecord['action'],
  {
    label: string
    tone: 'brand' | 'amber' | 'emerald' | 'rose'
    icon: 'send' | 'users' | 'check-circle' | 'x-circle'
  }
> = {
  dispatch: { label: '分派', tone: 'brand', icon: 'send' },
  reassign: { label: '转交', tone: 'amber', icon: 'users' },
  accept: { label: '接收', tone: 'emerald', icon: 'check-circle' },
  reject: { label: '退回', tone: 'rose', icon: 'x-circle' },
}

function shortEmail(email: string | null | undefined): string {
  if (!email) return '未知用户'
  return email.split('@')[0]
}

interface Props {
  request: WorkRequest
  /** 当前登录用户 id（调用方传入，用于按钮可见性）。 */
  currentUserId: string
  accepting: boolean
  rejecting: boolean
  onAccept: () => void
  onReject: () => void
}

export function DispatchPanel({
  request,
  currentUserId,
  accepting,
  rejecting,
  onAccept,
  onReject,
}: Props) {
  const isAssignee = request.assignee_id === currentUserId
  const isOwner = request.owner_id === currentUserId
  const canAccept = isAssignee && request.status === 'pending'
  const canReject =
    (isAssignee || isOwner) && (request.status === 'pending' || request.status === 'accepted')
  const history = request.dispatch_history ?? []

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface-muted/80 px-3 py-2.5">
        <Icon
          name={request.assignee_id ? 'user' : 'inbox'}
          className="h-3.5 w-3.5 text-ink-faint"
        />
        {request.assignee_id ? (
          <>
            <span className="text-xs text-ink-muted">当前接收人</span>
            <span className="text-ui font-medium text-ink">
              {shortEmail(request.assignee_email)}
              {isAssignee && <span className="ml-1 text-xs text-ink-faint">（我）</span>}
            </span>
            {request.status === 'pending' && (
              <Badge tone="amber" dot>
                待对方接收
              </Badge>
            )}
            {request.status === 'accepted' && (
              <Badge tone="emerald" dot>
                已接收
              </Badge>
            )}
          </>
        ) : (
          <span className="text-ui text-ink-muted">尚未分派给任何人</span>
        )}

        {(canAccept || canReject) && (
          <div className="ml-auto flex items-center gap-2">
            {canAccept && (
              <Button
                variant="primary"
                size="sm"
                leftIcon="check-circle"
                loading={accepting}
                onClick={onAccept}
              >
                接收
              </Button>
            )}
            {canReject && (
              <Button
                variant="secondary"
                size="sm"
                leftIcon="x-circle"
                loading={rejecting}
                onClick={onReject}
              >
                退回
              </Button>
            )}
          </div>
        )}
      </div>

      {history.length > 0 && (
        <div>
          <p className="mb-1.5 text-ui font-medium text-ink-soft">
            经手记录
            <span className="ml-1 text-xs font-normal text-ink-faint">{history.length} 条</span>
          </p>
          <ol className="space-y-1.5">
            {history.map((h) => {
              const meta = ACTION_META[h.action]
              const actor = shortEmail(h.actor_email)
              const to = shortEmail(h.to_assignee_email)
              const from = shortEmail(h.from_assignee_email)
              return (
                <li
                  key={h.id}
                  className="flex items-start gap-2 rounded-lg border border-line/70 bg-surface px-2.5 py-2"
                >
                  <Badge tone={meta.tone}>{meta.label}</Badge>
                  <div className="min-w-0 flex-1">
                    <p className="text-ui text-ink-soft">
                      <span className="font-medium text-ink">{actor}</span>
                      {h.action === 'dispatch' && <> 派给 {to}</>}
                      {h.action === 'reassign' && <> 转交给 {to}</>}
                      {h.action === 'accept' && <> 接收了这件需求</>}
                      {h.action === 'reject' && <> 退回了这件需求</>}
                      {h.action === 'reassign' && h.from_assignee_email && (
                        <span className="text-ink-faint">（原 {from}）</span>
                      )}
                    </p>
                    {h.reason && <p className="mt-0.5 text-xs text-ink-muted">原因：{h.reason}</p>}
                  </div>
                  <time className="shrink-0 text-micro tabular-nums text-ink-faint">
                    {new Date(h.created_at).toLocaleString('zh-CN', {
                      month: 'numeric',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </time>
                </li>
              )
            })}
          </ol>
        </div>
      )}
    </div>
  )
}
