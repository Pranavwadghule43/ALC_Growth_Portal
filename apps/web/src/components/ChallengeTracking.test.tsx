// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import { fakeApi, render } from '../test/harness'
import ChallengeTracking from './ChallengeTracking'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
const get = vi.mocked(api.get) as unknown as ReturnType<typeof vi.fn>

beforeEach(() => { vi.useFakeTimers(); get.mockReset() })
afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })

const challenge = { id: 'gc1', name: 'Monsoon Outreach', description: 'Verified outreach', start_date: '2026-07-01', end_date: '2026-07-31', status: 'ACTIVE', targets: { prospects: 10, meetings: 2, pilots: 1, partnerships: 1 } }
const progress = {
  challenge,
  targets: challenge.targets,
  summary: { eligible: 2, participating: 1, completed: 1, in_progress: 0, not_started: 1, completion_percent: 50 },
  sbu_breakdown: [{ sbu_id: 's1', sbu_code: 'SBU 4', sbu_name: 'Nashik Four', eligible: 2, participating: 1, completed: 1, completion_percent: 50 }],
  items: [
    { id: 'a1', alc_code: '57210164', alc_name: 'Jayesh', sbu_code: 'SBU 4', prospects: 10, meetings: 2, pilots: 1, partnerships: 1, activity_count: 2, evidence_count: 1, progress_status: 'COMPLETED' },
    { id: 'a2', alc_code: '57210168', alc_name: 'Shree', sbu_code: 'SBU 4', prospects: 0, meetings: 0, pilots: 0, partnerships: 0, activity_count: 0, evidence_count: 0, progress_status: 'NOT_STARTED' },
  ],
  page: 1, pages: 1, total: 2,
}

describe('ChallengeTracking', () => {
  it('renders scoped counts, SBU breakdown and ALC progress', async () => {
    const calls = fakeApi(get, path => {
      if (path === '/portal/growth-challenges') return { items: [challenge] }
      if (path === '/portal/growth-challenges/gc1/progress') return progress
      throw new Error(path)
    })
    const { container } = await render(<ChallengeTracking role="DCU" />)
    expect(container.textContent).toContain('Monsoon Outreach')
    expect(container.textContent).toContain('Eligible ALCs2')
    expect(container.textContent).toContain('Completion50%')
    expect(container.textContent).toContain('SBU 4')
    expect(container.textContent).toContain('57210164')
    expect(container.textContent).toContain('COMPLETED')
    expect(calls.calls.some(path => path.startsWith('/portal/growth-challenges/gc1/progress?'))).toBe(true)
  })

  it('shows an empty state when Admin has published nothing', async () => {
    fakeApi(get, path => path === '/portal/growth-challenges' ? { items: [] } : progress)
    const { container } = await render(<ChallengeTracking role="SBU" />)
    expect(container.textContent).toContain('No published Growth Challenge')
  })
})
