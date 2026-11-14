/** 认证 Hook（T025）：暴露登录态与便捷操作。 */

import { useAuthStore } from '../stores/authStore'

export function useAuth() {
  const user = useAuthStore((s) => s.user)
  const accessToken = useAuthStore((s) => s.accessToken)
  const initialized = useAuthStore((s) => s.initialized)
  const login = useAuthStore((s) => s.login)
  const register = useAuthStore((s) => s.register)
  const logout = useAuthStore((s) => s.logout)

  return {
    user,
    accessToken,
    initialized,
    isAuthenticated: !!accessToken && !!user,
    isAdmin: user?.role === 'admin',
    isMember: user?.role === 'admin' || user?.role === 'member',
    login,
    register,
    logout,
  }
}
