import { apiClient } from './client'

// ---------- 组织架构（020）----------

export type AssignScope = 'all' | 'subtree' | 'self'

export interface Department {
  id: string
  name: string
  parent_id: string | null
  /** 物化路径 `/root/child/`：按它排序就是树的先序 */
  path: string
  depth: number
  assign_scope: AssignScope
  member_count: number
  created_at: string
}

export interface DepartmentMemberRow {
  user_id: string
  email: string
  department_id: string | null
}

export async function fetchDepartments(): Promise<Department[]> {
  const { data } = await apiClient.get<{ items: Department[]; total: number }>('/departments')
  return data.items
}

export async function createDepartment(payload: {
  name: string
  parent_id?: string | null
  assign_scope?: AssignScope
}): Promise<Department> {
  const { data } = await apiClient.post<Department>('/departments', payload)
  return data
}

export async function updateDepartment(
  id: string,
  payload: { name?: string; assign_scope?: AssignScope }
): Promise<Department> {
  const { data } = await apiClient.patch<Department>(`/departments/${id}`, payload)
  return data
}

export async function deleteDepartment(id: string): Promise<void> {
  await apiClient.delete(`/departments/${id}`)
}

export async function fetchDepartmentMembers(): Promise<DepartmentMemberRow[]> {
  const { data } = await apiClient.get<{ items: DepartmentMemberRow[] }>(
    '/departments/members'
  )
  return data.items
}

/** 把用户放进部门；null = 移出（未归属）。 */
export async function setUserDepartment(
  userId: string,
  departmentId: string | null
): Promise<void> {
  await apiClient.put(`/departments/users/${userId}/department`, {
    department_id: departmentId,
  })
}

export const SCOPE_LABEL: Record<AssignScope, string> = {
  all: '全公司',
  subtree: '本部门及下级',
  self: '仅本部门',
}
