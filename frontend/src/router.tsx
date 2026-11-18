/** 路由与守卫（T026）：登录守卫 + 角色守卫 + 工作台路由（T037/T038）。 */

import { Suspense, lazy, useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { AppLayout } from './components/layout/AppLayout'
import { SettingsLayout } from './components/layout/SettingsLayout'
import { Button } from './components/ui/Button'
import { EmptyState } from './components/ui/EmptyState'
import { Icon } from './components/ui/Icons'
import { LogoMark } from './components/ui/Logo'
import { Spinner } from './components/ui/Spinner'
import { useAuth } from './hooks/useAuth'

/**
 * 路由级代码分割。
 *
 * 此前所有页面都静态 import，单个 480KB / gzip 153KB 的包必须整包下载才能
 * 首屏可见。拆开后首屏只需要「登录/布局/看板」这条主路径，其余按需拉取。
 *
 * 特别注意 BoardPage：它引入了 @dnd-kit（约 113KB 源码，占全包 8.8%），
 * 而登录页完全用不到 —— 不拆的话未登录用户也要付这份下载代价。
 *
 * ⚠️ React.lazy 的默认导出要求：用 `.then(m => ({ default: m.XxxPage }))`
 * 适配命名导出，别为了偷懒去改各页面的导出形式。
 */
const LoginPage = lazy(() => import('./pages/LoginPage').then((m) => ({ default: m.LoginPage })))
const RegisterPage = lazy(() =>
  import('./pages/RegisterPage').then((m) => ({ default: m.RegisterPage }))
)
const BoardPage = lazy(() => import('./pages/BoardPage').then((m) => ({ default: m.BoardPage })))
const DashboardPage = lazy(() =>
  import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage }))
)
const AgentPage = lazy(() => import('./pages/AgentPage').then((m) => ({ default: m.AgentPage })))
/**
 * 模板管理页（005）。
 *
 * 单独分包而不是并进 AgentPage：它带一套模板表单 + 两段列表，
 * 且 viewer 永远进不来（模板端点一律 403），不该为它付这份下载代价。
 */
const AgentTemplatesPage = lazy(() =>
  import('./pages/AgentTemplatesPage').then((m) => ({ default: m.AgentTemplatesPage }))
)
const GroupPage = lazy(() => import('./pages/GroupPage').then((m) => ({ default: m.GroupPage })))
/**
 * 调度页（004）。
 *
 * 单独分包而不是并进 Board：它带一个 cron 预览、一个历史抽屉和一套表单，
 * 只在真正打开时才值得下载。viewer 永远进不来，也就不该为它付这份体积。
 */
const SchedulesPage = lazy(() =>
  import('./pages/SchedulesPage').then((m) => ({ default: m.SchedulesPage }))
)
const MarketplacePage = lazy(() =>
  import('./pages/MarketplacePage').then((m) => ({ default: m.MarketplacePage }))
)
/**
 * 项目管理页（008）。
 *
 * 单独分包：它是一个独立的管理面（列表 + 表单 + 确认框），看板用户不必
 * 为它付体积；viewer 进不来（后端 403，页面直接渲染 403 态）。
 */
const ProjectsPage = lazy(() =>
  import('./pages/ProjectsPage').then((m) => ({ default: m.ProjectsPage }))
)
const RunDetailPage = lazy(() =>
  import('./pages/RunDetailPage').then((m) => ({ default: m.RunDetailPage }))
)
const AdminPage = lazy(() => import('./pages/AdminPage').then((m) => ({ default: m.AdminPage })))
const SettingsPage = lazy(() =>
  import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage }))
)

/** 全屏加载：恢复会话期间别让用户看到白屏。 */
function FullPageLoading() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-canvas">
      <LogoMark size={44} />
      <div className="flex items-center gap-2 text-sm text-ink-muted">
        <Spinner className="h-3.5 w-3.5 text-brand-500" />
        正在恢复会话…
      </div>
    </div>
  )
}

/**
 * 懒加载页面加载态。
 *
 * 用 min-h 撑住高度而不是只放个尾巴 spinner：否则内容区会从 0 高度跳到
 * 满屏，滚动条闪一下，体感比等待本身更差。
 */
function PageLoading() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3">
      <Spinner className="h-5 w-5 text-brand-500" />
      <p className="text-sm text-ink-muted">加载中…</p>
    </div>
  )
}

/**
 * 按路由更新 document.title。
 *
 * 多标签场景与读屏用户靠标题分辨当前页面；静态 SPA 全程一个标题
 * 是主流框架（Next/Remix）默认就处理好的事，这里补上。
 */
