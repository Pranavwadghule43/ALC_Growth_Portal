// @vitest-environment jsdom
// Phase 4H: debounced server-side search, page reset, stale-response safety, and paginated
// selectors on the list pages. The API client is mocked; timers are fake (no real sleeps).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../lib/api'
import { SEARCH_DEBOUNCE_MS } from '../lib/hooks'
import type { ActivityListItem } from '../types'
import {
  button, click, deferred, fakeApi, inputByPlaceholder, page, render, selectValue, settle, setInput, typeText,
} from '../test/harness'
import AdminChallenge from './AdminChallenge'
import AdminDirectory from './AdminDirectory'
import AdminUsers from './AdminUsers'
import DcuActivities from './DcuActivities'
import SbuActivities from './SbuActivities'
import SbuAlcs from './SbuAlcs'

vi.mock('../lib/api', () => {
  class ApiError extends Error { constructor(message: string, public status: number) { super(message) } }
  return { ApiError, api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), downloadUrl: (p: string) => p } }
})
const get = vi.mocked(api.get) as unknown as ReturnType<typeof vi.fn>

beforeEach(() => { vi.useFakeTimers(); get.mockReset() })
afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })

const alcs = (n: number, prefix = 5721) => Array.from({ length: n }, (_, i) => ({
  id: `alc-${i + 1}`, alc_code: `${prefix}${String(i + 1).padStart(4, '0')}`, alc_name: `Centre ${i + 1}`, status: 'ACTIVE',
  activities: i, verified: 0, pending: 0, learners: 0, partners: 0, corrections: 0,
}))
const matching = <T extends { alc_code: string; alc_name: string }>(rows: T[], search: string | null) =>
  search ? rows.filter(r => `${r.alc_code} ${r.alc_name}`.toLowerCase().includes(search.toLowerCase())) : rows

function activity(n: number, extra: Partial<ActivityListItem> = {}): ActivityListItem {
  return {
    id: `act-${n}`, activity_number: `ACT-2026-${n}`, alc_id: 'alc-1', activity_type: 'Partner meeting',
    activity_date: '2026-08-01', status: 'SUBMITTED', submitted_at: '2026-08-02T10:00:00Z', updated_at: '2026-08-02T10:00:00Z',
    learners_reached: 11, leads_generated: 7, admissions_generated: 3, partner_name: 'Nashik College',
    evidence_count: 2, revision_count: 1, had_correction: false, ...extra,
  }
}
const queueRow = (a: ActivityListItem) => ({ activity: a, alc: { id: 'alc-1', alc_code: '57210001', alc_name: 'Centre 1', status: 'ACTIVE' }, sbu: { id: 'sbu-4', code: 'SBU 4' } })

