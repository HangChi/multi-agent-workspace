/** 需求卡片（T047）：看板上的最小单元。 */

import { WorkRequest, attachmentDownloadUrl } from '../../api/endpoints'

const PRIORITY_STYLE: Record<string, string> = {
  high: 'bg-red-50 text-red-700 border-red-200',
  normal: 'bg-blue-50 text-blue-700 border-blue-200',
  low: 'bg-slate-50 text-slate-500 border-slate-200',
}

const PRIORITY_LABEL: Record<string, string> = {
  high: '高',
  normal: '中',
  low: '低',
}

const STATUS_LABEL: Record<string, string> = {
  created: '待分配',
  assigned: '已分配',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

interface Props {
  request: WorkRequest
}

export function RequestCard({ request }: Props) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm transition-shadow hover:shadow">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span
          className={`rounded-full border px-2 py-0.5 text-xs font-medium ${PRIORITY_STYLE[request.priority]}`}
        >
          {PRIORITY_LABEL[request.priority]}
        </span>
        <span className="text-xs text-slate-400">
          {new Date(request.created_at).toLocaleDateString('zh-CN')}
        </span>
      </div>
      <h4 className="text-sm font-semibold text-slate-800">{request.title}</h4>
      {request.description && (
        <p className="mt-1 line-clamp-2 text-xs text-slate-500">{request.description}</p>
      )}
      <div className="mt-2 flex items-center gap-2">
        {request.attachments.length > 0 && (
          <span className="text-xs text-slate-400">
            📎 {request.attachments.length}
          </span>
        )}
        <span className="text-xs text-slate-400">{STATUS_LABEL[request.status]}</span>
      </div>
      {request.attachments.length > 0 && (
        <div className="mt-2 space-y-1">
          {request.attachments.map((att) => (
            <a
              key={att.key}
              href={attachmentDownloadUrl(request.id, att.key)}
              download={att.name}
              className="block truncate rounded bg-slate-50 px-2 py-1 text-xs text-blue-600 hover:bg-blue-50"
            >
              {att.name}（{(att.size / 1024).toFixed(1)} KB）
            </a>
          ))}
        </div>
      )}
    </div>
  )
}