const ROUTE_TITLES: [RegExp, string][] = [
  [/^\/login$/, '登录'],
  [/^\/register$/, '注册'],
  [/^\/runs\//, '运行详情'],
  [/^\/dashboard$/, '仪表盘'],
  [/^\/agents$/, 'Agent 管理'],
  [/^\/groups$/, 'Agent 组'],
  [/^\/projects$/, '项目管理'],
  [/^\/settings\/general$/, '通用设置'],
  [/^\/settings\/templates$/, 'Agent 模板'],
  [/^\/settings\/schedules$/, '定时调度'],
  [/^\/settings\/marketplace$/, '工具市场'],
  [/^\/settings\/admin$/, '管理后台'],
  [/^\/settings$/, '设置'],
  [/^\/$/, '需求看板'],
]

function titleForPath(pathname: string): string {
  return ROUTE_TITLES.find(([re]) => re.test(pathname))?.[1] ?? '页面不存在'
}

function useDocumentTitle() {
  const { pathname } = useLocation()
  useEffect(() => {
    document.title = `${titleForPath(pathname)} · Agent 工作台`
  }, [pathname])
}

/** 登录守卫：未登录 → /login（带上原目标，登录后回跳）。 */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { initialized, isAuthenticated } = useAuth()
  const location = useLocation()
  if (!initialized) return <FullPageLoading />
  if (!isAuthenticated)
    return <Navigate to="/login" replace state={{ from: { pathname: location.pathname } }} />
  return <>{children}</>
}

/** 角色守卫：非 admin → 403 提示。 */
function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { isAdmin } = useAuth()
  if (!isAdmin) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-sm rounded-2xl border border-line bg-surface p-6 text-center shadow-card">
          <span className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-rose-50 text-rose-500 ring-1 ring-inset ring-rose-100">
            <Icon name="shield" className="h-5 w-5" />
          </span>
          <p className="text-base font-semibold text-ink">403 · 无访问权限</p>
          <p className="mt-1 text-sm leading-relaxed text-ink-muted">
            该页面仅限管理员角色访问，如需权限请联系系统管理员。
          </p>
          <Button
            as="link"
            to="/"
            variant="secondary"
            size="sm"
            leftIcon="arrow-left"
            className="mt-4"
          >
            返回看板
          </Button>
        </div>
      </div>
    )
  }
  return <>{children}</>
}

/** 工作台首页（Phase 6 起为拖拽看板交互入口）。 */
function BoardHome() {
  return <BoardPage />
}

/**
 * 路由出口的统一 Suspense 边界。
 *
 * 放在 <Routes> 外层而不是每个 element 上：翻页时 Suspense 的 fallback 会
 * 直接替掉旧内容，若边界在路由内侧，滚动位置和布局容器会被反复重建。
 * 放在外层则 AppLayout（导航栏）保持挂载，只有内容区变化。
 */
export function AppRouter() {
  useDocumentTitle()
  return (
    <Suspense fallback={<FullPageLoading />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        >
          {/* Outlet 内部单独套一层：这样布局内翻页只显示内容区加载态，
              不会把已渲染的导航栏一起换成全屏 loading。 */}
          <Route index element={<Suspended element={<BoardHome />} />} />
          <Route path="dashboard" element={<Suspended element={<DashboardPage />} />} />
          <Route path="agents" element={<Suspended element={<AgentPage />} />} />
          <Route path="groups" element={<Suspended element={<GroupPage />} />} />
          <Route path="projects" element={<Suspended element={<ProjectsPage />} />} />
          <Route path="runs/:runId" element={<Suspended element={<RunDetailPage />} />} />

          {/* 设置模块：二级导航 + 子页面。
              模板 / 调度 / 工具市场 / 管理后台都从顶部导航挪到这里，
              顶部因此只需容纳 5 个高频入口。 */}
          <Route path="settings" element={<SettingsLayout />}>
            <Route index element={<Navigate to="/settings/general" replace />} />
            <Route path="general" element={<Suspended element={<SettingsPage />} />} />
            <Route path="templates" element={<Suspended element={<AgentTemplatesPage />} />} />
            {/* 调度页/模板页自己处理 viewer 的 403（页面内文案比通用守卫更具体），
                因此这里不再叠一层 RequireMember 守卫。 */}
            <Route path="schedules" element={<Suspended element={<SchedulesPage />} />} />
            <Route path="marketplace" element={<Suspended element={<MarketplacePage />} />} />
            <Route
              path="admin"
              element={
                <RequireAdmin>
                  <Suspended element={<AdminPage />} />
                </RequireAdmin>
              }
            />
          </Route>

          {/* 旧地址兼容：书签、历史通知、外部文档里的链接不能因为重排就 404 */}
          <Route path="agent-templates" element={<Navigate to="/settings/templates" replace />} />
          <Route path="schedules" element={<Navigate to="/settings/schedules" replace />} />
          <Route path="marketplace" element={<Navigate to="/settings/marketplace" replace />} />
          <Route path="admin" element={<Navigate to="/settings/admin" replace />} />

          {/* 真 404：拼错的 URL 要明确告知，而不是静默跳看板——
              用户会把「没保存成功」误判成「跳转了」。放在布局子路由里，
              顶栏与设置导航仍在，用户可以直接点走。 */}
          <Route path="*" element={<Suspended element={<NotFound />} />} />
        </Route>
      </Routes>
    </Suspense>
  )
}

function NotFound() {
  return (
    <EmptyState
      icon="search"
      title="页面不存在"
      description="地址可能拼错了，或指向一个已被移除的页面。"
      className="mt-10"
      action={
        <Button as="link" to="/" variant="primary" leftIcon="arrow-left">
          返回看板
        </Button>
      }
    />
  )
}

/** 区块级 Suspense：懒加载页面在布局内切换时只替换内容区。 */
function Suspended({ element }: { element: React.ReactNode }) {
  return <Suspense fallback={<PageLoading />}>{element}</Suspense>
}
