/**
 * 「导入模板」按钮（R5）。
 *
 * 这段逻辑原先在 `AgentTemplatesPage` 里逐字复制了两遍。复制出来的两份还没有
 * 测试——页面级的测试只覆盖了「点导入会不会打开文件框」这类表面行为，而
 * 三条真正的规则（value 重置、JSON 解析失败、私有模板提示）都没人守。
 *
 * 这里把三条规则各自钉成断言。
 */

import { fireEvent, render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { importAgentTemplate } from '../../../api/endpoints'
import { ToastProvider } from '../../ui/Toast'
import { TemplateImportButton } from '../TemplateImportButton'

vi.mock('../../../api/endpoints', () => ({
  importAgentTemplate: vi.fn(),
}))

vi.mock('../../../api/client', () => ({
  extractError: (err: unknown) => (err instanceof Error ? err.message : '未知错误'),
}))

const mockedImport = vi.mocked(importAgentTemplate)

/** 选文件（`userEvent.upload` 不做，jsdom 下直接 fireEvent 更可控）。 */
function pickFile(container: HTMLElement, file: File) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [file] } })
  return input
}

function jsonFile(payload: unknown, name = 'template.json') {
  return new File([JSON.stringify(payload)], name, { type: 'application/json' })
}

function renderButton() {
  const onImported = vi.fn()
  const onError = vi.fn()
  const view = render(
    <ToastProvider>
      <TemplateImportButton onImported={onImported} onError={onError} />
    </ToastProvider>
  )
  return { ...view, onImported, onError }
}

describe('TemplateImportButton', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedImport.mockResolvedValue(undefined as never)
  })

  it('导入成功后回调 onImported', async () => {
    const { container, onImported, onError } = renderButton()
    pickFile(container, jsonFile({ schema_version: 1, name: 't' }))

    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1))
    expect(onError).not.toHaveBeenCalled()
    expect(mockedImport).toHaveBeenCalledWith({ schema_version: 1, name: 't' })
  })

  it('读完后立刻重置 input.value，同一个文件能连续选两次', async () => {
    const { container } = renderButton()
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = jsonFile({ schema_version: 1 })

    /*
     * 只断言 `input.value === ''` 在 jsdom 下是**恒真**的空断言：jsdom 不会把
     * fireEvent 传进来的 files 同步写进 value（真实浏览器会），所以删掉
     * `e.target.value = ''` 那行，断言照样绿。改盯「重置这个动作有没有发生」：
     * 在 value 上挂 setter spy，要求 change 期间被写入空串。
     */
    const writes: string[] = []
    const proto = Object.getPrototypeOf(input) as object
    const original = Object.getOwnPropertyDescriptor(proto, 'value')
    Object.defineProperty(input, 'value', {
      configurable: true,
      get: () => original?.get?.call(input) ?? '',
      set: (next: string) => {
        writes.push(next)
        original?.set?.call(input, next)
      },
    })

    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => expect(mockedImport).toHaveBeenCalledTimes(1))
    expect(writes).toContain('')

    // 重置之后，第二次选同一个文件仍要触发（否则用户会以为没反应）
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() => expect(mockedImport).toHaveBeenCalledTimes(2))
  })

  it('JSON 不合法时走 onError，不抛到调用方', async () => {
    const { container, onImported, onError } = renderButton()
    const broken = new File(['{ not json'], 'broken.json', {
      type: 'application/json',
    })

    // 不该有未捕获的 rejection：这里如果抛出去，vitest 会把整条用例判失败
    pickFile(container, broken)

    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1))
    expect(onImported).not.toHaveBeenCalled()
    expect(mockedImport).not.toHaveBeenCalled()
  })

  it('接口失败时把 extractError 的结果交给 onError', async () => {
    mockedImport.mockRejectedValueOnce(new Error('同名模板已存在'))
    const { container, onImported, onError } = renderButton()

    pickFile(container, jsonFile({ schema_version: 1 }))

    await waitFor(() => expect(onError).toHaveBeenCalledWith('同名模板已存在'))
    expect(onImported).not.toHaveBeenCalled()
  })

  it('文件输入框对键盘用户可达（sr-only 而非 hidden）', () => {
    const { container } = renderButton()
    const input = container.querySelector('input[type="file"]')

    // `hidden`（display:none）会把元素踢出无障碍树，键盘与读屏都够不到
    expect(input?.className).toContain('sr-only')
    expect(input?.className).not.toContain('hidden')
    // 有可读标签，否则读屏只会念「文件上传」
    expect(input?.getAttribute('aria-label')).toBeTruthy()
  })
})
