/** Vitest 全局 setup：jest-dom 匹配器 + jsdom 缺口的补齐。 */

import '@testing-library/jest-dom'

// Node 22 may expose an incomplete localStorage in jsdom tests. Zustand's
// persist middleware needs a real Storage implementation for every test file.
if (
  typeof globalThis.localStorage === 'undefined' ||
  typeof globalThis.localStorage.setItem !== 'function'
) {
  const values = new Map<string, string>()
  const storage: Storage = {
    get length() {
      return values.size
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => Array.from(values.keys())[index] ?? null,
    removeItem: (key) => {
      values.delete(key)
    },
    setItem: (key, value) => {
      values.set(key, String(value))
    },
  }
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage })
}

// jsdom 的 `File`/`Blob` 没有 `text()` 与 `arrayBuffer()`（规范里有，实现没跟上）。
// 凡是用 `await file.text()` 读用户上传文件的代码，在测试里都会炸
// `TypeError: file.text is not a function`——看起来像被测组件有 bug，其实是环境缺口。
// 这里按规范语义补上：读出的内容经过一次 JSON/字符串解析都是等价的。
if (typeof Blob !== 'undefined' && typeof Blob.prototype.text !== 'function') {
  Object.defineProperty(Blob.prototype, 'text', {
    configurable: true,
    value(this: Blob): Promise<string> {
      return new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result ?? ''))
        reader.onerror = () => reject(reader.error)
        reader.readAsText(this)
      })
    },
  })
}
if (typeof Blob !== 'undefined' && typeof Blob.prototype.arrayBuffer !== 'function') {
  Object.defineProperty(Blob.prototype, 'arrayBuffer', {
    configurable: true,
    value(this: Blob): Promise<ArrayBuffer> {
      return new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(reader.result as ArrayBuffer)
        reader.onerror = () => reject(reader.error)
        reader.readAsArrayBuffer(this)
      })
    },
  })
}