// ---------------------------------------------------------------------------------------------
describe('debounced search (ALC directory)', () => {
  const rows = alcs(80)
  const setup = () => fakeApi(get, (path, p) => page(matching(rows, p.get('search')), p))

  it('typing several characters quickly sends one search after the debounce interval', async () => {
    const api = setup()
    const { container } = await render(<AdminDirectory />)
    expect(api.to('/admin/alcs')).toHaveLength(1) // initial page
    await typeText(inputByPlaceholder(container, 'Search ALC code or centre name'), 'Centre 7')
    await settle(SEARCH_DEBOUNCE_MS - 1)
    expect(api.to('/admin/alcs')).toHaveLength(1) // still waiting
    await settle(1)
    const searches = api.to('/admin/alcs')
    expect(searches).toHaveLength(2) // exactly one new request, not one per keystroke
    expect(searches[1]!.get('search')).toBe('Centre 7')
    expect(container.textContent).toContain('Centre 7')
    expect(container.textContent).not.toContain('Centre 12')
  })

  it('resets to page 1 on a new search, and pagination still works on the results', async () => {
    const api = setup()
    const { container } = await render(<AdminDirectory />)
    await click(button(container, 'Next')); await settle()
    await click(button(container, 'Next')); await settle()
    expect(api.to('/admin/alcs').at(-1)!.get('page')).toBe('3')
    const before = api.calls.length
    await typeText(inputByPlaceholder(container, 'Search ALC code or centre name'), '5721')
    await settle(SEARCH_DEBOUNCE_MS)
    const after = api.to('/admin/alcs').slice(-(api.calls.length - before))
    // One request, for page 1 of the new search; the old page is never asked for.
    expect(after.map(p => [p.get('page'), p.get('search')])).toEqual([['1', '5721']])
    await click(button(container, 'Next')); await settle()
    expect(api.to('/admin/alcs').at(-1)!.toString()).toBe('page=2&page_size=25&search=5721')
  })

  it('clearing the search returns to the full list immediately', async () => {
    const api = setup()
    const { container } = await render(<AdminDirectory />)
    const input = inputByPlaceholder(container, 'Search ALC code or centre name')
    await typeText(input, 'Centre 3')
    await settle(SEARCH_DEBOUNCE_MS)
    const count = api.calls.length
    await setInput(input, '')
    await settle() // no debounce wait for a cleared box
    // The unfiltered first page was already cached, so the list is back without a new search.
    expect(api.calls.length).toBeLessThanOrEqual(count + 1)
    // Only the unfiltered list and the finished term were ever requested, never a partial one.
    expect(new Set(api.to('/admin/alcs').map(p => p.get('search')))).toEqual(new Set(['', 'Centre 3']))
    expect(container.textContent).toContain('Centre 12')
  })
})

// ---------------------------------------------------------------------------------------------
describe('immediate filters and page reset (SBU ALCs)', () => {
  it('status select queries at once and returns to page 1', async () => {
    const rows = alcs(60)
    const api = fakeApi(get, (_path, p) => page(p.get('status') === 'INACTIVE' ? rows.slice(0, 30) : rows, p))
    const { container } = await render(<SbuAlcs />)
    await click(button(container, 'Next')); await settle()
    expect(api.to('/portal/alcs').at(-1)!.get('page')).toBe('2')
    await selectValue(container.querySelector('select')!, 'INACTIVE')
    await settle() // no debounce for a select
    const last = api.to('/portal/alcs').at(-1)!
    expect([last.get('status'), last.get('page')]).toEqual(['INACTIVE', '1'])
  })
})

// ---------------------------------------------------------------------------------------------
describe('stale responses', () => {
  it('an older, slower response never replaces the newer search results', async () => {
    const slow = deferred<unknown>()
    const rows = alcs(40)
    fakeApi(get, (_path, p) => {
      const search = p.get('search')
      if (search === 'Centre 1') return slow.promise // the older request answers last
      return page(matching(rows, search), p)
    })
    const { container } = await render(<AdminDirectory />)
    const input = inputByPlaceholder(container, 'Search ALC code or centre name')
    await typeText(input, 'Centre 1')
    await settle(SEARCH_DEBOUNCE_MS)
    await typeText(input, '5', { from: 'Centre 1' })
    await settle(SEARCH_DEBOUNCE_MS)
    expect(container.textContent).toContain('Centre 15')
    // Now the stale "Centre 1" response arrives with different rows.
    slow.resolve(page([{ ...rows[0]!, alc_name: 'STALE ROW' }], new URLSearchParams()))
    await settle()
    expect(container.textContent).toContain('Centre 15')
    expect(container.textContent).not.toContain('STALE ROW')
  })
})

