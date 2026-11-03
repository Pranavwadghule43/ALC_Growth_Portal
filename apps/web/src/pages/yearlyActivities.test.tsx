// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Outlet, Route, Routes } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { SEARCH_DEBOUNCE_MS } from '../lib/hooks'
import type { Role, User } from '../types'
import { button, click, fakeApi, page, render, selectValue, setInput, settle } from '../test/harness'
import YearlyActivities from './YearlyActivities'

vi.mock('../lib/api', () => {
  class ApiError extends Error { constructor(message: string, public status: number) { super(message) } }
  return { ApiError, api: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }
})
const get = vi.mocked(api.get) as unknown as ReturnType<typeof vi.fn>
const patch = vi.mocked(api.patch)
const post = vi.mocked(api.post)
const run = { id: 'run-1', dcu_id: 'dcu-1', dcu_name: 'DCU Pune', status: 'MARKING', scheduled_start_date: '2026-01-01', scheduled_end_date: '2026-01-02', instructions: 'Use the official attendance record', finalized_at: null }
const definition = { id: 'activity-1', rcu_id: 'rcu-1', calendar_year: 2026, activity_code: 'CAREER-EXPO', name: 'Career Expo', description: 'Regional career activity', introduced_by_dcu: { id: 'dcu-1', code: 'PUNE', name: 'DCU Pune' }, created_at: '2026-01-01T00:00:00Z', regional_status: 'PENDING', participant_count: 2, finalized_count: 1, runs: [run] }
const summary = { completed_count: 1, total_applicable: 3, completion_percentage: 33.3333, official_completed_count: 0 }
const completion = { alc_id: 'alc-1', alc_code: '10000001', alc_name: 'Pune Centre', sbu_id: 'sbu-1', sbu_name: 'SBU One', completed: null as boolean | null, marked_at: null, updated_at: null, can_edit: true, yearly_completed_count: 1, yearly_official_completed_count: 0, yearly_total_applicable: 3 }

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-03T06:00:00Z')); get.mockReset(); patch.mockReset(); post.mockReset(); patch.mockResolvedValue({}); post.mockResolvedValue({}) })
afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })

function setup(options: { status?: string; rows?: typeof completion[]; definitionItems?: typeof definition[] } = {}) {
  const rows = options.rows ?? [{ ...completion }]
  return fakeApi(get, (path, params) => {
    if (path.endsWith('/definitions')) return page(options.definitionItems ?? [{ ...definition, runs: [{ ...run, status: options.status ?? 'MARKING' }] }], params, { summary })
    if (path.endsWith('/completions')) {
      const search = params.get('q') ?? ''
      return page(rows.filter(row => `${row.alc_code} ${row.alc_name}`.includes(search)), params, { summary: { yes: rows.filter(row => row.completed === true).length, no: rows.filter(row => row.completed === false).length, not_marked: rows.filter(row => row.completed === null).length }, sbus: [{ id: 'sbu-1', name: 'SBU One' }] })
    }
    throw new Error(path)
  })
}
async function mount(role: Role = 'DCU') {
  const user: User = { id: 'user-1', username: 'tester', role, dcu_id: 'dcu-1', alc_id: role === 'ALC' ? 'alc-1' : undefined, is_active: true, must_change_password: false }
  return render(<Routes><Route element={<Outlet context={user} />}><Route path="*" element={<YearlyActivities />} /></Route></Routes>)
}
async function showCompletions(container: Element, role: Role = 'DCU') {
  await click(button(container, role === 'DCU' ? 'Manage my DCU run' : 'View activity details')); await settle()
  await click(button(container, 'View ALC decisions')); await settle()
}
async function submit(form: HTMLFormElement) { await act(async () => { form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })) }); await settle() }

