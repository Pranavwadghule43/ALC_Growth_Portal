// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../lib/api'
import type { Role, User } from '../types'
import { button, click, fakeApi, page, render, selectValue, setInput, settle } from '../test/harness'
import YearlySubmissions from './YearlySubmissions'

vi.mock('../lib/api', () => ({ ApiError: class extends Error {}, api: {
  get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), uploadEvidence: vi.fn(), appUrl: (p: string) => p,
} }))
const get = vi.mocked(api.get) as unknown as ReturnType<typeof vi.fn>
const post = vi.mocked(api.post); const patch = vi.mocked(api.patch)
const run = { id: 'run-1', status: 'ACTIVE', scheduled_end_date: '2026-11-04' }
const row = { id: 'participant', alc_id: 'alc-1', alc_code: '10000001', alc_name: 'Centre One', sbu_name: 'SBU One', activity_name: 'Career Expo', submission_id: 'sub-1', status: 'SUBMITTED', deadline: '2026-11-04', yearly_verified_count: 1, yearly_pending_count: 1, yearly_correction_count: 0 }
let state = 'SUBMITTED'
let role: Role = 'DCU'
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-04T06:00:00Z')); vi.clearAllMocks(); state = 'SUBMITTED'; role = 'DCU'; post.mockResolvedValue({}); patch.mockResolvedValue({}) })
afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })
function setup({ count = 1, empty = false } = {}) {
  return fakeApi(get, (path, params) => {
    if (path.endsWith('/completions')) return { sbus: [{ id: 'sbu-1', name: 'SBU One' }] }
    if (path.endsWith('/submissions')) return page(Array.from({ length: count }, (_, n) => ({ ...row, id: `p-${n}`, alc_name: `Centre ${n}`, submission_id: empty ? null : 'sub-1', status: empty ? 'NOT_SUBMITTED' : state })), params, { summary: { [state]: count } })
    if (path.endsWith('/submissions/sub-1')) return { id: 'sub-1', alc_code: row.alc_code, alc_name: row.alc_name, status: state, revision: state === 'DRAFT' ? 0 : 1, completion_date: '2026-11-02', remarks: 'Completed the regional career event', can_edit: role === 'ALC' && ['DRAFT','CORRECTION_REQUIRED'].includes(state), can_review: role === 'DCU' && ['SUBMITTED','RESUBMITTED'].includes(state), scheduled_start_date: '2026-11-01', scheduled_end_date: '2026-11-04', verified_at: state === 'VERIFIED' ? '2026-11-04T06:00:00Z' : null, submitted_at: '2026-11-02T06:00:00Z', resubmitted_at: null, run_status: 'ACTIVE', upload_limits: { max_files: 10, max_bytes: 10485760 }, evidence: [], reviews: state === 'CORRECTION_REQUIRED' ? [{ id: 'review', revision: 1, decision: 'REQUEST_CORRECTION', remarks: 'Add a video', reviewed_at: '2026-11-03T06:00:00Z' }] : [] }
    throw new Error(path)
  })
}
async function mount(next: Role = 'DCU') {
  role = next
  const user = { id: 'u', role: next, dcu_id: 'dcu', alc_id: 'alc-1' } as User
  return render(<YearlySubmissions run={run} year={2026} user={user} onSaved={vi.fn()} />)
}
async function open(container: Element) { await click(button(container, 'Open submission')); await settle() }
async function textarea(container: Element, value: string) {
  const el = container.querySelector<HTMLTextAreaElement>('textarea[aria-label="DCU review remarks"]')!
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })) })
}
describe('yearly evidence submissions', () => {
  it('lets an owning ALC create and save an evidence draft', async () => {
    setup({ empty: true }); state = 'DRAFT'; post.mockResolvedValue({ id: 'sub-1' })
    const { container } = await mount('ALC')
    await click(button(container, 'Create draft')); await settle()
    expect(post).toHaveBeenCalledWith('/yearly-activities/runs/run-1/submission', { remarks: '' })
    await click(button(container, 'Save draft')); await settle()
    expect(patch).toHaveBeenCalledWith('/yearly-activities/submissions/sub-1', { completion_date: '2026-11-02', remarks: 'Completed the regional career event' })
  })
  it('shows correction remarks and resubmits after saving', async () => {
    setup(); state = 'CORRECTION_REQUIRED'
    const { container } = await mount('ALC'); await open(container)
    expect(container.textContent).toContain('Add a video')
    await click(button(container, 'Resubmit for verification')); await settle()
    expect(post).toHaveBeenCalledWith('/yearly-activities/submissions/sub-1/submit')
  })
  it('requires DCU confirmation before verifying the submitted evidence version', async () => {
    setup(); const { container } = await mount(); await open(container)
    await click(button(container, 'Verify')); expect(post).not.toHaveBeenCalled()
    await click(button(container, 'Record DCU decision')); await settle()
    expect(post).toHaveBeenCalledWith('/yearly-activities/submissions/sub-1/review', { decision: 'VERIFY', revision: 1, remarks: null })
  })
  it('requires correction/rejection remarks and sends an explicit reason', async () => {
    setup(); const { container } = await mount(); await open(container)
    await click(button(container, 'Request correction')); await settle()
    expect(container.textContent).toContain('Review remarks are required')
    expect(post).not.toHaveBeenCalled()
    await textarea(container, 'Upload a clear video'); await click(button(container, 'Reject'))
    await click(button(container, 'Record DCU decision')); await settle()
    expect(post).toHaveBeenCalledWith('/yearly-activities/submissions/sub-1/review', { decision: 'REJECT', revision: 1, remarks: 'Upload a clear video' })
  })
  it.each<Role>(['SBU','ADMIN'])('%s is read-only and cannot review or submit', async who => {
    setup(); const { container } = await mount(who); await open(container)
    expect(container.querySelector('fieldset')!.disabled).toBe(true)
    expect(container.textContent).not.toContain('Upload evidence')
    expect([...container.querySelectorAll('button')].some(b => b.textContent === 'Verify')).toBe(false)
    expect(post).not.toHaveBeenCalled(); expect(patch).not.toHaveBeenCalled()
  })
  it('locks verified ALC records and shows verification time', async () => {
    setup(); state = 'VERIFIED'; const { container } = await mount('ALC'); await open(container)
    expect(container.textContent).toContain('Verified and locked')
    expect(container.querySelector('fieldset')!.disabled).toBe(true)
    expect(container.querySelector('input[type="file"]')).toBeNull()
  })
  it('shows a failed evidence upload without claiming submission success', async () => {
    setup(); state = 'DRAFT'; vi.mocked(api.uploadEvidence).mockRejectedValue(new ApiError('Storage unavailable', 503))
    const { container } = await mount('ALC'); await open(container)
    const file = container.querySelector<HTMLInputElement>('input[type="file"]')!
    await act(async () => { Object.defineProperty(file, 'files', { configurable: true, value: [new File(['proof'], 'a.mp4', { type: 'video/mp4' })] }); file.dispatchEvent(new Event('change', { bubbles: true })) }); await settle()
    expect(api.uploadEvidence).toHaveBeenCalled()
    expect(container.textContent).toContain('Storage unavailable')
    expect(post).not.toHaveBeenCalled()
  })
  it('paginates large queues and resets paging for search/status/SBU filters', async () => {
    const calls = setup({ count: 80 }); const { container } = await mount()
    await click(button(container, 'Next')); await settle()
    expect(calls.to('/yearly-activities/submissions').at(-1)!.get('page')).toBe('2')
    await setInput(container.querySelector<HTMLInputElement>('input[aria-label="Search submissions"]')!, 'Centre 7'); await settle(400)
    expect(calls.to('/yearly-activities/submissions').at(-1)!.get('page')).toBe('1')
    await selectValue(container.querySelector('select[aria-label="Submission SBU"]')!, 'sbu-1'); await settle()
    expect(calls.to('/yearly-activities/submissions').at(-1)!.get('sbu_id')).toBe('sbu-1')
    await selectValue(container.querySelector('select[aria-label="Submission status"]')!, 'RESUBMITTED'); await settle()
    expect(calls.to('/yearly-activities/submissions').at(-1)!.get('status')).toBe('RESUBMITTED')
  })
  it('preserves the current submission when a scoped review fails', async () => {
    setup(); post.mockRejectedValue(new ApiError('Submission moved outside scope', 404))
    const { container } = await mount(); await open(container)
    await click(button(container, 'Verify')); await click(button(container, 'Record DCU decision')); await settle()
    expect(container.textContent).toContain('Submission moved outside scope')
  })
})
