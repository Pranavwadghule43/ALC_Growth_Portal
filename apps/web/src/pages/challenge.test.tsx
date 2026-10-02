// @vitest-environment jsdom
// Growth Challenge: variable duration and global configuration. The period (15, 30, 45, 60 ...
// days) and all day counts come from the API; these tests check that nothing in the UI assumes
// 30 days, that "not configured" is shown as such, and that an Admin can configure the period.
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import { challengePeriodSummary, challengeTimePercent, inclusiveDays, type ChallengePeriod } from '../lib/challenge'
import { button, click, fakeApi, page, render, setInput, settle } from '../test/harness'
import AdminChallenge from './AdminChallenge'
import Challenge from './Challenge'

vi.mock('../lib/api', () => {
  class ApiError extends Error { constructor(message: string, public status: number) { super(message) } }
  return { ApiError, api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), downloadUrl: (p: string) => p } }
})
const get = vi.mocked(api.get) as unknown as ReturnType<typeof vi.fn>
const post = vi.mocked(api.post) as unknown as ReturnType<typeof vi.fn>
const patch = vi.mocked(api.patch) as unknown as ReturnType<typeof vi.fn>

beforeEach(() => { vi.useFakeTimers(); get.mockReset(); post.mockReset(); patch.mockReset() })
afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })

const targets = { prospects: 40, meetings: 20, pilots: 10, partnerships: 5 }
const achieved = { prospects: 10, meetings: 5, pilots: 1, partnerships: 0 }

// A configured period of ``total`` days on its ``elapsed``-th day, as the API reports it.
function active(total: number, elapsed: number, extra: Partial<ChallengePeriod> = {}): ChallengePeriod {
  return {
    period_start: '2026-03-01', period_end: '2026-04-30', configured: true, status: 'ACTIVE', total_days: total,
    elapsed_days: elapsed, remaining_days: total - elapsed, days_until_start: 0,
    time_progress_percent: Math.round(elapsed / total * 1000) / 10, name: 'Spring challenge', period_source: 'GLOBAL',
    challenge_id: 'gc-1', ...extra,
  }
}
const notConfigured: ChallengePeriod = {
  period_start: null, period_end: null, configured: false, status: 'NOT_CONFIGURED', total_days: 0,
  elapsed_days: 0, remaining_days: 0, days_until_start: 0, time_progress_percent: 0, name: null, period_source: null,
}

describe('challenge period summary', () => {
  it.each([15, 30, 45, 60, 90])('describes day 10 of a %i-day challenge', total => {
    expect(challengePeriodSummary(active(total, 10))).toBe(`Day 10 of ${total} · ${total - 10} days remaining`)
  })

  it('handles the first day, the day before the end and the final day', () => {
    expect(challengePeriodSummary(active(45, 1))).toBe('Day 1 of 45 · 44 days remaining')
    expect(challengePeriodSummary(active(45, 44))).toBe('Day 44 of 45 · 1 day remaining')
    expect(challengePeriodSummary(active(45, 45))).toBe('Day 45 of 45 · final day')
  })

  it('describes upcoming, completed and not-configured states', () => {
    const upcoming = active(60, 0, { status: 'UPCOMING', remaining_days: 60, days_until_start: 7 })
    expect(challengePeriodSummary(upcoming)).toBe('60-day challenge · starts in 7 days')
    expect(challengePeriodSummary({ ...upcoming, days_until_start: 1 })).toBe('60-day challenge · starts in 1 day')
    expect(challengePeriodSummary(active(15, 15, { status: 'COMPLETED' }))).toBe('15-day challenge · completed')
    expect(challengePeriodSummary(notConfigured)).toBe('Not configured')
  })

  it('is empty when the API sends no period information', () => {
    expect(challengePeriodSummary(undefined)).toBe('')
    expect(challengePeriodSummary({ period_start: '2026-03-01', period_end: '2026-03-30' })).toBe('')
  })

  it('reports time progress against the configured total, clamped to 0–100', () => {
    expect(challengeTimePercent(active(15, 15))).toBe(100)
    expect(challengeTimePercent(active(30, 15))).toBe(50)
    expect(challengeTimePercent(active(60, 15))).toBe(25)
    expect(challengeTimePercent({ time_progress_percent: 140 })).toBe(100)
    expect(challengeTimePercent({ time_progress_percent: -5 })).toBe(0)
    expect(challengeTimePercent(undefined)).toBe(0)
  })

  it('counts both dates when measuring a period', () => {
    expect(inclusiveDays('2026-03-10', '2026-03-24')).toBe(15)
    expect(inclusiveDays('2026-03-10', '2026-04-08')).toBe(30)
    expect(inclusiveDays('2026-03-10', '2026-04-23')).toBe(45)
    expect(inclusiveDays('2026-03-10', '2026-05-08')).toBe(60)
    expect(inclusiveDays('2026-03-10', '2026-06-07')).toBe(90)
    expect(inclusiveDays('2026-03-10', '2026-03-10')).toBe(1)
    expect(inclusiveDays('2028-02-15', '2028-03-15')).toBe(30) // leap year
    expect(inclusiveDays('2026-12-18', '2027-02-15')).toBe(60) // across New Year
    expect(inclusiveDays('2026-03-10', '2027-09-10')).toBe(550) // longer than a year
    expect(inclusiveDays('2026-03-10', '2026-03-09')).toBe(0) // end before start
    expect(inclusiveDays('2026-03-10', '')).toBe(0)
  })
})

