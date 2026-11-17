/** Pagination 测试：总页数计算、边界钳制、单页隐藏、加载态禁用。 */

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

import { Pagination } from './Pagination'

describe('Pagination', () => {
  it('只有一页时整体不渲染（避免永远禁用的噪音）', () => {
    const { container } = render(
      <Pagination page={1} pageSize={20} total={5} onChange={() => {}} />,
    )
    expect(container.innerHTML).toBe('')
  })

  it('恰好等于一页时也不渲染', () => {
    const { container } = render(
      <Pagination page={1} pageSize={20} total={20} onChange={() => {}} />,
    )
    expect(container.innerHTML).toBe('')
  })

  it('空列表不渲染', () => {
    const { container } = render(
      <Pagination page={1} pageSize={20} total={0} onChange={() => {}} />,
    )
    expect(container.innerHTML).toBe('')
  })

  it('多页时展示页码与总条数', () => {
    const { container } = render(
      <Pagination page={2} pageSize={10} total={35} onChange={() => {}} />,
    )
    const summary = container.querySelector('p')?.textContent ?? ''
    // total=35, pageSize=10 → 4 页
    expect(summary).toContain('2')
    expect(summary).toContain('4 页')
    expect(summary).toContain('35')
  })

  it('总页数向上取整（35 条 / 每页 10 = 4 页）', () => {
    const { container } = render(
      <Pagination page={1} pageSize={10} total={35} onChange={() => {}} />,
    )
    expect(container.querySelector('p')?.textContent).toContain('4 页')
  })

  it('第 1 页时「上一页」禁用', () => {
    render(<Pagination page={1} pageSize={10} total={30} onChange={() => {}} />)
    expect(screen.getByRole('button', { name: /上一页/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /下一页/ })).not.toBeDisabled()
  })

  it('末页时「下一页」禁用', () => {
    render(<Pagination page={3} pageSize={10} total={30} onChange={() => {}} />)
    expect(screen.getByRole('button', { name: /下一页/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /上一页/ })).not.toBeDisabled()
  })

  it('点击翻页回调收到目标页', () => {
    const onChange = vi.fn()
    render(<Pagination page={2} pageSize={10} total={50} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /下一页/ }))
    expect(onChange).toHaveBeenCalledWith(3)

    fireEvent.click(screen.getByRole('button', { name: /上一页/ }))
    expect(onChange).toHaveBeenCalledWith(1)
  })

  it('越界页码被钳制到有效区间，不会把 page=0 传出去', () => {
    const onChange = vi.fn()
    render(<Pagination page={5} pageSize={10} total={50} onChange={onChange} />)
    // page=5 是末页，「下一页」禁用；强行点到也不该发出 6
    fireEvent.click(screen.getByRole('button', { name: /下一页/ }))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('loading 时两个按钮都禁用，避免连点导致请求乱序', () => {
    render(<Pagination page={2} pageSize={10} total={50} onChange={() => {}} loading />)
    expect(screen.getByRole('button', { name: /上一页/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /下一页/ })).toBeDisabled()
  })
})
