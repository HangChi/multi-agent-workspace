/**
 * 组织架构（020）：部门树 + 分派范围 + 人员归属。
 *
 * 一页做完三件事，因为这三点互相依赖：不给部门配人，范围策略就是空的；
 * 不设范围，部门只是个标签。
 *
 * 界面上两个刻意的提示：
 * 1. 「未归属部门的成员不受分派范围限制」——升级后默认不收紧，不说清会有
 *    管理员以为自己配好了却还能被乱派；
 * 2. 删部门会连子树一起删、成员变「未归属」——不给这句，一次误点就是一场事故。
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../api/client'
import {
  createDepartment,
  deleteDepartment,
  fetchDepartmentMembers,
  fetchDepartments,
  SCOPE_LABEL,
  setUserDepartment,
  updateDepartment,
} from '../api/endpoints'
import type { AssignScope, Department } from '../api/endpoints'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Input, Select } from '../components/ui/Field'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { useConfirm } from '../components/ui/useConfirm'
import { useToast } from '../components/ui/useToast'

const SCOPE_TONE = { all: 'slate', subtree: 'sky', self: 'amber' } as const

export function OrganizationPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [parentId, setParentId] = useState('')
  const [scope, setScope] = useState<AssignScope>('all')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')

  const {
    data: departments = [],
    isLoading,
    refetch,
  } = useQuery({ queryKey: ['departments'], queryFn: fetchDepartments })
  const { data: members = [] } = useQuery({
    queryKey: ['departmentMembers'],
    queryFn: fetchDepartmentMembers,
  })

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['departments'] })
    void qc.invalidateQueries({ queryKey: ['departmentMembers'] })
    // 分派人选受组织范围影响，改完组织要重拉
    void qc.invalidateQueries({ queryKey: ['users', 'options'] })
  }

  const createMut = useMutation({
    mutationFn: () =>
      createDepartment({
        name,
        parent_id: parentId || null,
        assign_scope: scope,
      }),
    onSuccess: () => {
      setName('')
      setParentId('')
      setScope('all')
      toast.success('已创建部门')
      invalidate()
    },
    onError: (err) => setError(extractError(err)),
  })

  const renameMut = useMutation({
    mutationFn: (id: string) => updateDepartment(id, { name: editingName }),
    onSuccess: () => {
      setEditingId(null)
      toast.success('已改名')
      invalidate()
    },
    onError: (err) => setError(extractError(err)),
  })

  const scopeMut = useMutation({
    mutationFn: ({ id, assign_scope }: { id: string; assign_scope: AssignScope }) =>
      updateDepartment(id, { assign_scope }),
    onSuccess: () => {
      toast.success('已更新分派范围')
      invalidate()
    },
    onError: (err) => setError(extractError(err)),
  })

  const removeMut = useMutation({
    mutationFn: (id: string) => deleteDepartment(id),
    onSuccess: () => {
      toast.success('已删除部门（成员变为未归属）')
      invalidate()
    },
    onError: (err) => setError(extractError(err)),
  })

  const assignMut = useMutation({
    mutationFn: ({ userId, departmentId }: { userId: string; departmentId: string | null }) =>
      setUserDepartment(userId, departmentId),
    onSuccess: () => {
      toast.success('已更新人员归属')
      invalidate()
    },
    onError: (err) => setError(extractError(err)),
  })

  async function onRemove(dept: Department) {
    const ok = await confirm({
      title: `删除部门「${dept.name}」？`,
      description:
        '该部门及其所有下级部门会被删除；里面的成员会变成「未归属」，账号与需求不受影响。此操作不可撤销。',
      tone: 'warning',
      confirmText: '删除部门',
    })
    if (ok) removeMut.mutate(dept.id)
  }

  return (
    <Page>
      <PageHeader
        title="组织架构"
        description="部门树 + 分派范围：决定「谁能把需求派给谁」。未归属部门的成员不受限制（便于渐进启用）。"
      />

      {error && (
        <ErrorBanner error={error} onDismiss={() => setError(null)} className="mt-4" />
      )}

      <Card className="mt-4" padding="md">
        <p className="mb-3 text-ui font-medium text-ink-soft">新建部门</p>
        <div className="flex flex-wrap items-end gap-2">
          <Input
            label="部门名"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：平台组"
            wrapperClassName="w-56"
          />
          <Select
            label="上级部门"
            value={parentId}
            onChange={(e) => setParentId(e.target.value)}
            wrapperClassName="w-56"
          >
            <option value="">（顶层）</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {'　'.repeat(d.depth)}
                {d.name}
              </option>
            ))}
          </Select>
          <Select
            label="分派范围"
            value={scope}
            onChange={(e) => setScope(e.target.value as AssignScope)}
            wrapperClassName="w-48"
          >
            <option value="all">全公司</option>
            <option value="subtree">本部门及下级</option>
            <option value="self">仅本部门</option>
          </Select>
          <Button loading={createMut.isPending} disabled={!name.trim()} onClick={() => createMut.mutate()}>
            创建
          </Button>
        </div>
      </Card>

      <Card className="mt-3" padding="none">
        {isLoading ? (
          <p className="p-4 text-ui text-ink-faint">加载中…</p>
        ) : departments.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon="users"
              title="还没有部门"
              description="组织架构是可选的：不建部门时，所有人都能互相分派（与升级前一致）。建了部门并设置范围后才会收紧。"
            />
          </div>
        ) : (
          <ul className="divide-y divide-line-subtle">
            {departments.map((dept) => (
              <li key={dept.id} className="flex flex-wrap items-center gap-2 px-4 py-2.5">
                <span style={{ paddingLeft: dept.depth * 16 }} className="min-w-0 flex-1">
                  {editingId === dept.id ? (
                    <span className="flex items-center gap-2">
                      <Input
                        value={editingName}
                        onChange={(e) => setEditingName(e.target.value)}
                        controlSize="sm"
                        aria-label={`改名 ${dept.name}`}
                        wrapperClassName="w-48"
                      />
                      <Button
                        size="sm"
                        loading={renameMut.isPending}
                        onClick={() => renameMut.mutate(dept.id)}
                      >
                        保存
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                        取消
                      </Button>
                    </span>
                  ) : (
                    <span className="flex items-center gap-2">
                      <span className="truncate text-ui font-medium text-ink">{dept.name}</span>
                      <Badge tone={SCOPE_TONE[dept.assign_scope]}>
                        {SCOPE_LABEL[dept.assign_scope]}
                      </Badge>
                      <span className="text-xs text-ink-faint">{dept.member_count} 人</span>
                    </span>
                  )}
                </span>

                {editingId !== dept.id && (
                  <span className="flex shrink-0 items-center gap-1">
                    <Select
                      value={dept.assign_scope}
                      onChange={(e) =>
                        scopeMut.mutate({
                          id: dept.id,
                          assign_scope: e.target.value as AssignScope,
                        })
                      }
                      controlSize="sm"
                      aria-label={`${dept.name} 的分派范围`}
                      wrapperClassName="w-36"
                    >
                      <option value="all">全公司</option>
                      <option value="subtree">本部门及下级</option>
                      <option value="self">仅本部门</option>
                    </Select>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setEditingId(dept.id)
                        setEditingName(dept.name)
                      }}
                    >
                      改名
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      tone="danger"
                      leftIcon="trash"
                      onClick={() => void onRemove(dept)}
                    >
                      删除
                    </Button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="mt-3" padding="md">
        <p className="mb-1 text-ui font-medium text-ink-soft">人员归属</p>
        <p className="mb-3 text-xs text-ink-faint">
          未归属部门的成员不受分派范围限制——要收紧，先把人归档。
        </p>
        <ul className="max-h-96 space-y-1.5 overflow-auto">
          {members.map((m) => (
            <li
              key={m.user_id}
              className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-1.5"
            >
              <span className="min-w-0 flex-1 truncate text-ui text-ink-soft">{m.email}</span>
              <Select
                value={m.department_id ?? ''}
                onChange={(e) =>
                  assignMut.mutate({
                    userId: m.user_id,
                    departmentId: e.target.value || null,
                  })
                }
                controlSize="sm"
                aria-label={`${m.email} 的部门`}
                wrapperClassName="w-52"
              >
                <option value="">未归属</option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {'　'.repeat(d.depth)}
                    {d.name}
                  </option>
                ))}
              </Select>
            </li>
          ))}
        </ul>
        <div className="mt-2 text-right">
          <Button variant="ghost" size="sm" onClick={() => void refetch()}>
            刷新
          </Button>
        </div>
      </Card>
    </Page>
  )
}