// ---------------------------------------------------------------------------------------------
describe('loading and error states', () => {
  it('shows loading (not "no results") while a search is pending, and survives an API error', async () => {
    const rows = alcs(10)
    const pending = deferred<unknown>()
    fakeApi(get, (_path, p) => {
      if (p.get('search') === 'slow') return pending.promise
      if (p.get('search') === 'boom') throw new ApiError('Service temporarily unavailable', 503)
      return page(matching(rows, p.get('search')), p)
    })
    const { container } = await render(<AdminDirectory />)
    const input = inputByPlaceholder(container, 'Search ALC code or centre name')
    await typeText(input, 'slow')
    expect(container.textContent).not.toContain('Nothing to show') // debounce window: old rows stay
    await settle(SEARCH_DEBOUNCE_MS)
    expect(container.textContent).toContain('Loading')
    expect(container.textContent).not.toContain('Nothing to show')
    pending.resolve(page([], new URLSearchParams()))
    await settle()
    expect(container.textContent).toContain('Nothing to show')

    await setInput(input, '')
    await typeText(input, 'boom')
    await settle(SEARCH_DEBOUNCE_MS)
    expect(container.textContent).toContain('Service temporarily unavailable')
    expect(container.textContent).not.toContain('Traceback')
    // The page still works: a new search recovers.
    await setInput(input, '')
    await typeText(input, 'Centre 2')
    await settle(SEARCH_DEBOUNCE_MS)
    expect(container.textContent).toContain('Centre 2')
    expect(container.textContent).not.toContain('Service temporarily unavailable')
  })
})

// ---------------------------------------------------------------------------------------------
describe('Admin Users search keeps its existing 350 ms debounce', () => {
  it('sends one user search per pause', async () => {
    const api = fakeApi(get, (path, p) => {
      if (path === '/admin/dcus') return { items: [] }
      return page([], p)
    })
    const { container } = await render(<AdminUsers />)
    const before = api.to('/admin/users').length
    await typeText(container.querySelector<HTMLInputElement>('input[type="search"], input[placeholder*="Search"]')!, 'ravi')
    await settle(SEARCH_DEBOUNCE_MS)
    const users = api.to('/admin/users')
    expect(users.length - before).toBe(1)
    expect([users.at(-1)!.get('q'), users.at(-1)!.get('page')]).toEqual(['ravi', '1'])
  })
})

// ---------------------------------------------------------------------------------------------
describe('Growth Challenge: all ALCs reachable', () => {
  const rows = alcs(784).map(r => ({ ...r, prospects: 1, meetings: 0, pilots: 0, partnerships: 0 }))
  const targets = { prospects: 40, meetings: 20, pilots: 10, partnerships: 5 }
  const setup = () => fakeApi(get, (_path, p) => page(matching(rows, p.get('search')), p, { targets }))

  it('pages beyond the first 100 ALCs', async () => {
    const api = setup()
    const { container } = await render(<AdminChallenge />)
    expect(api.to('/admin/challenge')[0]!.toString()).toBe('page=1&page_size=100')
    expect(container.textContent).toContain('Page 1 of 8')
    expect(container.textContent).not.toContain('57210101')
    await click(button(container, 'Next')); await settle()
    expect(api.to('/admin/challenge').at(-1)!.get('page')).toBe('2')
    expect(container.textContent).toContain('57210101')
  })

  it('searches the server for an ALC outside the first page', async () => {
    const api = setup()
    const { container } = await render(<AdminChallenge />)
    await typeText(inputByPlaceholder(container, 'Search ALC code or centre name'), '57210700')
    await settle(SEARCH_DEBOUNCE_MS)
    const last = api.to('/admin/challenge').at(-1)!
    expect([last.get('search'), last.get('page')]).toEqual(['57210700', '1'])
    expect(container.textContent).toContain('Centre 700')
    expect(api.to('/admin/challenge')).toHaveLength(2) // initial + one debounced search
  })
})

