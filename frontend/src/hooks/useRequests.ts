/** 需求数据 hook（T058 + 007）：分配乐观更新 + 失败回滚 + 错误提示。
 *
 * 007 变化：
 * - useRequests 支持 scope（relevant 默认 / mine）——「与我相关」是看板的
 *   默认视图，分派给我的需求与自己的需求在同一块板子上；
 * - useAssignRequest 同时处理两种落点：拖 Agent/组（建 Run，乐观 assigned）
 *   与拖员工（仅转交，乐观 pending）；后者没有 run_id 可记。
 * - 新增 useAcceptRequest / useRejectRequest（收件箱动作）。
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'

import { extractError } from '../api/client'
import {
  acceptRequest,
  assignRequest,
  AssignRequestPayload,
  fetchRequests,
  fetchUnreadCount,
  isDispatchResult,
  rejectRequest,
  WorkRequest,
} from '../api/endpoints'
import { useBoardStore } from '../stores/boardStore'

/** 需求列表（看板用，含 refetch）。 */
export function useRequests(scope: 'relevant' | 'mine' = 'relevant') {
  const qc = useQueryClient()
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['requests', scope],
    queryFn: () => fetchRequests({ scope }),
  })
  const requests = data?.items ?? []

  const invalidate = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['requests'] })
  }, [qc])

  return { requests, isLoading, refetch, invalidate }
}

/** 分配需求：乐观更新 + 失败回滚。agent/group 落点乐观 assigned，user 落点乐观 pending。 */
export function useAssignRequest(onSettled?: (error: string | null) => void) {
  const qc = useQueryClient()

  const mutate = useMutation({
    mutationFn: ({ requestId, payload }: { requestId: string; payload: AssignRequestPayload }) =>
      assignRequest(requestId, payload),
    onMutate: async ({ requestId, payload }) => {
      await qc.cancelQueries({ queryKey: ['requests'] })
      const prev = qc.getQueryData<{ items: WorkRequest[] }>(['requests', 'relevant'])
      const optimisticStatus = payload.assignee_type === 'user' ? 'pending' : 'assigned'
      if (prev) {
        qc.setQueryData<{ items: WorkRequest[] }>(['requests', 'relevant'], {
          ...prev,
          items: prev.items.map((r) =>
            r.id === requestId
              ? ({
                  ...r,
                  status: optimisticStatus,
                  ...(payload.assignee_type === 'user' ? { assignee_id: payload.assignee_id } : {}),
                } as WorkRequest)
              : r
          ),
        })
      }
      return { prev }
    },
    onError: (err, _vars, context) => {
      if (context?.prev) {
        qc.setQueryData(['requests', 'relevant'], context.prev)
      }
      onSettled?.(extractError(err))
    },
    onSuccess: (data, vars) => {
      if (!isDispatchResult(data)) {
        // 拖 Agent/组：记录 request → run 映射（供「查看运行」跳转）
        useBoardStore.getState().setRunForRequest(vars.requestId, data.run_id)
      }
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

/** 接收分派给我的需求（007 US2：pending → accepted）。 */
export function useAcceptRequest(onSettled?: (error: string | null) => void) {
  const qc = useQueryClient()

  const mutate = useMutation({
    mutationFn: (requestId: string) => acceptRequest(requestId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['requests'] })
      onSettled?.(null)
    },
    onError: (err) => onSettled?.(extractError(err)),
  })

  return {
    accept: mutate.mutate,
    acceptAsync: mutate.mutateAsync,
    isPending: mutate.isPending,
  }
}

/** 退回分派来的需求（007 US2：pending|accepted → created，原因必填）。 */
export function useRejectRequest(onSettled?: (error: string | null) => void) {
  const qc = useQueryClient()

  const mutate = useMutation({
    mutationFn: ({ requestId, reason }: { requestId: string; reason: string }) =>
      rejectRequest(requestId, reason),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['requests'] })
      onSettled?.(null)
    },
    onError: (err) => onSettled?.(extractError(err)),
  })

  return {
    reject: mutate.mutate,
    rejectAsync: mutate.mutateAsync,
    isPending: mutate.isPending,
  }
}

/** 未读通知数（顶栏铃铛；轮询 ≤30s，SC-007）。 */
export function useUnreadCount() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['notifications', 'unread-count'],
    queryFn: fetchUnreadCount,
    refetchInterval: 30_000,
  })
  const invalidate = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['notifications'] })
  }, [qc])
  return { unread: data ?? 0, invalidate }
}