describe('ALC Growth Challenge page', () => {
  const show = async (period: Partial<ChallengePeriod>, extra: Record<string, unknown> = {}) => {
    fakeApi(get, () => ({ ...period, targets, achieved, source: 'verified activities', ...extra }))
    return (await render(<Challenge />)).container
  }

  it.each([15, 45, 60, 90])('shows the configured %i-day period, not 30 days', async total => {
    const container = await show(active(total, 10))
    expect(container.textContent).toContain('Growth Challenge')
    expect(container.textContent).toContain('Spring challenge')
    expect(container.textContent).toContain(`Day 10 of ${total} · ${total - 10} days remaining`)
    const bar = container.querySelector('[role="progressbar"]')!
    expect(bar.getAttribute('aria-valuenow')).toBe(String(Math.round(10 / total * 1000) / 10))
    expect(container.textContent).toContain('10 / 40') // target progress is unchanged
    expect(container.textContent).toContain('25% of target')
  })

  it('shows upcoming and completed challenges with their status', async () => {
    const upcoming = await show(active(30, 0, { status: 'UPCOMING', remaining_days: 30, days_until_start: 4, time_progress_percent: 0 }))
    expect(upcoming.textContent).toContain('30-day challenge · starts in 4 days')
    document.body.innerHTML = ''
    const completed = await show(active(45, 45, { status: 'COMPLETED', remaining_days: 0 }))
    expect(completed.textContent).toContain('45-day challenge · completed')
  })

  it('says so when no challenge is configured instead of showing a 30-day window', async () => {
    const container = await show(notConfigured, { targets: {}, achieved: {} })
    expect(container.textContent).toContain('No Growth Challenge is running')
    expect(container.textContent).not.toContain('of target')
    expect(container.textContent).not.toContain('30')
    expect(container.querySelector('[role="progressbar"]')).toBeNull()
  })

  it('still renders a response without the new period fields', async () => {
    const container = await show({ period_start: '2026-02-09', period_end: '2026-03-10' })
    expect(container.textContent).toContain('prospects')
    expect(container.textContent).not.toContain('Challenge period')
  })

  it('does not divide by a zero target', async () => {
    const container = await show(active(30, 10), { targets: { ...targets, pilots: 0 } })
    expect(container.textContent).not.toContain('NaN')
    expect(container.textContent).not.toContain('Infinity')
  })
})