// ---------------------------------------------------------------------------------------------
describe('SBU activities: scoped, paginated ALC selector', () => {
  const scoped = alcs(130) // more than any fixed first-100 list
  const setup = () => fakeApi(get, (path, p) => {
    if (path === '/portal/alcs') return page(matching(scoped, p.get('search')), p)
    if (path === '/portal/verification') return page([
      queueRow(activity(1, { status: 'RESUBMITTED', evidence_count: 3, had_correction: true, resubmitted_at: '2026-08-03T09:00:00Z' })),
      queueRow(activity(2, { evidence_count: 0 })),
    ], p)
    throw new Error(`unexpected ${path}`)
  })
  const select = (c: Element) => c.querySelector<HTMLSelectElement>('select[aria-label="ALC"]')!
  const options = (c: Element) => [...select(c).options].map(o => o.textContent)

  it('loads one scoped page of options, can load more, and never uses a fixed 100-item list', async () => {
    const api = setup()
    const { container } = await render(<SbuActivities />)
    const lookups = api.to('/portal/alcs')
    expect(lookups).toHaveLength(1)
    expect(lookups[0]!.toString()).toBe('page=1&page_size=25')
    expect(options(container)).toHaveLength(26) // "All ALCs" + 25
    expect(container.textContent).toContain('25 of 130 ALCs')
    // Scope stays server-side: only the caller-scoped portal endpoint, no hierarchy ids sent.
    expect(api.calls.every(c => !c.startsWith('/admin/'))).toBe(true)
    expect(lookups.every(p => !p.has('sbu_id') && !p.has('dcu_id'))).toBe(true)
    await click(button(container, 'Show more')); await settle()
    expect(api.to('/portal/alcs').at(-1)!.get('page')).toBe('2')
    expect(options(container)).toHaveLength(51)
  })

  it('searches for an ALC outside the loaded page and keeps the selected ALC visible', async () => {
    const api = setup()
    const { container } = await render(<SbuActivities />)
    await typeText(container.querySelector<HTMLInputElement>('input[aria-label="Search ALCs"]')!, 'Centre 120')
    await settle(SEARCH_DEBOUNCE_MS)
    expect(api.to('/portal/alcs').at(-1)!.get('search')).toBe('Centre 120')
    expect(options(container)).toContain('57210120 · Centre 120')
    await selectValue(select(container), 'alc-120')
    await settle()
    // The activity list is filtered immediately (a select is not debounced), from page 1.
    const list = api.to('/portal/verification').at(-1)!
    expect([list.get('alc_id'), list.get('page')]).toEqual(['alc-120', '1'])
    // Search for something else: the selected ALC is no longer in the options page but stays shown.
    await setInput(container.querySelector<HTMLInputElement>('input[aria-label="Search ALCs"]')!, 'Centre 3')
    await settle(SEARCH_DEBOUNCE_MS)
    expect(select(container).value).toBe('alc-120')
    expect(select(container).selectedOptions[0]!.textContent).toBe('57210120 · Centre 120')
  })

  it('renders the Phase 4F lean list fields', async () => {
    setup()
    const { container } = await render(<SbuActivities />)
    const rows = [...container.querySelectorAll('tbody tr')].map(r => r.textContent)
    expect(rows[0]).toContain('ACT-2026-1')
    expect(rows[0]).toContain('3') // evidence_count
    expect(rows[1]).toContain('0')
  })
})

// ---------------------------------------------------------------------------------------------
describe('DCU activity monitoring', () => {
  it('debounces search, keeps selects immediate, and renders lean correction fields', async () => {
    const api = fakeApi(get, (path, p) => {
      if (path === '/portal/sbus') return { items: [{ id: 'sbu-4', code: 'SBU 4' }] }
      if (path === '/portal/lookups/alcs') return { items: [] }
      return page([queueRow(activity(9, { status: 'RESUBMITTED', had_correction: true, resubmitted_at: '2026-08-05T09:00:00Z', evidence_count: 4 }))], p)
    })
    const { container } = await render(<DcuActivities queueOnly />)
    expect(container.textContent).toContain('Yes') // had_correction
    expect(container.querySelector('tbody')!.textContent).toContain('4') // evidence_count
    const initial = api.to('/portal/verification').length
    await typeText(inputByPlaceholder(container, 'Activity number, ALC code or name'), 'ACT-2026')
    await settle(SEARCH_DEBOUNCE_MS)
    expect(api.to('/portal/verification').length - initial).toBe(1)
    await selectValue(container.querySelector('select')!, 'sbu-4')
    await settle()
    const last = api.to('/portal/verification').at(-1)!
    expect([last.get('sbu_id'), last.get('search'), last.get('page')]).toEqual(['sbu-4', 'ACT-2026', '1'])
    expect(api.to('/portal/verification').length - initial).toBe(2)
  })
})
