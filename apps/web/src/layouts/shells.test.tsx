// @vitest-environment jsdom
// Post-demo update 3: the left sidebar is gone; every role navigates from the shared top header
// (RCU Pune logo, role navigation with a "More" overflow, account menu with Profile / Sign out).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'
import { api } from '../lib/api'
import type { Role, User } from '../types'
import { click, render } from '../test/harness'
import AdminShell from './AdminShell'
import PortalShell from './PortalShell'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } }))
const post = vi.mocked(api.post)

beforeEach(() => { vi.useFakeTimers(); post.mockReset(); post.mockResolvedValue(undefined) })
afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })

const user = (role: Role, extra: Partial<User> = {}): User => ({
  id: 'u1', username: `${role.toLowerCase()}.user`, role, is_active: true, must_change_password: false,
  alc: role === 'ALC' ? { alc_code: '57210001', alc_name: 'Pune Centre' } as User['alc'] : undefined,
  sbu: role === 'SBU' ? { code: 'SBU-1', name: 'Pune SBU' } as User['sbu'] : undefined,
  dcu: role === 'DCU' ? { code: 'DCU-1', name: 'Pune DCU' } as User['dcu'] : undefined,
  ...extra,
})

function Where() { return <p data-testid="where">{useLocation().pathname}</p> }

async function mount(u: User, path: string) {
  const Shell = u.role === 'ADMIN' ? AdminShell : PortalShell
  const view = await render(<Routes>
    <Route element={<Shell user={u}/>}><Route path="*" element={<Where/>}/></Route>
    <Route path="/login" element={<Where/>}/><Route path="/admin/login" element={<Where/>}/>
  </Routes>, path)
  const header = view.container.querySelector('header')!
  const bar = header.querySelector('nav')!
  return {
    ...view, header,
    // Inline header links (the "More" panel is not rendered until opened).
    barLinks: () => [...bar.querySelectorAll(':scope > a')].map(a => [a.textContent, a.getAttribute('href')]),
    current: () => [...header.querySelectorAll('a[aria-current="page"]')].map(a => a.textContent),
    where: () => view.container.querySelector('[data-testid="where"]')?.textContent,
    button: (name: RegExp) => [...header.querySelectorAll('button')].find(b => name.test(b.textContent ?? ''))!,
  }
}

const expected: Record<Role, [string, string][]> = {
  ALC: [['Dashboard', '/portal'], ['Partners', '/portal/partners'], ['Add Activity', '/portal/activities/new'], ['My Activities', '/portal/activities'], ['Tasks', '/portal/tasks'],
    ['Growth Challenge', '/portal/challenge'], ['Performance', '/portal/performance'], ['Growth Resources', '/portal/resources'], ['Profile', '/portal/profile']],
  SBU: [['Dashboard', '/portal'], ['ALCs', '/portal/alcs'], ['Partners', '/portal/partners'], ['Activities', '/portal/activities'], ['Verification', '/portal/verification'], ['Reports', '/portal/reports'], ['Profile', '/portal/profile']],
  DCU: [['Dashboard', '/portal'], ['SBUs', '/portal/sbus'], ['ALCs', '/portal/alcs'], ['Partners', '/portal/partners'], ['Activities', '/portal/activities'], ['Verification', '/portal/verification'], ['Reports', '/portal/reports'], ['Profile', '/portal/profile']],
  ADMIN: [['Dashboard', '/admin'], ['Partners', '/admin/partners'], ['Verification Queue', '/admin/verification'], ['Activities', '/admin/activities'], ['ALCs', '/admin/alcs'], ['SBUs', '/admin/sbus'],
    ['Growth Challenge', '/admin/challenge'], ['Reports', '/admin/reports'], ['Users', '/admin/users'], ['Audit Logs', '/admin/audit'], ['Settings', '/admin/settings']],
}
const home = (role: Role) => (role === 'ADMIN' ? '/admin' : '/portal')