describe('Yearly Activities', () => {
  it('registers a dynamic regional identity and warns about possible duplicate codes', async () => {
    const calls = setup()
    const { container } = await mount()
    await click(button(container, 'Register new regional activity'))
    const form = container.querySelector<HTMLFormElement>('form[aria-label="Register regional activity"]')!
    const inputs = form.querySelectorAll('input')
    await setInput(inputs[0]!, 'Career Expo'); await setInput(inputs[1]!, 'career-expo'); await settle(SEARCH_DEBOUNCE_MS)
    expect(container.textContent).toContain('Possible existing activities: CAREER-EXPO')
    expect(calls.to('/yearly-activities/definitions').some(params => params.get('q') === 'CAREER-EXPO')).toBe(true)
    await submit(form)
    expect(post).toHaveBeenCalledWith('/yearly-activities/definitions', { name: 'Career Expo', activity_code: 'career-expo', description: '', calendar_year: expect.any(Number) })
    expect(container.textContent).toContain('Regional activity registered')
  })

  it('preserves NOT MARKED and saves YES, NO and a cleared decision explicitly', async () => {
    const rows = [{ ...completion }]
    setup({ rows })
    patch.mockImplementation(async (_path, body) => { rows[0]!.completed = (body as { completed: boolean | null }).completed; return {} })
    const { container } = await mount(); await showCompletions(container)
    const select = () => container.querySelector<HTMLSelectElement>('select[aria-label="Completion for 10000001"]')!
    expect(select().value).toBe('pending')
    expect(container.textContent).toContain('NOT MARKED: 1')
    for (const [value, completed] of [['yes', true], ['no', false], ['pending', null]] as const) {
      await selectValue(select(), value); await settle()
      expect(patch).toHaveBeenLastCalledWith('/yearly-activities/runs/run-1/completions/alc-1', { completed })
      expect(select().value).toBe(value)
    }
    expect(container.textContent).toContain('10000001: NOT MARKED saved.')
  })

  it.each(['ADMIN', 'SBU', 'ALC'] as Role[])('%s sees read-only scoped progress and provisional/official totals', async role => {
    const calls = setup({ rows: [{ ...completion, completed: true, can_edit: false }] })
    const { container } = await mount(role); await showCompletions(container, role)
    expect(container.textContent).toContain('33.3%')
    expect(container.textContent).toContain('Official completions')
    expect(container.textContent).toContain('Results are provisional')
    expect(container.textContent).toContain('10000001')
    expect(container.textContent).toContain('Year completed / total')
    expect(container.textContent).toContain('1 / 3')
    expect(container.querySelector('select[aria-label^="Completion for"]')).toBeNull()
    expect(container.querySelector('form[aria-label="DCU activity schedule"]')).toBeNull()
    expect([...container.querySelectorAll('button')].some(element => element.textContent?.includes('Register new'))).toBe(false)
    expect(patch).not.toHaveBeenCalled(); expect(post).not.toHaveBeenCalled()
    expect(calls.calls.some(path => path.includes('alc_id='))).toBe(false)
  })

  it('locks historical transferred ALC decisions and finalized results', async () => {
    setup({ rows: [{ ...completion, can_edit: false }] })
    const first = await mount(); await showCompletions(first.container)
    expect(first.container.textContent).toContain('Historical assignment · locked')
    expect(first.container.querySelector('select[aria-label^="Completion for"]')).toBeNull()
    await first.unmount()
    setup({ status: 'FINALIZED' })
    const second = await mount(); await showCompletions(second.container)
    expect(second.container.textContent).toContain('Completion editing is locked')
    expect(second.container.querySelector('select[aria-label^="Completion for"]')).toBeNull()
  })

  it('keeps an unchanged decision visible when saving fails', async () => {
    setup(); patch.mockRejectedValue(new ApiError('This ALC has moved outside your scope', 403))
    const { container } = await mount(); await showCompletions(container)
    const select = container.querySelector<HTMLSelectElement>('select[aria-label="Completion for 10000001"]')!
    await selectValue(select, 'yes'); await settle()
    expect(container.textContent).toContain('This ALC has moved outside your scope')
    expect(select.value).toBe('pending')
  })

  it('paginates hundreds of ALCs and resets to page one after a debounced search or SBU filter', async () => {
    const rows = Array.from({ length: 80 }, (_, index) => ({ ...completion, alc_id: `alc-${index}`, alc_code: String(10000000 + index), alc_name: `Centre ${index}` }))
    const calls = setup({ rows })
    const { container } = await mount(); await showCompletions(container)
    await click(button(container, 'Next')); await settle()
    expect(calls.to('/yearly-activities/runs/run-1/completions').at(-1)!.get('page')).toBe('2')
    await setInput(container.querySelector<HTMLInputElement>('input[placeholder="Search ALC code or name"]')!, 'Centre 7'); await settle(SEARCH_DEBOUNCE_MS)
    const last = calls.to('/yearly-activities/runs/run-1/completions').at(-1)!
    expect([last.get('page'), last.get('q'), last.get('page_size')]).toEqual(['1', 'Centre 7', '25'])
    await selectValue(container.querySelector<HTMLSelectElement>('select[aria-label="Completion SBU"]')!, 'sbu-1'); await settle()
    expect(calls.to('/yearly-activities/runs/run-1/completions').at(-1)!.get('sbu_id')).toBe('sbu-1')
  })

  it('requires an explicit finalization confirmation and shows backend rejection', async () => {
    setup(); post.mockRejectedValue(new ApiError('All participating ALCs must be marked', 409))
    const { container } = await mount()
    await click(button(container, 'Manage my DCU run'))
    await click(button(container, 'Finalize DCU activity'))
    expect(post).not.toHaveBeenCalled()
    expect(container.querySelector('[role="dialog"]')!.textContent).toContain('Finalization locks these results')
    await click(button(container, 'Finalize and lock results')); await settle()
    expect(post).toHaveBeenCalledWith('/yearly-activities/runs/run-1/finalize')
    expect(container.textContent).toContain('All participating ALCs must be marked')
  })

  it('saves only the responsible DCU schedule and transitions in order', async () => {
    setup({ status: 'AVAILABLE' })
    const { container } = await mount(); await click(button(container, 'Manage my DCU run'))
    const form = container.querySelector<HTMLFormElement>('form[aria-label="DCU activity schedule"]')!
    await setInput(form.querySelectorAll('input')[0]!, '2026-02-01'); await setInput(form.querySelectorAll('input')[1]!, '2026-02-02'); await submit(form)
    expect(patch).toHaveBeenCalledWith('/yearly-activities/runs/run-1', { scheduled_start_date: '2026-02-01', scheduled_end_date: '2026-02-02', instructions: 'Use the official attendance record', status: 'SCHEDULED' })
  })

  it('loads a selected year and survives registry errors', async () => {
    const calls = setup()
    const { container } = await mount('ALC')
    await setInput(container.querySelector<HTMLInputElement>('input[aria-label="Calendar year"]')!, '2025')
    await submit(container.querySelector('form')!)
    expect(calls.to('/yearly-activities/definitions').at(-1)!.get('year')).toBe('2025')
    expect(calls.calls.every(path => !path.includes('/history'))).toBe(true)
    get.mockRejectedValue(new ApiError('Yearly registry unavailable', 503))
    await setInput(container.querySelector<HTMLInputElement>('input[aria-label="Calendar year"]')!, '2024'); await submit(container.querySelector('form')!)
    expect(container.textContent).toContain('Yearly registry unavailable')
  })
})
