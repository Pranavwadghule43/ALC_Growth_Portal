import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Tests for the single-flight token refresh in api.ts. They run in plain Node (no browser):
// fetch, window.location and document.cookie are stubbed.

type Pending = { resolve: (r: Response) => void }
const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

let calls: string[]
let assign: ReturnType<typeof vi.fn>
let handler: (path: string, n: number) => Response | Promise<Response>

function setup(pathname = '/portal') {
  calls = []
  assign = vi.fn()
  vi.stubGlobal('window', { location: { pathname, assign } })
  vi.stubGlobal('document', { cookie: 'csrf_token=abc' })
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = url.replace(/^.*\/api/, '')
    calls.push(path)
    return Promise.resolve(handler(path, calls.filter(c => c === path).length))
  }))
}

// A fresh copy of api.ts per test, so the module-level refresh state starts empty.
async function loadApi() { vi.resetModules(); return (await import('./api')).api }

const count = (path: string) => calls.filter(c => c === path).length

beforeEach(() => setup())
afterEach(() => vi.unstubAllGlobals())

describe('single-flight refresh', () => {
  it('simultaneous 401s trigger only one refresh and all requests resume', async () => {
    const api = await loadApi()
    let refresh!: Pending
    handler = (path, n) => {
      if (path === '/auth/refresh') return new Promise(resolve => { refresh = { resolve } })
      return n === 1 ? json(401) : json(200, { path })
    }
    const results = Promise.all([api.get('/a'), api.get('/b'), api.get('/c')])
    await vi.waitFor(() => expect(count('/auth/refresh')).toBe(1))
    refresh.resolve(json(200))
    expect(await results).toEqual([{ path: '/a' }, { path: '/b' }, { path: '/c' }])
    expect(count('/auth/refresh')).toBe(1)
    expect(['/a', '/b', '/c'].map(count)).toEqual([2, 2, 2]) // original + one retry each
    expect(assign).not.toHaveBeenCalled()
  })

  it('a 401 that arrives after the refresh finished retries without refreshing again', async () => {
    const api = await loadApi()
    let late!: Pending
    handler = (path, n) => {
      if (path === '/auth/refresh') return json(200)
      if (path === '/late' && n === 1) return new Promise(resolve => { late = { resolve } })
      return n === 1 ? json(401) : json(200, { path })
    }
    const slow = api.get('/late') // sent before the refresh...
    await vi.waitFor(() => expect(count('/late')).toBe(1))
    expect(await api.get('/fast')).toEqual({ path: '/fast' }) // ...which this request triggers
    late.resolve(json(401)) // ...but its 401 comes back only afterwards
    expect(await slow).toEqual({ path: '/late' })
    expect(count('/auth/refresh')).toBe(1)
  })

  it('failed refresh rejects every waiting request and sends the user to sign in', async () => {
    const api = await loadApi()
    handler = path => (path === '/auth/refresh' ? json(401, { detail: 'Session expired' }) : json(401, { detail: 'Not authenticated' }))
    const results = await Promise.allSettled([api.get('/a'), api.get('/b')])
    expect(results.map(r => r.status)).toEqual(['rejected', 'rejected'])
    expect(count('/auth/refresh')).toBe(1)
    expect(assign).toHaveBeenCalledWith('/login')
    expect(count('/a')).toBe(1) // not retried after a failed refresh
  })

  it('admin pages are sent to the admin sign-in page', async () => {
    setup('/admin/users')
    const api = await loadApi()
    handler = () => json(401)
    await expect(api.get('/admin/users')).rejects.toMatchObject({ status: 401 })
    expect(assign).toHaveBeenCalledWith('/admin/login')
  })

  it('a retried request that fails again is not retried a second time', async () => {
    const api = await loadApi()
    handler = path => (path === '/auth/refresh' ? json(200) : json(401))
    await expect(api.get('/a')).rejects.toMatchObject({ status: 401 })
    expect(count('/a')).toBe(2)
    expect(count('/auth/refresh')).toBe(1)
  })

  it('auth endpoints never trigger a refresh themselves', async () => {
    const api = await loadApi()
    handler = () => json(401, { detail: 'Invalid' })
    for (const path of ['/auth/refresh', '/auth/login', '/auth/admin-login', '/auth/logout']) {
      await expect(api.post(path, {})).rejects.toMatchObject({ status: 401 })
    }
    expect(calls).toEqual(['/auth/refresh', '/auth/login', '/auth/admin-login', '/auth/logout'])
    expect(assign).not.toHaveBeenCalled()
  })

  it('/auth/me refreshes an expired access token, and leaves navigation to the guards', async () => {
    const api = await loadApi()
    handler = (path, n) => (path === '/auth/refresh' ? json(200) : n === 1 ? json(401) : json(200, { id: 'u1' }))
    expect(await api.get('/auth/me')).toEqual({ id: 'u1' })

    handler = () => json(401)
    await expect(api.get('/auth/me')).rejects.toMatchObject({ status: 401 })
    expect(assign).not.toHaveBeenCalled()
  })

  it('a new refresh can start after the previous one has settled', async () => {
    const api = await loadApi()
    handler = (path, n) => (path === '/auth/refresh' ? json(200) : n % 2 === 1 ? json(401) : json(200, {}))
    await api.get('/a')
    await api.get('/a')
    expect(count('/auth/refresh')).toBe(2)
  })
})