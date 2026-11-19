/** AssignTargets 组件测试（014）：三行分区 / 折叠 / 同事过滤。
 *
 * 拖拽契约（useDroppable 的 id 与 title）由 s12 的 B4 真实浏览器覆盖，
 * 这里只钉纯 UI 逻辑：行标签、计数、折叠阈值、过滤。
 */

import { render, screen, fireEvent } from '@testing-library/react'
import { DndContext } from '@dnd-kit/core'
import { describe, expect, it } from 'vitest'

import { AssignTargets } from '../components/board/AssignTargets'
import type { Agent, Group, UserOption } from '../api/endpoints'

const agent = {
  id: 'a1',
  owner_id: 'u1',
  name: '官网改版助手',
  description: null,
  system_prompt: 'x',
  model: 'Deepseek-V4.1-Flash',
  temperature: 0.7,
  max_steps: 4,
  max_run_seconds: 300,
  cost_limit_cents: 10000,
  egress_mode: 'default',
  tools: [],
  status: 'active',
  created_at: '2026-11-18T00:00:00Z',
  updated_at: '2026-11-18T00:00:00Z',
} as Agent

const group = {
  id: 'g1',
  owner_id: 'u1',
  name: '内容流水线',
  description: null,
  routing_strategy: 'pipeline',
  members: [{ agent_id: 'a1', name: '官网改版助手', position: 0 }],
  status: 'active',
  created_at: '2026-11-18T00:00:00Z',
  updated_at: '2026-11-18T00:00:00Z',
} as Group

function user(i: number): UserOption {
  return {
    id: `u${i}`,
    email: `colleague-${i}@example.test`,
    role: 'member',
    status: 'active',
  } as UserOption
}

function setup(users: UserOption[]) {
  return render(
    <DndContext>
      <AssignTargets agents={[agent]} groups={[group]} users={users} />
    </DndContext>
  )
}

describe('AssignTargets（014 重设计）', () => {
  it('三类目标各占一行，带各自计数', () => {
    setup([user(1), user(2)])
    expect(screen.getByText('同事')).toBeDefined()
    expect(screen.getByText('2')).toBeDefined()
    expect(screen.getByText('Agent')).toBeDefined()
    expect(screen.getAllByText('1').length).toBeGreaterThanOrEqual(2) // Agent 行 + 组行各一个
    expect(screen.getByText('组')).toBeDefined()
    // 芯片仍带可拖拽提示（title 契约不变）
    expect(screen.getByTitle('拖到这里分派给「colleague-1」 · 同事 · 转交处理责任')).toBeDefined()
    expect(
      screen.getByTitle('拖到这里分配给「官网改版助手」 · Deepseek-V4.1-Flash · 0 个工具')
    ).toBeDefined()
  })

  it('同事超过 8 个时折叠成 +N，展开后全部可见', () => {
    setup(Array.from({ length: 12 }, (_, i) => user(i + 1)))
    // 默认只见 8 个
    expect(screen.getAllByTitle(/拖到这里分派给/)).toHaveLength(8)
    fireEvent.click(screen.getByText('+4 展开'))
    expect(screen.getAllByTitle(/拖到这里分派给|拖到这里分派给/)).toHaveLength(12)
    fireEvent.click(screen.getByText('收起'))
    expect(screen.getAllByTitle(/拖到这里分派给/)).toHaveLength(8)
  })

  it('过滤框按邮箱子串缩小同事行', () => {
    setup([user(1), user(2), user(3)])
    fireEvent.change(screen.getByLabelText('筛选分配同事'), {
      target: { value: 'colleague-2' },
    })
    expect(screen.getAllByTitle(/拖到这里分派给/)).toHaveLength(1)
    expect(screen.getByTitle('拖到这里分派给「colleague-2」 · 同事 · 转交处理责任')).toBeDefined()
  })

  it('三类全空时显示引导文案', () => {
    render(
      <DndContext>
        <AssignTargets agents={[]} groups={[]} users={[]} />
      </DndContext>
    )
    expect(screen.getByText(/还没有可分配的/)).toBeDefined()
  })
})
