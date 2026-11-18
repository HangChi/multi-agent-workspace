/**
 * 登录页（T025）：邮箱 + 密码，字段级校验与密码可见切换。
 *
 * 细节约定：
 * - noValidate 统一走自定义校验路径（浏览器原生气泡语言随 locale 漂移）；
 * - 校验失败聚焦第一个出错字段（Geist/shadcn 表单惯例），读屏用户不必自己找；
 * - 触屏设备不 autoFocus（一进页面就弹键盘会把标题顶出首屏）；
 * - 受保护深链登录后回跳原目标（守卫在 location.state.from 里带了地址）。
 */

import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { extractError } from '../api/client'
import { AuthShell } from '../components/layout/AuthShell'
import { Button, IconButton } from '../components/ui/Button'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Input } from '../components/ui/Field'
import { useAuth } from '../hooks/useAuth'
import { isEmail } from '../utils/validation'

/** 触屏设备进页面就弹键盘是负体验；精确指针（鼠标/触控板）才自动聚焦。 */
const FINE_POINTER =
  typeof window !== 'undefined' && window.matchMedia('(pointer: fine)').matches

interface FieldErrors {
  email?: string
  password?: string
}

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [loading, setLoading] = useState(false)

  /** 深链回跳：未登录访问受保护页时，守卫把原地址放在 state.from。 */
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname

  const clear = (key: keyof FieldErrors) =>
    setFieldErrors((v) => (v[key] ? { ...v, [key]: undefined } : v))

  const validate = () => {
    const next: FieldErrors = {}
    if (!email.trim()) next.email = '请输入邮箱'
    else if (!isEmail(email)) next.email = '邮箱格式不正确'
    if (!password) next.password = '请输入密码'
    setFieldErrors(next)
    const firstKey = (['email', 'password'] as const).find((k) => next[k])
    if (firstKey) document.getElementById(`login-${firstKey}`)?.focus()
    return !firstKey
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!validate()) return
    setLoading(true)
    try {
      await login(email.trim(), password)
      navigate(from ?? '/', { replace: true })
    } catch (err) {
      setError(extractError(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthShell
      title="登录工作台"
      subtitle="使用你的企业邮箱登录，继续管理需求与 Agent。"
      footer={
        <>
          还没有账号？{' '}
          <Link
            to="/register"
            className="font-medium text-accent underline-offset-4 hover:text-accent hover:underline"
          >
            使用邀请码注册
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        <Input
          id="login-email"
          label="邮箱"
          type="email"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value)
            clear('email')
          }}
          error={fieldErrors.email}
          leftIcon="mail"
          placeholder="you@example.com"
          autoComplete="email"
          autoFocus={FINE_POINTER}
        />

        <Input
          id="login-password"
          label="密码"
          type={showPassword ? 'text' : 'password'}
          value={password}
          onChange={(e) => {
            setPassword(e.target.value)
            clear('password')
          }}
          error={fieldErrors.password}
          leftIcon="lock"
          placeholder="请输入密码"
          autoComplete="current-password"
          rightSlot={
            <IconButton
              icon={showPassword ? 'eye-off' : 'eye'}
              label={showPassword ? '隐藏密码' : '显示密码'}
              size="sm"
              aria-pressed={showPassword}
              onClick={() => setShowPassword((v) => !v)}
            />
          }
        />

        {error && <ErrorBanner error={error} onDismiss={() => setError(null)} className="mt-0" />}

        <Button
          type="submit"
          variant="primary"
          size="lg"
          fullWidth
          loading={loading}
          className="mt-2"
        >
          {loading ? '登录中…' : '登录'}
        </Button>
      </form>
    </AuthShell>
  )
}
