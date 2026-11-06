import { readFileSync } from 'node:fs'
import { expect, test, vi } from 'vitest'

// Both service workers run here against a fake worker scope: the real one
// (public/sw.js) and the one the API serves as /sw.js on the retired shalgo
// host (render/api/app/shalgo_sw.js), which releases apps installed from there.
const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8')

function load(code, extra = {}) {
  const listeners = {}
  const self = {
    addEventListener: (type, fn) => { listeners[type] = fn },
    skipWaiting: vi.fn(),
    ...extra,
  }
  new Function('self', 'caches', 'fetch', code)(self, extra.caches, extra.fetch)
  return { self, listeners }
}

test('the shalgo worker unregisters itself and reloads each window on the main domain', async () => {
  const code = read('../../api/app/shalgo_sw.js').replace('__MAIN__', JSON.stringify('https://puntmore.com'))
  const windows = [
    { url: 'https://shalgo.puntmore.com/paddy?day=sat', navigate: vi.fn(async () => null) },
    { url: 'https://shalgo.puntmore.com/', navigate: vi.fn(async () => null) },
  ]
  const order = []
  const { self, listeners } = load(code, {
    registration: { unregister: vi.fn(async () => { order.push('unregister'); return true }) },
    clients: {
      claim: vi.fn(async () => { order.push('claim') }),
      matchAll: vi.fn(async () => { order.push('matchAll'); return windows }),
    },
  })
  listeners.install({})
  expect(self.skipWaiting).toHaveBeenCalled()
  let done
  listeners.activate({ waitUntil: (p) => { done = p } })
  await done
  expect(order).toEqual(['claim', 'unregister', 'matchAll'])
  expect(windows[0].navigate).toHaveBeenCalledWith('https://puntmore.com/paddy?day=sat')
  expect(windows[1].navigate).toHaveBeenCalledWith('https://puntmore.com/')
})

test('the real worker hands a redirected navigation to the browser as it is', async () => {
  const redirect = { type: 'opaqueredirect', ok: false, status: 0 }
  const cached = { cached: true }
  const caches = { match: vi.fn(async () => cached), open: vi.fn(async () => ({ put: vi.fn() })) }
  const { listeners } = load(read('../public/sw.js'), { caches, fetch: vi.fn(async () => redirect) })
  let answer
  listeners.fetch({
    request: { url: 'https://shalgo.puntmore.com/paddy', method: 'GET', mode: 'navigate' },
    respondWith: (p) => { answer = p },
  })
  expect(await answer).toBe(redirect)
  expect(caches.match).not.toHaveBeenCalled()
})