describe.each(['ALC', 'SBU', 'DCU', 'ADMIN'] as Role[])('%s shell', role => {
  it('has no sidebar, shows the RCU Pune logo linking home, and no sidebar offset on content', async () => {
    const view = await mount(user(role), `${home(role)}/partners`)
    expect(view.container.querySelector('aside')).toBeNull()
    expect(view.container.innerHTML).not.toMatch(/lg:ml-64|w-64|-translate-x-full/)
    expect(view.header.className).toContain('sticky')
    const logo = view.header.querySelector<HTMLImageElement>('img')!
    expect(logo.getAttribute('src')).toBe('/branding/rcu-pune-logo.png')
    expect(logo.alt).toMatch(/RCU Pune/)
    expect(logo.className).toContain('w-auto') // height-driven: never stretched
    const logoLink = logo.closest('a')!
    expect(logoLink.getAttribute('href')).toBe(home(role))
    await click(logoLink)
    expect(view.where()).toBe(home(role))
  })

  it('keeps every existing nav item, in order, with unchanged URLs', async () => {
    const view = await mount(user(role), home(role))
    const items = expected[role]
    // Inline bar holds the items in order; "more" items are reachable from the More menu.
    const inline = view.barLinks()
    expect(inline).toEqual(items.slice(0, inline.length))
    await click(view.button(/More/))
    const menu = [...view.header.querySelectorAll('nav ul a')].map(a => [a.textContent, a.getAttribute('href')])
    expect(menu).toEqual(items)
    const labels = items.map(([l]) => l)
    expect(labels.indexOf('Partners')).toBeLessThan(labels.findIndex(l => /Activit|Verification/.test(l)))
    expect(labels.join()).not.toMatch(/30-Day/)
    if (role === 'ALC' || role === 'ADMIN') expect(labels).toContain('Growth Challenge')
  })
})

describe('active navigation', () => {
  it.each([
    ['ALC', '/portal', 'Dashboard'], ['ALC', '/portal/activities/new', 'Add Activity'], ['ALC', '/portal/activities/a1', 'My Activities'],
    ['ALC', '/portal/challenge', 'Growth Challenge'], ['SBU', '/portal/alcs/x', 'ALCs'], ['DCU', '/portal/verification/v1', 'Verification'],
    ['ADMIN', '/admin', 'Dashboard'], ['ADMIN', '/admin/activities/a1', 'Activities'], ['ADMIN', '/admin/sbus/s1', 'SBUs'],
  ] as [Role, string, string][])('%s at %s highlights only %s', async (role, path, label) => {
    const view = await mount(user(role), path)
    expect(view.current()).toEqual([label])
  })

  it('marks "More" while the active page is one of its items', async () => {
    const view = await mount(user('ADMIN'), '/admin/audit')
    expect(view.button(/More/).className).toMatch(/(^| )bg-white\/15/)
    await click(view.button(/More/))
    expect(view.current()).toEqual(['Audit Logs'])
  })
})

describe('menus', () => {
  it('More closes on Escape and returns focus, and on an outside click', async () => {
    const view = await mount(user('ADMIN'), '/admin')
    const more = view.button(/More/)
    await click(more)
    expect(more.getAttribute('aria-expanded')).toBe('true')
    await act(async () => { more.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
    expect(more.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(more)
    await click(more)
    await act(async () => { document.body.dispatchEvent(new Event('pointerdown', { bubbles: true })) })
    expect(more.getAttribute('aria-expanded')).toBe('false')
  })

  it('following a More link navigates and closes the menu', async () => {
    const view = await mount(user('ADMIN'), '/admin')
    await click(view.button(/More/))
    await click([...view.header.querySelectorAll('nav ul a')].find(a => a.textContent === 'Users')!)
    expect(view.where()).toBe('/admin/users')
    expect(view.button(/More/).getAttribute('aria-expanded')).toBe('false')
  })

  it.each(['ALC', 'SBU', 'DCU'] as Role[])('%s account menu shows identity, Profile and Sign out', async role => {
    const view = await mount(user(role), '/portal')
    const account = view.button(/Account|Pune/)
    await click(account)
    expect(account.getAttribute('aria-expanded')).toBe('true')
    const panel = document.getElementById(account.getAttribute('aria-controls')!)!
    expect(panel.textContent).toContain(role === 'ALC' ? 'Pune Centre' : `Pune ${role}`)
    expect(panel.querySelector('a')!.getAttribute('href')).toBe('/portal/profile')
    await click([...panel.querySelectorAll('button')].find(b => b.textContent === 'Sign out')!)
    expect(post).toHaveBeenCalledWith('/auth/logout')
    expect(view.where()).toBe('/login')
  })

  it('ADMIN Sign out logs out to the admin login', async () => {
    const view = await mount(user('ADMIN'), '/admin')
    await click(view.button(/Account|admin\.user/))
    await click(view.button(/Sign out/))
    expect(post).toHaveBeenCalledWith('/auth/logout')
    expect(view.where()).toBe('/admin/login')
  })
})

describe('password change required', () => {
  it('portal shows only Profile plus the unlock notice', async () => {
    const view = await mount(user('ALC', { must_change_password: true }), '/portal/profile')
    expect(view.barLinks()).toEqual([['Profile', '/portal/profile']])
    expect(view.header.textContent).toContain('Change your password to unlock the rest of the portal.')
  })

  it('admin shows no navigation, only the notice and account menu', async () => {
    const view = await mount(user('ADMIN', { must_change_password: true }), '/admin/profile')
    expect(view.barLinks()).toEqual([])
    expect(view.button(/More/)).toBeUndefined()
    expect(view.header.textContent).toContain('Change your password on this page to unlock the admin console.')
  })
})
