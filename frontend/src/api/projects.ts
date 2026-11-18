import { apiClient } from './client'

// ---------- 项目（008）----------

/** 项目：需求的业务容器。单人归属，无成员（research R-01）。 */
export interface Project {
  id: string
  owner_id: string
  name: string
  description: string | null
  status: 'active' | 'archived'
  /** 该项目下、当前用户可见范围内的需求数（我创建的 + 分派给我的） */
  request_count: number
  created_at: string
  updated_at: string
}

export interface ProjectCreateRequest {
  name: string
  description?: string | null
}

export interface ProjectUpdateRequest {
  name?: string
  description?: string | null
  status?: 'active' | 'archived'
}

export interface ProjectList {
  items: Project[]
  total: number
}

/**
 * 项目列表。
 * scope=mine（默认，管理页）：我创建的项目；
 * scope=relevant（看板筛选下拉）：我创建的 + 派给我需求所属的项目
 * （接收人的下拉必须含「派给他那件活所属的项目」）。
 */
export async function fetchProjects(params?: {
  scope?: 'mine' | 'relevant'
}): Promise<ProjectList> {
  const { data } = await apiClient.get<ProjectList>('/projects', { params })
  return data
}

/** 项目详情。 */
export async function fetchProject(id: string): Promise<Project> {
  const { data } = await apiClient.get<Project>(`/projects/${id}`)
  return data
}

/** 建项目（同用户名下唯一，含已归档项目也占名字 → 409）。 */
export async function createProject(payload: ProjectCreateRequest): Promise<Project> {
  const { data } = await apiClient.post<Project>('/projects', payload)
  return data
}

/** 改项目（改名同事务同步需求的 project_name 快照）。 */
export async function updateProject(id: string, payload: ProjectUpdateRequest): Promise<Project> {
  const { data } = await apiClient.patch<Project>(`/projects/${id}`, payload)
  return data
}

/** 删项目：其下需求保留、归属清空（204）。 */
export async function deleteProject(id: string): Promise<void> {
  await apiClient.delete(`/projects/${id}`)
}
