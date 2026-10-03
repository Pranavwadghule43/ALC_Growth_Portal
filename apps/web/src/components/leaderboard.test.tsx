// @vitest-environment jsdom
// Region Top 10, All ALC Scores and the score reference: reusable components composed into
// each role's dashboard. Each owns its query and its loading / error / empty states, so a
// leaderboard failure never breaks the page. ALC Performance shows only personal performance.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../lib/api'
import { button, click, deferred, fakeApi, render, selectValue, settle } from '../test/harness'
import AdminDashboard from '../pages/AdminDashboard'
import AlcDashboard from '../pages/AlcDashboard'
import DcuDashboard from '../pages/DcuDashboard'
import Performance from '../pages/Performance'
import SbuDashboard from '../pages/SbuDashboard'
import { AllRegionScores, RegionTop10, RegionTop10ScoreReference, type RegionTop10Data, type RegionTop10Item } from './leaderboard'

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

// The complete regional ranking: two DCUs, four SBUs. Ranks/scores are regional and fixed.
const SCORES_URL = '/leaderboard/region-scores'
const nashik = { code: 'DCU_NASHIK', name: 'DCU Nashik' }
const puneNorth = { code: 'DCU_PUNE_NORTH', name: 'DCU Pune North' }
function scored(rank: number, code: string, sbu: string, dcu: typeof nashik, score: number): RegionTop10Item {
  return { rank, alc_id: `id-${code}`, alc_code: code, alc_name: `Centre ${code}`, sbu: { code: sbu, name: `${sbu} name` }, dcu, verified_leads: 100 - rank, verified_admissions: 10, activities_done: 3, partners: 2, score }
}
const allScores: RegionTop10Data = {
  period: 'LIFETIME', generated_at: '2026-10-03T10:00:00Z',
  items: [
    scored(1, 'A1', 'SBU 4', nashik, 91.25), scored(2, 'B1', 'SBU_PN_1', puneNorth, 85),
    scored(3, 'A2', 'SBU 6', nashik, 70.5), scored(4, 'B2', 'SBU_PN_2', puneNorth, 60),
    scored(5, 'A3', 'SBU 4', nashik, 40), scored(6, 'B3', 'SBU_PN_1', puneNorth, 12.34),
  ],
}

const BOARD = 'section[aria-labelledby="region-top10-title"]'
const DIRECTORY = 'section[aria-labelledby="region-scores-title"]'
const REFERENCE = 'section[aria-labelledby="region-top10-reference-title"]'
const PERSONAL = ['Record activity', 'Verified learners', 'Growth Challenge', 'Recent activities', 'Upcoming tasks', 'Recent verification results']

function directoryRows(container: Element) {
  return [...container.querySelectorAll(`${DIRECTORY} tbody tr`)].map(tr => [...tr.querySelectorAll('td')].map(td => td.textContent))
}
const shown = (container: Element) => directoryRows(container).map(r => [r[0], r[1], r[9]])  // rank, code, score
const select = (container: Element, name: string) => container.querySelector<HTMLSelectElement>(`${DIRECTORY} select[aria-label="${name}"]`)!
const options = (el: HTMLSelectElement) => [...el.options].map(o => o.textContent)

function leaderboardApi(dashboardPath?: string, dashboard?: unknown) {
  return fakeApi(get, path => {
    if (path === URL) return top
    if (path === SCORES_URL) return allScores
    if (path === dashboardPath) return dashboard
    throw new Error(path)
  })
}

