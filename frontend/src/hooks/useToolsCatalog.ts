/**
 * 工具目录 hook（AgentForm / TemplateForm 共用）。
 *
 * 走 TanStack Query：loading / error / ready 三态分明 + 重试。
 * 此前两个表单各自裸 fetch + catch(setCatalog([]))，把「拉取失败」
 * 伪装成「平台没有工具」，还顺带显示一条误导性的配置警告。
 */

import { useQuery } from '@tanstack/react-query'

import { fetchTools } from '../api/endpoints'
import type { ToolSpec } from '../api/endpoints'

export function useToolsCatalog() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['tools', 'catalog'],
    queryFn: fetchTools,
    staleTime: 60_000,
  })
  const catalog: ToolSpec[] = (data?.items ?? []).filter((tool) => tool.enabled)
  return { catalog, isLoading, isError, refetch }
}
