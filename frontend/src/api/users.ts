import { apiClient } from './client'

// ---------- 人员选择（007 US1）----------

/** 在职用户的最小投影（分派选人用；后端只返回在职）。 */
export interface UserOption {
  id: string
  email: string
  role: 'admin' | 'member' | 'viewer'
}

export interface UserOptionList {
  items: UserOption[]
  total: number
}

/** 在职用户列表（分派落点；viewer 403）。 */
export async function fetchUserOptions(): Promise<UserOptionList> {
  const { data } = await apiClient.get<UserOptionList>('/users/options')
  return data
}
