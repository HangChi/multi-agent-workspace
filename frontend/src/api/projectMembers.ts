import { apiClient } from './client'

// ---------- 项目成员（010）----------

/** 成员列表的一项（owner 也在列表里，is_owner 标记区分）。 */
export interface ProjectMember {
  user_id: string
  user_email: string | null
  /** True = 项目创建者（不在 project_members 表里，后端合成） */
  is_owner: boolean
  joined_at: string
}

export interface ProjectMemberList {
  items: ProjectMember[]
  total: number
}

/** 加成员（owner 特权；被加者收到通知）。 */
export async function addProjectMember(projectId: string, userId: string): Promise<ProjectMember> {
  const { data } = await apiClient.post<ProjectMember>(`/projects/${projectId}/members`, {
    user_id: userId,
  })
  return data
}

/** 移除成员（owner 特权；不能移除自己）。 */
export async function removeProjectMember(projectId: string, userId: string): Promise<void> {
  await apiClient.delete(`/projects/${projectId}/members/${userId}`)
}

/** 成员列表（owner + 成员；owner ∪ 成员可读）。 */
export async function fetchProjectMembers(projectId: string): Promise<ProjectMemberList> {
  const { data } = await apiClient.get<ProjectMemberList>(`/projects/${projectId}/members`)
  return data
}