describe('dashboard composition', () => {
  it.each([
    ['ADMIN', <AdminDashboard />, '/admin/dashboard', adminDash, 'Administration Overview'],
    ['DCU', <DcuDashboard />, '/portal/dashboard', dcuDash, 'DCU Nashik'],
    ['SBU', <SbuDashboard />, '/portal/dashboard', sbuDash, 'SBU Dashboard'],
  ])('%s: existing dashboard, Region Top 10, All ALC Scores, then the score reference last', async (_role, ui, dashboardPath, dashboard, title) => {
    const api = leaderboardApi(dashboardPath, dashboard)
    const { container } = await render(ui)
    expect(container.querySelector('h1')?.textContent).toBe(title)
    const board = container.querySelectorAll(BOARD)
    const directory = container.querySelectorAll(DIRECTORY)
    const reference = container.querySelectorAll(REFERENCE)
    expect([board.length, directory.length, reference.length]).toEqual([1, 1, 1])
    expect(board[0]!.nextElementSibling).toBe(directory[0])
    expect(directory[0]!.nextElementSibling).toBe(reference[0])
    expect(reference[0]!.parentElement!.lastElementChild).toBe(reference[0])
    expect(reference[0]!.textContent).toContain('How the Region Top 10 Score Is Calculated')
    expect(reference[0]!.textContent).toContain('For reference only')
    expect(board[0]!.querySelectorAll('select')).toHaveLength(0)  // the Top 10 has no filters
    expect(select(container, 'DCU')).not.toBeNull()
    expect(select(container, 'SBU')).not.toBeNull()
    expect(rows(container).map(r => r[1])).toEqual(['57210164', '57210168'])
    expect(directoryRows(container)).toHaveLength(6)  // regional, not limited to the user's unit
    // The reference is static: only the dashboard and the two leaderboard requests are made.
    expect(api.calls.sort()).toEqual([dashboardPath, URL, SCORES_URL].sort())
  })

  it('ALC dashboard: only Region Top 10 and All ALC Scores, no personal performance', async () => {
    const api = leaderboardApi()
    const { container } = await render(<AlcDashboard />)
    expect(container.querySelector('h1')?.textContent).toBe('ALC Dashboard')
    const board = container.querySelectorAll(BOARD)
    const directory = container.querySelectorAll(DIRECTORY)
    expect([board.length, directory.length]).toEqual([1, 1])
    expect(board[0]!.nextElementSibling).toBe(directory[0])
    expect(directory[0]!.parentElement!.lastElementChild).toBe(directory[0])
    expect(board[0]!.querySelectorAll('select')).toHaveLength(0)
    expect(select(container, 'DCU')).not.toBeNull()
    expect(select(container, 'SBU')).not.toBeNull()
    expect(container.querySelectorAll(REFERENCE)).toHaveLength(0)
    for (const phrase of PERSONAL) expect(container.textContent).not.toContain(phrase)
    expect(api.calls.sort()).toEqual([URL, SCORES_URL].sort())  // no personal dashboard request
  })

  it('ALC Performance: complete personal performance, no leaderboard', async () => {
    const api = leaderboardApi('/portal/dashboard', alcDash)
    const { container } = await render(<Performance />)
    expect(container.querySelector('h1')?.textContent).toBe('Performance')
    for (const phrase of PERSONAL) expect(container.textContent).toContain(phrase)
    expect(container.textContent).toContain('Verified admissions')
    expect(container.textContent).toContain('Active partnerships')
    for (const sel of [BOARD, DIRECTORY, REFERENCE]) expect(container.querySelectorAll(sel)).toHaveLength(0)
    expect(container.querySelectorAll('select')).toHaveLength(0)
    expect(api.calls).toEqual(['/portal/dashboard'])  // unchanged personal data source only
  })

  it('a leaderboard failure leaves the dashboard and the reference working', async () => {
    fakeApi(get, path => { if (path === URL || path === SCORES_URL) throw new ApiError('Leaderboard unavailable', 500); return sbuDash })
    const { container } = await render(<SbuDashboard />)
    expect(container.querySelector('h1')?.textContent).toBe('SBU Dashboard')
    expect(container.textContent).toContain('Verified admissions')
    expect(container.querySelector(BOARD)!.textContent).toContain('Leaderboard unavailable')
    expect(container.querySelector(DIRECTORY)!.textContent).toContain('Leaderboard unavailable')
    expect(container.querySelectorAll(REFERENCE)).toHaveLength(1)
  })
})

