/** Vitest 全局 setup：jest-dom 匹配器。 */

import '@testing-library/jest-dom'

// Node 22 may expose an incomplete localStorage in jsdom tests. Zustand's
// persist middleware needs a real Storage implementation for every test file.
if (typeof globalThis.localStorage === 'undefined' || typeof globalThis.localStorage.setItem !== 'function') {
  const values = new Map<string, string>()
  const storage: Storage = {
    get length() { return values.size },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => Array.from(values.keys())[index] ?? null,
    removeItem: (key) => { values.delete(key) },
    setItem: (key, value) => { values.set(key, String(value)) },
  }
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage })
}
