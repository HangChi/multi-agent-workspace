import { useCallback, useEffect, useState } from 'react'

import {
  createAgentEgressRule,
  deleteAgentEgressRule,
  fetchAgentEgressRules,
  type EgressRule,
} from '../../api/endpoints'
import { Button } from '../ui/Button'
import { Input } from '../ui/Field'

export function EgressRules({ agentId, mode }: { agentId: string; mode: 'default' | 'restricted' }) {
  const [rules, setRules] = useState<EgressRule[]>([])
  const [target, setTarget] = useState('')
  const [port, setPort] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await fetchAgentEgressRules(agentId)
      setRules(data.items)
    } catch {
      setError('出站规则加载失败。')
    }
  }, [agentId])

  useEffect(() => { void load() }, [load])

  const add = async () => {
    const value = target.trim().toLowerCase()
    if (!value) return
    setBusy(true)
    setError(null)
    try {
      await createAgentEgressRule(agentId, {
        target_type: value.includes('/') ? 'cidr' : 'domain',
        target: value,
        port_range: port.trim() || null,
      })
      setTarget('')
      setPort('')
      await load()
    } catch {
      setError('规则保存失败，请检查域名、网段或端口范围。')
    } finally { setBusy(false) }
  }

  const remove = async (ruleId: string) => {
    setBusy(true)
    setError(null)
    try {
      await deleteAgentEgressRule(agentId, ruleId)
      await load()
    } catch {
      setError('规则删除失败。')
    } finally { setBusy(false) }
  }

  return (
    <section className="space-y-3 rounded-xl border border-line bg-surface-muted/50 p-4">
      <div>
        <h3 className="text-sm font-medium text-ink">Agent 出站允许清单</h3>
        <p className="mt-1 text-xs text-ink-muted">
          {mode === 'restricted' ? '限制模式下，仅允许清单内的目标出网。' : '默认模式下规则仅用于预设；全局内网与云元数据黑名单不可绕过。'}
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-[1fr_130px_auto]">
        <Input aria-label="域名或网段" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="例如 pypi.org 或 203.0.113.0/24" />
        <Input aria-label="端口范围" value={port} onChange={(e) => setPort(e.target.value)} placeholder="端口（可选）" />
        <Button size="sm" variant="secondary" loading={busy} onClick={() => void add()}>添加规则</Button>
      </div>
      {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
      {rules.length > 0 ? (
        <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
          {rules.map((rule) => (
            <li key={rule.id} className="flex items-center gap-3 px-3 py-2 text-xs">
              <span className="min-w-0 flex-1 truncate font-mono text-ink" title={rule.target}>{rule.target}</span>
              <span className="text-ink-faint">{rule.port_range ? `端口 ${rule.port_range}` : '全部端口'}</span>
              <button type="button" disabled={busy} onClick={() => void remove(rule.id)} className="min-h-11 px-2 text-rose-600 hover:underline">删除</button>
            </li>
          ))}
        </ul>
      ) : <p className="text-xs text-ink-faint">尚未配置允许规则。</p>}
    </section>
  )
}
