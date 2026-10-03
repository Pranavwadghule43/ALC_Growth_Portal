// @vitest-environment jsdom
// Region Top 10: one reusable component on every role's dashboard. It owns its query, shows
// its own loading / error / empty states, and a leaderboard failure never breaks the dashboard.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../lib/api'
import { deferred, fakeApi, render, settle } from '../test/harness'
import AdminDashboard from '../pages/AdminDashboard'
import AlcDashboard from '../pages/AlcDashboard'
import DcuDashboard from '../pages/DcuDashboard'
import Performance from '../pages/Performance'
import SbuDashboard from '../pages/SbuDashboard'
import { RegionTop10, RegionTop10ScoreReference, type RegionTop10Data } from './leaderboard'

vi.mock('../lib/api', () => {
  class ApiError extends Error { constructor(message: string, public status: number) { super(message) } }
  return { ApiError, api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), downloadUrl: (p: string) => p } }
})
const get = vi.mocked(api.get) as unknown as ReturnType<typeof vi.fn>

// jsdom has no ResizeObserver; the Admin dashboard's recharts containers need one to mount.
class NoopResizeObserver { observe() {} unobserve() {} disconnect() {} }

beforeEach(() => { vi.useFakeTimers(); get.mockReset(); vi.stubGlobal('ResizeObserver', NoopResizeObserver) })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); document.body.innerHTML = '' })

const URL = '/leaderboard/region-top10'
const top: RegionTop10Data = {
  period: 'LIFETIME', generated_at: '2026-10-03T10:00:00Z',
  items: [
    { rank: 1, alc_id: 'a1', alc_code: '57210164', alc_name: 'Jayesh Computers', sbu: { code: 'SBU 4', name: 'SBU Four' }, dcu: { code: 'DCU_NASHIK', name: 'DCU Nashik' }, verified_leads: 1250, verified_admissions: 40, activities_done: 12, partners: 6, score: 87.5 },
    { rank: 2, alc_id: 'a2', alc_code: '57210168', alc_name: 'Shree Computers', sbu: { code: 'SBU 7', name: 'SBU Seven' }, dcu: { code: 'DCU_NASHIK', name: 'DCU Nashik' }, verified_leads: 90, verified_admissions: 3, activities_done: 4, partners: 1, score: 33.33 },
  ],
}

function rows(container: Element) {
  return [...container.querySelectorAll('section[aria-labelledby="region-top10-title"] tbody tr')]
    .map(tr => [...tr.querySelectorAll('td')].map(td => td.textContent))
}

describe('RegionTop10', () => {
  it('renders the ranked rows with every column and a 2-decimal score', async () => {
    const api = fakeApi(get, path => { if (path === URL) return top; throw new Error(path) })
    const { container } = await render(<RegionTop10 />)
    expect(api.calls).toEqual([URL])
    expect([...container.querySelectorAll('thead th')].map(th => th.textContent)).toEqual([
      'Rank', 'ALC Code', 'ALC Name', 'SBU', 'DCU', 'Verified Leads', 'Verified Admissions', 'Verified Activities', 'Active Partners', 'Score',
    ])
    expect(rows(container)).toEqual([
      ['1', '57210164', 'Jayesh Computers', 'SBU 4', 'DCU Nashik', '1,250', '40', '12', '6', '87.50'],
      ['2', '57210168', 'Shree Computers', 'SBU 7', 'DCU Nashik', '90', '3', '4', '1', '33.33'],
    ])
  })

  it('never links rows to ALC detail pages', async () => {
    fakeApi(get, () => top)
    const { container } = await render(<RegionTop10 />)
    expect(container.querySelectorAll('a')).toHaveLength(0)
  })

  it('shows a loading state until the ranking arrives', async () => {
    const pending = deferred<RegionTop10Data>()
    fakeApi(get, () => pending.promise)
    const { container } = await render(<RegionTop10 />)
    expect(container.textContent).toContain('Loading Region Top 10')
    pending.resolve(top)
    await settle()
    expect(rows(container)).toHaveLength(2)
  })

  it('shows an empty state when no ALC qualifies', async () => {
    fakeApi(get, () => ({ ...top, items: [] }))
    const { container } = await render(<RegionTop10 />)
    expect(container.textContent).toContain('No ranked ALCs yet')
    expect(container.querySelector('table')).toBeNull()
  })

  it('shows its own error state', async () => {
    fakeApi(get, () => { throw new ApiError('Leaderboard unavailable', 500) })
    const { container } = await render(<RegionTop10 />)
    expect(container.textContent).toContain('Leaderboard unavailable')
  })
})

const sbuDash = { assigned_alcs: 3, active_alcs: 3, partners: 2, activities: 5, submitted: 1, pending: 1, verified: 3, corrections: 0, rejected: 1, learners: 10, leads: 20, admissions: 2, recent_activities: [] }
const dcuDash = { ...sbuDash, role: 'DCU', unit: { type: 'DCU', id: 'd1', code: 'DCU_NASHIK', name: 'DCU Nashik' }, sbus: 1, inactive_alcs: 0, resubmitted: 0, sbu_breakdown: [] }
const alcDash = { activities: 5, pending: 1, verified: 3, corrections: 0, rejected: 1, learners: 10, leads: 20, admissions: 2, active_partners: 2, recent_activities: [], upcoming_tasks: [], notifications: [], challenge: { status: 'NOT_CONFIGURED', targets: {}, achieved: {} } }
const adminDash = { total_alcs: 3, active_alcs: 3, activities_submitted: 5, pending_verification: 1, verified_activities: 3, correction_required: 0, rejected_activities: 1, verified_learners: 10, verified_leads: 20, verified_admissions: 2, active_partnerships: 2, status_distribution: [], submission_trend: [], verified_categories: [] }