describe('Admin Growth Challenge overview', () => {
  const rows = [
    { id: 'alc-1', alc_code: '57210001', alc_name: 'Centre 1', prospects: 12, meetings: 3, pilots: 1, partnerships: 0 },
    { id: 'alc-2', alc_code: '57210002', alc_name: 'Centre 2', prospects: 4, meetings: 1, pilots: 0, partnerships: 0 },
  ]
  const config = { id: 'gc-1', name: 'Spring challenge', start_date: '2026-03-01', end_date: '2026-04-29', targets, status: 'ACTIVE', total_days: 60 }
  const configured = { targets, default_period: { ...active(60, 10), targets }, periods: {}, challenges: [config], today: '2026-03-10' }
  const empty = { targets, default_period: { ...notConfigured, targets: {} }, periods: {}, challenges: [], today: '2026-03-10' }
  const overview = async (extra: Record<string, unknown>) => {
    const fake = fakeApi(get, (_path, p) => page(rows, p, extra))
    return { fake, ...(await render(<AdminChallenge />)) }
  }
  const field = (container: Element, id: string) => container.querySelector<HTMLInputElement>(`#${id}`)!
  const submit = async (container: Element) => {
    await act(async () => { container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })) })
    await settle()
  }

  it('shows the global period for every ALC and an own period where one exists', async () => {
    const periods = { 'alc-1': { ...active(15, 5, { period_source: 'ALC_OVERRIDE', name: null }), targets: { ...targets, prospects: 80 } } }
    const { container } = await overview({ ...configured, periods })
    expect(container.querySelector('[aria-label="Challenge period"]')!.textContent).toContain('Spring challenge')
    expect(container.querySelector('[aria-label="Challenge period"]')!.textContent).toContain('Day 10 of 60 · 50 days remaining')
    const [first, second] = [...container.querySelectorAll('tbody tr')].map(tr => tr.textContent ?? '')
    expect(first).toContain('Day 5 of 15 · 10 days remaining · own period')
    expect(first).toContain('12 / 80') // this ALC's own target
    expect(second).toContain('Day 10 of 60 · 50 days remaining')
    expect(second).toContain('4 / 40') // global target
  })

  it('says the challenge is not configured and shows no 30-day numbers', async () => {
    const { container } = await overview(empty)
    expect(container.textContent).toContain('No Growth Challenge is configured')
    expect(button(container, 'Configure challenge')).toBeTruthy()
    const body = container.querySelector('tbody')!.textContent ?? ''
    expect(body).toContain('Not configured')
    expect(body).not.toContain('/ 40')
    expect(body).not.toContain('12')
  })

  it('creates a challenge of any length from the configuration form', async () => {
    post.mockResolvedValue({})
    const { container, fake } = await overview(empty)
    await click(button(container, 'Configure challenge'))
    expect(field(container, 'gc-start').value).toBe('2026-03-10') // the server's "today" (India)
    await setInput(field(container, 'gc-name'), ' Summer drive ')
    await setInput(field(container, 'gc-end'), '2026-04-23')
    await setInput(field(container, 'gc-prospects'), '90')
    expect(container.textContent).toContain('Duration: 45 days (both dates included).')
    const before = fake.to('/admin/challenge').length
    await submit(container)
    expect(post).toHaveBeenCalledWith('/admin/growth-challenges', {
      name: 'Summer drive', start_date: '2026-03-10', end_date: '2026-04-23',
      prospects_target: 90, meetings_target: 20, pilots_target: 10, partnerships_target: 5,
    })
    expect(patch).not.toHaveBeenCalled()
    expect(container.querySelector('form')).toBeNull() // closed after saving
    expect(fake.to('/admin/challenge').length).toBe(before + 1) // overview reloaded
  })

  it('saves a challenge longer than 366 days (no maximum), with a non-blocking note', async () => {
    post.mockResolvedValue({})
    const { container } = await overview(empty)
    await click(button(container, 'Configure challenge'))
    await setInput(field(container, 'gc-end'), '2026-04-08') // 30 days
    expect(container.querySelector('[role="note"]')).toBeNull()
    await setInput(field(container, 'gc-end'), '2027-09-10') // 550 days
    expect(container.textContent).toContain('Duration: 550 days (both dates included).')
    expect(container.querySelector('[role="note"]')!.textContent).toContain('you can still save it')
    await submit(container)
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(post).toHaveBeenCalledWith('/admin/growth-challenges', expect.objectContaining({ start_date: '2026-03-10', end_date: '2027-09-10' }))
  })

  it('edits the current challenge', async () => {
    patch.mockResolvedValue({})
    const { container } = await overview(configured)
    await click(button(container, 'Edit period'))
    expect(field(container, 'gc-name').value).toBe('Spring challenge')
    expect(field(container, 'gc-end').value).toBe('2026-04-29')
    await setInput(field(container, 'gc-end'), '2026-05-29')
    expect(container.textContent).toContain('Duration: 90 days (both dates included).')
    await submit(container)
    expect(patch).toHaveBeenCalledWith('/admin/growth-challenges/gc-1', {
      name: 'Spring challenge', start_date: '2026-03-01', end_date: '2026-05-29',
      prospects_target: 40, meetings_target: 20, pilots_target: 10, partnerships_target: 5,
    })
    expect(post).not.toHaveBeenCalled()
  })

  it('does not submit an end date before the start date', async () => {
    const { container } = await overview(empty)
    await click(button(container, 'Configure challenge'))
    await setInput(field(container, 'gc-end'), '2026-03-01')
    await submit(container)
    expect(post).not.toHaveBeenCalled()
    expect(container.querySelector('[role="alert"]')!.textContent).toContain('End date must be on or after the start date')
  })

  it('shows the server message when the period overlaps another challenge', async () => {
    post.mockRejectedValue(new Error('Overlaps Growth Challenge "Spring challenge" (2026-03-01 to 2026-04-29). Challenge periods cannot overlap.'))
    const { container } = await overview(configured)
    await click(button(container, 'Add period'))
    await setInput(field(container, 'gc-end'), '2026-03-20')
    await submit(container)
    expect(container.querySelector('[role="alert"]')!.textContent).toContain('Challenge periods cannot overlap')
    expect(container.querySelector('form')).not.toBeNull() // stays open so it can be corrected
  })

  it('still renders a response without period information', async () => {
    const { container } = await overview({ targets })
    expect(container.textContent).toContain('12 / 40')
    expect(container.textContent).toContain('Centre 2')
    expect(container.querySelector('[aria-label="Challenge period"]')).toBeNull()
  })
})
