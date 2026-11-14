/** API 端点封装（auth 部分，T025）。 */

import { apiClient } from './client'

export interface UserOut {
  id: string
  email: string
  role: 'admin' | 'member' | 'viewer'
  status: string
}

export interface TokenPair {
  access: string
  refresh: string
}

export interface RegisterRequest {
  email: string
  password: string
  invite_code: string
}

export interface LoginRequest {
  email: string
  password: string
}

export interface AuthResponse {
  user: UserOut
  tokens: TokenPair
}

/** 注册（邀请码）。 */
export async function register(payload: RegisterRequest): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>('/auth/register', payload)
  return data
}

/** 登录。 */
export async function login(payload: LoginRequest): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>('/auth/login', payload)
  return data
}

/** 刷新 token。 */
export async function refreshToken(refresh: string): Promise<TokenPair> {
  const { data } = await apiClient.post<{ tokens: TokenPair }>('/auth/refresh', {
    refresh_token: refresh,
  })
  return data.tokens
}

/** 当前用户。 */
export async function fetchMe(): Promise<UserOut> {
  const { data } = await apiClient.get<UserOut>('/auth/me')
  return data
}
