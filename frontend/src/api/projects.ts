import { apiClient } from './client'

// ---------- 项目（008）----------

/** 项目：需求的业务容器。创建者为 owner；成员与共享由 010 补齐（两层权限，无 role 列）。 */
export interface Project {
  id: string
  owner_id: string
  name: string
  description: string | null
  status: 'active' | 'archived'
  /** 该项目下、当前用户可见范围内的需求数（我创建的 + 分派给我的） */
  request_count: number
  /** 010：成员数（含 owner——「这个项目有几个人」的自然口径） */
  member_count: number
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
  /** 页码；不传 = 要全量（看板/表单的项目下拉依赖全量选项） */
  page?: number
  page_size?: number
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

/** 移交入参（017）：目标必须是项目现有成员。 */
export interface ProjectTransferRequest {
  new_owner_id: string
  /** 默认 true：移交后我仍是成员，可继续查看（离职场景显式传 false）。 */
  keep_as_member?: boolean
}

/**
 * 移交项目所有权（017）：200 + ProjectOut（owner_id 已是新值）。
 *
 * 为什么不用 updateProject 改 owner_id：移交要同时增删成员行、发通知、
 * 写独立审计，且 409 有四种语义（自移交/非成员/只读/同名）——
 * 独立端点才能让每种冲突给出可读提示（spec research R-01）。
 */
export async function transferProject(
  id: string,
  payload: ProjectTransferRequest
): Promise<Project> {
  const { data } = await apiClient.post<Project>(`/projects/${id}/transfer`, payload)
  return data
}