describe('AllRegionScores', () => {
  const regional = allScores.items.map(x => [String(x.rank), x.alc_code, x.score.toFixed(2)])
  const only = (...codes: string[]) => regional.filter(r => codes.includes(r[1]!))

  it('defaults to All DCUs / All SBUs and shows every ALC with regional rank and score', async () => {
    const api = leaderboardApi()
    const { container } = await render(<AllRegionScores />)
    expect([...container.querySelectorAll(`${DIRECTORY} thead th`)].map(th => th.textContent)).toEqual([
      'Regional Rank', 'ALC Code', 'ALC Name', 'SBU', 'DCU', 'Verified Leads', 'Verified Admissions', 'Verified Activities', 'Active Partners', 'Regional Score',
    ])
    expect(select(container, 'DCU').value).toBe('')
    expect(select(container, 'SBU').value).toBe('')
    expect(options(select(container, 'DCU'))).toEqual(['All DCUs', 'DCU Nashik', 'DCU Pune North'])
    expect(options(select(container, 'SBU'))).toEqual(['All SBUs', 'SBU 4', 'SBU 6', 'SBU_PN_1', 'SBU_PN_2'])
    expect(shown(container)).toEqual(regional)
    expect(container.textContent).toContain('Showing 6 of 6 ALCs')
    expect(container.querySelectorAll(`${DIRECTORY} a`)).toHaveLength(0)
    expect(api.calls).toEqual([SCORES_URL])
  })

  it('a DCU filter narrows rows and SBU options, keeping regional ranks and scores', async () => {
    const api = leaderboardApi()
    const { container } = await render(<AllRegionScores />)
    await selectValue(select(container, 'DCU'), 'DCU_PUNE_NORTH')
    expect(shown(container)).toEqual(only('B1', 'B2', 'B3'))  // ranks 2, 4, 6 — not renumbered
    expect(options(select(container, 'SBU'))).toEqual(['All SBUs', 'SBU_PN_1', 'SBU_PN_2'])
    expect(container.textContent).toContain('Showing 3 of 6 ALCs')
    expect(api.calls).toEqual([SCORES_URL])  // filtering makes no request and rescoring is impossible
  })

  it('an SBU filter shows only that SBU', async () => {
    leaderboardApi()
    const { container } = await render(<AllRegionScores />)
    await selectValue(select(container, 'DCU'), 'DCU_NASHIK')
    await selectValue(select(container, 'SBU'), 'SBU 4')
    expect(shown(container)).toEqual(only('A1', 'A3'))
  })

  it('changing DCU resets an SBU that no longer belongs to it', async () => {
    leaderboardApi()
    const { container } = await render(<AllRegionScores />)
    await selectValue(select(container, 'SBU'), 'SBU_PN_1')
    expect(shown(container)).toEqual(only('B1', 'B3'))
    await selectValue(select(container, 'DCU'), 'DCU_PUNE_NORTH')  // still valid: kept
    expect(select(container, 'SBU').value).toBe('SBU_PN_1')
    await selectValue(select(container, 'DCU'), 'DCU_NASHIK')  // invalid now: reset
    expect(select(container, 'SBU').value).toBe('')
    expect(shown(container)).toEqual(only('A1', 'A2', 'A3'))
  })

  it('clearing the filters restores the full regional list', async () => {
    leaderboardApi()
    const { container } = await render(<AllRegionScores />)
    await selectValue(select(container, 'DCU'), 'DCU_NASHIK')
    await selectValue(select(container, 'SBU'), 'SBU 6')
    expect(shown(container)).toEqual(only('A2'))
    await click(button(container, 'Clear filters'))
    expect([select(container, 'DCU').value, select(container, 'SBU').value]).toEqual(['', ''])
    expect(shown(container)).toEqual(regional)
  })

  it('shows its own loading, empty and error states', async () => {
    const pending = deferred<RegionTop10Data>()
    fakeApi(get, () => pending.promise)
    const loading = await render(<AllRegionScores />)
    expect(loading.container.textContent).toContain('Loading ALC scores')
    pending.resolve({ ...allScores, items: [] })
    await settle()
    expect(loading.container.textContent).toContain('No scored ALCs yet')
    expect(loading.container.querySelector('select')).toBeNull()
    fakeApi(get, () => { throw new ApiError('Scores unavailable', 500) })
    const failed = await render(<AllRegionScores />)
    expect(failed.container.textContent).toContain('Scores unavailable')
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