const BOARD = 'section[aria-labelledby="region-top10-title"]'
const REFERENCE = 'section[aria-labelledby="region-top10-reference-title"]'

describe('Region Top 10 on every dashboard', () => {
  it.each([
    ['ADMIN', <AdminDashboard />, '/admin/dashboard', adminDash, 'Administration Overview'],
    ['DCU', <DcuDashboard />, '/portal/dashboard', dcuDash, 'DCU Nashik'],
    ['SBU', <SbuDashboard />, '/portal/dashboard', sbuDash, 'SBU Dashboard'],
  ])('%s dashboard: Region Top 10, then the score reference as the last section', async (_role, ui, dashboardPath, dashboard, title) => {
    const api = fakeApi(get, path => path === URL ? top : path === dashboardPath ? dashboard : (() => { throw new Error(path) })())
    const { container } = await render(ui)
    expect(container.querySelector('h1')?.textContent).toBe(title)
    const boards = container.querySelectorAll(BOARD)
    const references = container.querySelectorAll(REFERENCE)
    expect(boards).toHaveLength(1)
    expect(references).toHaveLength(1)
    expect(boards[0]!.nextElementSibling).toBe(references[0])
    expect(references[0]!.parentElement!.lastElementChild).toBe(references[0])
    expect(references[0]!.textContent).toContain('How the Region Top 10 Score Is Calculated')
    expect(references[0]!.textContent).toContain('For reference only')
    expect(rows(container).map(r => r[1])).toEqual(['57210164', '57210168'])
    // The reference is static: the only calls are the dashboard and one leaderboard request.
    expect(api.calls.sort()).toEqual([dashboardPath, URL].sort())
  })

  it.each([
    ['ALC', <AlcDashboard />],
    ['ALC Performance', <Performance />],
  ])('%s: Region Top 10 is the last section and there is no score reference', async (_role, ui) => {
    const api = fakeApi(get, path => path === URL ? top : path === '/portal/dashboard' ? alcDash : (() => { throw new Error(path) })())
    const { container } = await render(ui)
    expect(container.querySelector('h1')?.textContent).toBe('ALC Dashboard')
    const boards = container.querySelectorAll(BOARD)
    expect(boards).toHaveLength(1)
    expect(boards[0]!.parentElement!.lastElementChild).toBe(boards[0])
    expect(container.querySelectorAll(REFERENCE)).toHaveLength(0)
    expect(container.textContent).not.toContain('How the Region Top 10 Score Is Calculated')
    expect(rows(container).map(r => r[1])).toEqual(['57210164', '57210168'])
    expect(api.to(URL)).toHaveLength(1)
  })

  it('a leaderboard failure leaves the dashboard and the reference working', async () => {
    fakeApi(get, path => { if (path === URL) throw new ApiError('Leaderboard unavailable', 500); return sbuDash })
    const { container } = await render(<SbuDashboard />)
    expect(container.querySelector('h1')?.textContent).toBe('SBU Dashboard')
    expect(container.textContent).toContain('Verified admissions')
    expect(container.textContent).toContain('Leaderboard unavailable')
    expect(container.querySelectorAll(REFERENCE)).toHaveLength(1)
  })
})

describe('RegionTop10ScoreReference', () => {
  it('shows the four equal 25% components, the formula, the example and the notes', async () => {
    const { container } = await render(<RegionTop10ScoreReference />)
    const text = container.textContent ?? ''
    for (const phrase of [
      'How the Region Top 10 Score Is Calculated', 'For reference only', 'Final Score', 'Example', '70.00',
      'There are four approved performance metrics and each is given equal importance. Therefore each metric contributes 25% of the final score.',
      'Each eligible ALC is compared with all other eligible ALCs in the region for each of the four performance metrics.',
      'A higher percentile score means the ALC performs better than more ALCs in the region for that metric.',
    ]) expect(text).toContain(phrase)
    const weights = [...container.querySelectorAll('ul[aria-label="Metric weights"] li')].map(li => [li.querySelector('p')?.textContent, li.querySelectorAll('p')[1]?.textContent])
    expect(weights).toEqual([
      ['Verified Leads', '25%'], ['Verified Admissions', '25%'], ['Verified Activities', '25%'], ['Active Partners', '25%'],
    ])
    expect([...container.querySelectorAll('div[aria-label="Final score formula"] p')].map(p => p.textContent)).toEqual([
      'Final Score =', '(Verified Leads Score × 25%)', '+ (Verified Admissions Score × 25%)', '+ (Verified Activities Score × 25%)', '+ (Active Partners Score × 25%)',
    ])
    expect([...container.querySelectorAll('tbody tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.textContent))).toEqual([
      ['Verified Leads Score', '80', '× 25%', '20.00'], ['Verified Admissions Score', '60', '× 25%', '15.00'],
      ['Verified Activities Score', '100', '× 25%', '25.00'], ['Active Partners Score', '40', '× 25%', '10.00'],
    ])
    expect(container.querySelector('tfoot')?.textContent).toBe('Final Score = 20 + 15 + 25 + 1070.00')
    const notes = [...container.querySelectorAll('ul[aria-label="Score notes"] li')].map(li => li.textContent)
    expect(notes).toContain('Only ACTIVE ALCs under an ACTIVE SBU, ACTIVE DCU and ACTIVE RCU participate.')
    expect(notes).toContain('Ranking uses the full, unrounded score.')
    expect(notes).toHaveLength(10)
  })

  it('is read-only: no inputs, buttons, links or API calls', async () => {
    const { container } = await render(<RegionTop10ScoreReference />)
    expect(container.querySelectorAll('input, select, textarea, button, a')).toHaveLength(0)
    expect(get).not.toHaveBeenCalled()
  })
})
