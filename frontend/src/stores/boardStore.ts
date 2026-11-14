/** 看板分配弹窗状态（T058）：当前待分配需求 + 目标，由拖拽触发。 */

import { create } from 'zustand'

import { WorkRequest } from '../api/endpoints'
import { AssignTarget } from '../components/board/AssignModal'

interface BoardState {
  /** 待确认分配的需求 + 目标；null 表示弹窗关闭。 */
  pending: { request: WorkRequest; target: AssignTarget } | null
  /** request_id → 最近一次分配的 run_id（供「查看运行」跳转）。 */
  runByRequest: Record<string, string>
  openAssign: (request: WorkRequest, target: AssignTarget) => void
  closeAssign: () => void
  setRunForRequest: (requestId: string, runId: string) => void
}

export const useBoardStore = create<BoardState>((set) => ({
  pending: null,
  runByRequest: {},

  openAssign: (request, target) => set({ pending: { request, target } }),

  closeAssign: () => set({ pending: null }),

  setRunForRequest: (requestId, runId) =>
    set((state) => ({ runByRequest: { ...state.runByRequest, [requestId]: runId } })),
}))
