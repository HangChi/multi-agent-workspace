/** 注册页（T025）：邀请码 + 邮箱 + 密码，含密码强度提示。 */

import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { extractError } from '../api/client'
import { AuthShell } from '../components/layout/AuthShell'
import { Button, IconButton } from '../components/ui/Button'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Input } from '../components/ui/Field'
import { useAuth } from '../hooks/useAuth'
import { cn } from '../components/ui/cn'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const STRENGTH_STYLE = ['bg-line', 'bg-rose-400', 'bg-amber-400', 'bg-emerald-500']
const STRENGTH_LABEL = ['', '偏弱', '中等', '较强']
const STRENGTH_TEXT = ['', 'text-rose-600', 'text-amber-600', 'text-emerald-600']

/** 0~3 分：长度、字母+数字组合、额外复杂度。 */
function scorePassword(pwd: string): number {
  if (!pwd) return 0
  let score = 0
  if (pwd.length >= 8) score += 1
  if (/[a-zA-Z]/.test(pwd) && /\d/.test(pwd)) score += 1
  if (pwd.length >= 12 || /[^a-zA-Z0-9]/.test(pwd)) score += 1
  return score
}

interface FieldErrors {
  email?: string
  password?: string
  invite?: string
}

export function RegisterPage() {
  const navigate = useNavigate()
  const { register } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [loading, setLoading] = useState(false)

  const strength = scorePassword(password)
  const clear = (key: keyof FieldErrors) =>
    setFieldErrors((v) => (v[key] ? { ...v, [key]: undefined } : v))

  const validate = () => {
    const next: FieldErrors = {}
    if (!email.trim()) next.email = '请输入邮箱'
    else if (!EMAIL_RE.test(email.trim())) next.email = '邮箱格式不正确'

    if (!password) next.password = '请设置密码'
    else if (password.length < 8) next.password = '密码至少 8 位'
    else if (!(/[a-zA-Z]/.test(password) && /\d/.test(password)))
      next.password = '密码需同时包含字母和数字'

    if (!inviteCode.trim()) next.invite = '请输入邀请码'

    setFieldErrors(next)
    return Object.keys(next).length === 0
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!validate()) return
    setLoading(true)
    try {
      await register({ email: email.trim(), password, invite_code: inviteCode.trim() })
      navigate('/', { replace: true })
    } catch (err) {
      setError(extractError(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthShell
      title="创建账号"
      subtitle="注册需要管理员发放的邀请码，开通后即可加入团队工作台。"
      footer={
        <>
          已有账号？{' '}
          <Link
            to="/login"
            className="font-medium text-accent underline-offset-4 hover:text-accent hover:underline"
          >
            去登录
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
            clear('email')
          }}
          error={fieldErrors.email}
          leftIcon="mail"
          placeholder="you@example.com"
          autoComplete="email"
          autoFocus
        />

        <div>
          <Input
            label="密码"
            type={showPassword ? 'text' : 'password'}
            value={password}
            onChange={(e) => {
              setPassword(e.target.value)
              clear('password')
            }}
            error={fieldErrors.password}
            leftIcon="lock"
            placeholder="至少 8 位，含字母与数字"
            autoComplete="new-password"
            rightSlot={
              <IconButton
                icon={showPassword ? 'eye-off' : 'eye'}
                label={showPassword ? '隐藏密码' : '显示密码'}
                size="sm"
                onClick={() => setShowPassword((v) => !v)}
              />
            }
          />

          {/* 强度条：长度/组合达标才点亮，给用户即时反馈 */}
          {password.length > 0 && (
            <div className="mt-2 flex items-center gap-2">
              <div className="flex flex-1 gap-1">
                {[1, 2, 3].map((i) => (
                  <span
                    key={i}
                    className={cn(
                      'h-1 flex-1 rounded-full transition-colors duration-200',
                      i <= strength ? STRENGTH_STYLE[strength] : 'bg-line',
                    )}
                  />
                ))}
              </div>
              <span className={cn('text-2xs font-medium', STRENGTH_TEXT[strength])}>
                {STRENGTH_LABEL[strength]}
              </span>
            </div>
          )}
        </div>

        <Input
          label="邀请码"
          value={inviteCode}
          onChange={(e) => {
            setInviteCode(e.target.value.toUpperCase())
            clear('invite')
          }}
          error={fieldErrors.invite}
          leftIcon="key"
          placeholder="ABC-123-XYZ"
          hint="邀请码由管理员发放，请与发放内容保持一致"
          autoComplete="off"
          spellCheck={false}
          className="font-mono uppercase tracking-[0.12em]"
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
          {loading ? '注册中…' : '创建账号'}
        </Button>

        <p className="text-center text-xs leading-relaxed text-ink-faint">
          注册即表示同意团队内部使用规范，账号仅限本人使用。
        </p>
      </form>
    </AuthShell>
  )
}
