// @vitest-environment jsdom
// Evidence is previewed and opened through the authenticated application API
// (/api/.../evidence/<id>/content), never from object storage: no MinIO/S3 URL, bucket or
// object key is ever rendered, and a storage URL from the API would be refused.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { Route, Routes } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { click, deferred, fakeApi, render, settle } from '../test/harness'
import ActivityEditor from '../pages/ActivityEditor'
import type { Activity, Evidence, RemovedEvidence } from '../types'
import { EvidenceGallery } from './review'

// The real URL resolution, on a production page (same-origin API at /api). vi.mock is hoisted,
// so the page URL is written inline.
vi.mock('../lib/api', async () => {
  const { applicationUrl } = await vi.importActual<typeof import('../lib/apiUrl')>('../lib/apiUrl')
  class ApiError extends Error { constructor(message: string, public status: number) { super(message) } }
  return {
    ApiError,
    api: {
      get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), downloadUrl: (p: string) => p,
      appUrl: (p: string) => applicationUrl(p, '/api', 'https://portal.example/portal/activities/act-1'),
    },
  }
})
const get = vi.mocked(api.get) as unknown as ReturnType<typeof vi.fn>
const openWindow = vi.fn()

beforeEach(() => { vi.useFakeTimers(); get.mockReset(); openWindow.mockReset(); vi.stubGlobal('open', openWindow) })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); document.body.innerHTML = '' })

const photo: Evidence = { id: 'ev-photo', original_filename: 'photo.png', mime_type: 'image/png', file_size: 2 * 1024 * 1024, uploaded_at: '2026-10-01T10:00:00Z' }
const proof: Evidence = { id: 'ev-proof', original_filename: 'proof.pdf', mime_type: 'application/pdf', file_size: 1024 * 1024, uploaded_at: '2026-10-01T10:00:00Z' }
const removed: RemovedEvidence = { ...proof, id: 'ev-old', original_filename: 'old.pdf', submitted_in: [1], recorded: true }

const portalAccess = (id: string) => `/portal/evidence/${id}/access`
const adminAccess = (id: string) => `/admin/evidence/${id}/access`
const content = (scope: string, id: string) => `/api/${scope}/evidence/${id}/content`
const STORAGE_LEAKS = ['9000', 'minio', 'localhost', '127.0.0.1', 'alc-evidence', 'X-Amz', 'evidence/alc', 's3']

// The access check answers with the application content URL (root-relative), as the API does.
function appAccess(scope: 'portal' | 'admin') {
  return fakeApi(get, path => {
    const id = path.match(/evidence\/([^/]+)\/access$/)?.[1]
    if (!id) throw new Error(path)
    return { url: content(scope, id) }
  })
}

function expectNoStorageDetails(container: Element) {
  const html = container.innerHTML
  for (const leak of STORAGE_LEAKS) expect(html).not.toContain(leak)
}

function cards(container: Element) {
  return [...container.querySelectorAll('section button[title]')] as HTMLButtonElement[]
}

