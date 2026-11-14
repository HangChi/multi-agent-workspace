/** 登录页（T025）：邮箱 + 密码，字段级校验与密码可见切换。 */

import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { extractError } from '../api/client'
import { AuthShell } from '../components/layout/AuthShell'
import { Button, IconButton } from '../components/ui/Button'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Input } from '../components/ui/Field'
import { useAuth } from '../hooks/useAuth'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function LoginPage() {
  const navigate = useNavigate()
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({})
  const [loading, setLoading] = useState(false)

  const validate = () => {
    const next: { email?: string; password?: string } = {}
    if (!email.trim()) next.email = '请输入邮箱'
    else if (!EMAIL_RE.test(email.trim())) next.email = '邮箱格式不正确'
    if (!password) next.password = '请输入密码'
    setFieldErrors(next)
    return Object.keys(next).length === 0
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!validate()) return
    setLoading(true)
    try {
      await login(email.trim(), password)
      navigate('/', { replace: true })
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
          label="邮箱"
          type="email"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value)
            if (fieldErrors.email) setFieldErrors((v) => ({ ...v, email: undefined }))
          }}
          error={fieldErrors.email}
          leftIcon="mail"
          placeholder="you@example.com"
          autoComplete="email"
          autoFocus
        />

        <Input
          label="密码"
          type={showPassword ? 'text' : 'password'}
          value={password}
          onChange={(e) => {
            setPassword(e.target.value)
            if (fieldErrors.password) setFieldErrors((v) => ({ ...v, password: undefined }))
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
