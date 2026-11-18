import { apiClient } from './client'

/** API 端点封装（auth 部分，T025）。 */

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

/**
 * 刷新 token：refresh 在 HttpOnly cookie 里，所以**不能发空对象**。
 *
 * 后端的 body 是 `RefreshRequest | None`，`refresh_token` 为必填字段：
 * 传 `{}` 会被 FastAPI 判为校验失败直接 422（根本走不到读 cookie 的分支），
 * 表现为「登录成功、刷新页面就掉线」。必须不带 body 才是 cookie 分支。
 */
export async function refreshToken(): Promise<TokenPair> {
  const { data } = await apiClient.post<{ tokens: TokenPair }>('/auth/refresh')
  return data.tokens
}

/** 登出：服务端撤销 refresh 并清 cookie（同样不能带空 body，否则 422）。 */
export async function logout(): Promise<void> {
  await apiClient.post('/auth/logout')
}

/** 当前用户。 */
export async function fetchMe(): Promise<UserOut> {
  const { data } = await apiClient.get<UserOut>('/auth/me')
  return data
}