describe('EvidenceGallery', () => {
  it('shows images from the application API endpoint, not from storage', async () => {
    const calls = appAccess('portal')
    const { container } = await render(<EvidenceGallery evidence={[photo, proof]} accessPath={portalAccess} />)
    expect(calls.calls).toEqual([portalAccess('ev-photo'), portalAccess('ev-proof')])
    const img = container.querySelector('img')!
    expect(img.getAttribute('src')).toBe(`https://portal.example${content('portal', 'ev-photo')}`)
    expect(container.textContent).toContain('Image · click to preview · 2.0 MB')
    expectNoStorageDetails(container)
  })

  it('previews an image and opens a PDF through the application API', async () => {
    appAccess('portal')
    const { container } = await render(<EvidenceGallery evidence={[photo, proof]} removed={[removed]} accessPath={portalAccess} />)
    const [imageCard, pdfCard, removedCard] = cards(container)
    await click(imageCard!)
    const preview = container.querySelector('[role="dialog"] img')!
    expect(preview.getAttribute('src')).toBe(`https://portal.example${content('portal', 'ev-photo')}`)
    await click(pdfCard!)
    expect(openWindow).toHaveBeenLastCalledWith(`https://portal.example${content('portal', 'ev-proof')}`, '_blank', 'noopener,noreferrer')
    await click(removedCard!)  // historical evidence uses the same authorised route
    expect(openWindow).toHaveBeenLastCalledWith(`https://portal.example${content('portal', 'ev-old')}`, '_blank', 'noopener,noreferrer')
    expectNoStorageDetails(container)
  })

  it('uses the admin routes on the admin review page', async () => {
    const calls = appAccess('admin')
    const { container } = await render(<EvidenceGallery evidence={[photo]} accessPath={adminAccess} />)
    expect(calls.calls).toEqual([adminAccess('ev-photo')])
    expect(container.querySelector('img')!.getAttribute('src')).toBe(`https://portal.example${content('admin', 'ev-photo')}`)
  })

  it('is disabled while the access check is loading', async () => {
    const pending = deferred<{ url: string }>()
    fakeApi(get, () => pending.promise)
    const { container } = await render(<EvidenceGallery evidence={[photo]} accessPath={portalAccess} />)
    const [card] = cards(container)
    expect(card!.disabled).toBe(true)
    expect(container.querySelector('img')).toBeNull()
    pending.resolve({ url: content('portal', 'ev-photo') })
    await settle()
    expect(card!.disabled).toBe(false)
    expect(container.querySelector('img')).not.toBeNull()
  })

  it('shows Unavailable when access is denied or the evidence is missing', async () => {
    fakeApi(get, () => { throw new ApiError('Evidence not found', 404) })
    const { container } = await render(<EvidenceGallery evidence={[photo, proof]} accessPath={portalAccess} />)
    expect(cards(container).every(c => c.disabled)).toBe(true)
    expect(container.textContent!.match(/Unavailable/g)).toHaveLength(2)
    expect(container.querySelector('img')).toBeNull()
  })

  it('refuses a storage URL instead of rendering it', async () => {
    fakeApi(get, () => ({ url: 'http://127.0.0.1:9000/alc-evidence/evidence/alc/act/ev-photo.png?X-Amz-Signature=abc' }))
    const { container } = await render(<EvidenceGallery evidence={[photo]} accessPath={portalAccess} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('Unavailable')
    expect(cards(container)[0]!.disabled).toBe(true)
    expectNoStorageDetails(container)
  })

  it('shows Unavailable when the file itself cannot be loaded', async () => {
    appAccess('portal')
    const { container } = await render(<EvidenceGallery evidence={[photo]} accessPath={portalAccess} />)
    const img = container.querySelector('img')!
    await act(async () => { img.dispatchEvent(new Event('error')) })
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('Unavailable')
    expect(cards(container)[0]!.disabled).toBe(true)
  })
})

describe('ActivityEditor evidence', () => {
  const activity = {
    id: 'act-1', activity_number: 'ACT-0001', alc_id: 'alc-1', activity_type: 'Partner meeting', ecosystem: 'College',
    activity_date: '2026-10-01', location: 'Pune', learners_reached: 10, leads_generated: 2, admissions_generated: 0,
    description: 'Discussed a structured learner outreach pilot.', outcome: 'Pilot agreed', status: 'DRAFT',
    evidence: [proof], removed_evidence: [], reviews: [], revisions: [], created_at: '2026-10-01T10:00:00Z', updated_at: '2026-10-01T10:00:00Z',
  } as unknown as Activity

  async function renderEditor(access: (path: string) => unknown) {
    fakeApi(get, path => {
      if (path === '/portal/activities/act-1') return activity
      if (path === '/portal/partners') return []
      if (path.endsWith('/access')) return access(path)
      throw new Error(path)
    })
    return render(<Routes><Route path="/portal/activities/:id" element={<ActivityEditor />} /></Routes>, '/portal/activities/act-1')
  }

  function fileButton(container: Element) {
    const found = [...container.querySelectorAll('button')].find(b => b.textContent === 'proof.pdf')
    if (!found) throw new Error('No evidence button')
    return found
  }

  it('opens the ALC\'s own evidence through the application API', async () => {
    const { container } = await renderEditor(() => ({ url: content('portal', 'ev-proof') }))
    await click(fileButton(container))
    await settle()
    expect(get).toHaveBeenCalledWith(portalAccess('ev-proof'))
    expect(openWindow).toHaveBeenCalledWith(`https://portal.example${content('portal', 'ev-proof')}`, '_blank', 'noopener,noreferrer')
    expectNoStorageDetails(container)
  })

  it('never opens a storage URL', async () => {
    const { container } = await renderEditor(() => ({ url: 'http://localhost:9000/alc-evidence/evidence/x.pdf' }))
    await click(fileButton(container))
    await settle()
    expect(openWindow).not.toHaveBeenCalled()
    expect(container.textContent).toContain('Unable to open evidence')
  })
})
