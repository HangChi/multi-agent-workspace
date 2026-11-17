/** 需求数据 hook（T058）：分配乐观更新 + 失败回滚 + 错误提示。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'

import { extractError } from '../api/client'
import { assignRequest, AssignRequestPayload, fetchRequests, WorkRequest } from '../api/endpoints'
import { useBoardStore } from '../stores/boardStore'

/** 需求列表（看板用，含 refetch）。 */
export function useRequests() {
  const qc = useQueryClient()
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['requests'],
    queryFn: () => fetchRequests(),
  })
  const requests = data?.items ?? []

  const invalidate = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['requests'] })
  }, [qc])

  return { requests, isLoading, refetch, invalidate }
}

/** 分配需求：乐观更新卡片状态为 assigned + 失败回滚 + 返回错误信息。 */
export function useAssignRequest(onSettled?: (error: string | null) => void) {
  const qc = useQueryClient()

  const mutate = useMutation({
    mutationFn: ({ requestId, payload }: { requestId: string; payload: AssignRequestPayload }) =>
      assignRequest(requestId, payload),
    onMutate: async ({ requestId }) => {
      // 乐观更新：取消进行中的请求，避免覆盖
      await qc.cancelQueries({ queryKey: ['requests'] })
      const prev = qc.getQueryData<{ items: WorkRequest[] }>(['requests'])
      if (prev) {
        qc.setQueryData<{ items: WorkRequest[] }>(['requests'], {
          ...prev,
          items: prev.items.map((r) =>
            r.id === requestId ? { ...r, status: 'assigned' as const } : r
          ),
        })
      }
      return { prev }
    },
    onError: (err, _vars, context) => {
      // 失败回滚
      if (context?.prev) {
        qc.setQueryData(['requests'], context.prev)
      }
      onSettled?.(extractError(err))
    },
    onSuccess: (data, vars) => {
      // 记录 request → run 映射（供「查看运行」跳转）
      useBoardStore.getState().setRunForRequest(vars.requestId, data.run_id)
      void qc.invalidateQueries({ queryKey: ['requests'] })
      onSettled?.(null)
    },
  })

  return {
    assign: mutate.mutate,
    assignAsync: mutate.mutateAsync,
    isPending: mutate.isPending,
  }
}
