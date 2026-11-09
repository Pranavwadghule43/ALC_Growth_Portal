import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { toQuery } from '../lib/constants'
import { useDebouncedSearch, usePageFor } from '../lib/hooks'
import type { User } from '../types'
import { Badge, ConfirmDialog, Empty, ErrorState, Loading, Pager } from './ui'

const BASE = '/yearly-activities'
interface Run { id: string; status: string; scheduled_end_date: string | null }
interface Row {
  id: string; alc_id: string; alc_code: string; alc_name: string; sbu_name: string
  activity_name: string; submission_id: string | null; status: string; deadline: string
  yearly_verified_count: number; yearly_pending_count: number; yearly_correction_count: number
}
interface Page { items: Row[]; total: number; page: number; pages: number; summary: Record<string, number> }
interface Evidence { id: string; original_filename: string; mime_type: string; is_active: boolean }
interface Detail {
  id: string; alc_code: string; alc_name: string; status: string; revision: number
  completion_date: string | null; remarks: string; can_edit: boolean; can_review: boolean
  scheduled_start_date: string; scheduled_end_date: string; verified_at: string | null
  updated_at?: string; submitted_at: string | null; resubmitted_at: string | null; run_status: string
  upload_limits: { max_files: number; max_bytes: number }; evidence: Evidence[]
  reviews: { id: string; revision: number; decision: string; remarks: string | null; reviewed_at: string }[]
}
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date())

function Media({ evidence }: { evidence: Evidence }) {
  const access = useQuery({ queryKey: ['yearly-media', evidence.id], queryFn: () => api.get<{ url: string }>(`${BASE}/evidence/${evidence.id}/access`) })
  const url = access.data ? api.appUrl(access.data.url) : null
  return <div className="rounded-md border p-3"><p className="mb-2 break-all text-sm font-semibold">{evidence.original_filename}{!evidence.is_active && ' · Removed, retained in history'}</p>
    {access.error ? <ErrorState error={access.error} /> : !url ? <p className="text-sm">Loading evidence…</p> : evidence.mime_type.startsWith('video/') ? <video aria-label={evidence.original_filename} controls preload="metadata" src={url} className="max-h-72 w-full" /> : evidence.mime_type.startsWith('image/') ? <a href={url} target="_blank" rel="noopener noreferrer"><img src={url} alt={evidence.original_filename} loading="lazy" className="max-h-64 object-contain" /></a> : <a className="text-brand underline" href={url} target="_blank" rel="noopener noreferrer">Open document</a>}
  </div>
}

function Editor({ data, onSaved }: { data: Detail; onSaved: () => void }) {
  const [date, setDate] = useState(data.completion_date ?? '')
  const [remarks, setRemarks] = useState(data.remarks)
  const [reviewRemarks, setReviewRemarks] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [progress, setProgress] = useState<number | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  async function perform(work: () => Promise<unknown>, success: string) {
    setBusy(true); setError(''); setNotice('')
    try { await work(); setNotice(success); onSaved() }
    catch (e) { setError(e instanceof Error ? e.message : 'Unable to save submission') }
    finally { setBusy(false); setProgress(null) }
  }
  async function save(submit: boolean) {
    await perform(async () => {
      await api.patch(`${BASE}/submissions/${data.id}`, { completion_date: date || null, remarks })
      if (submit) await api.post(`${BASE}/submissions/${data.id}/submit`)
    }, submit ? 'Submitted for DCU review.' : 'Draft changes saved.')
  }
  function review(decision: string) {
    if (decision !== 'VERIFY' && !reviewRemarks.trim()) { setError('Review remarks are required.'); return }
    setPending(decision)
  }
  return <section className="mt-4 rounded-lg border bg-slate-50 p-4" aria-label="Yearly submission details">
    <div className="flex flex-wrap justify-between gap-3"><h3 className="font-semibold text-navy">{data.alc_code} · {data.alc_name}</h3><Badge status={data.status} /></div>
    <p className="my-2 text-sm">Version {data.revision} · Submitted: {data.resubmitted_at ?? data.submitted_at ?? 'Not submitted'}{data.verified_at && ` · Verified: ${data.verified_at}`}</p>
    {data.status === 'CORRECTION_REQUIRED' && <p role="note" className="my-3 rounded border border-amber-300 bg-amber-50 p-3">DCU correction: {data.reviews[data.reviews.length - 1]?.remarks}. Correct your details/evidence and resubmit.</p>}
    {data.status === 'VERIFIED' && <p className="my-3 text-sm text-emerald-800">Verified and locked. Regional points remain pending until every participating DCU finalizes.</p>}
    {data.status === 'REJECTED' && <p className="my-3 text-sm text-red-800">Rejected and closed: {data.reviews[data.reviews.length - 1]?.remarks}</p>}
    <fieldset disabled={!data.can_edit || busy} className="grid gap-3 sm:grid-cols-2">
      <label>Completion date<input aria-label="Completion date" type="date" min={data.scheduled_start_date} max={data.scheduled_end_date < today() ? data.scheduled_end_date : today()} value={date} onChange={e => setDate(e.target.value)} /></label>
      <label className="sm:col-span-2">Completion details<textarea aria-label="Completion details" maxLength={10000} value={remarks} onChange={e => setRemarks(e.target.value)} /></label>
    </fieldset>
    {data.can_edit && <div className="my-3 flex flex-wrap gap-3"><button className="btn-secondary" disabled={busy} onClick={() => void save(false)}>Save draft</button><button className="btn-primary" disabled={busy} onClick={() => void save(true)}>{data.status === 'CORRECTION_REQUIRED' ? 'Resubmit for verification' : 'Submit for verification'}</button>
      <label className="btn-secondary cursor-pointer">Upload evidence<input aria-label="Upload yearly evidence" type="file" className="mt-1 block text-xs" accept="image/jpeg,image/png,image/webp,application/pdf,video/mp4,video/webm" disabled={busy} onChange={e => {
        const file = e.target.files?.[0]; e.target.value = ''; if (!file) return
        if (file.size > data.upload_limits.max_bytes) { setError('File exceeds the configured limit.'); return }
        void perform(() => api.uploadEvidence(`${BASE}/submissions/${data.id}/evidence`, file, setProgress), 'Evidence uploaded.')
      }} /></label></div>}
    <p className="my-2 text-xs text-slate-600">Images, PDF, MP4 or WebM · up to {(data.upload_limits.max_bytes / 1048576).toFixed(0)} MiB each · {data.upload_limits.max_files} current files · one file per request.</p>
    {progress !== null && <p role="status">Upload: {progress}%</p>}
    <div className="grid gap-3 md:grid-cols-2">{data.evidence.map(file => <div key={file.id}><Media evidence={file} />{data.can_edit && file.is_active && <button disabled={busy} className="btn-ghost text-red-700" onClick={() => void perform(() => api.delete(`${BASE}/evidence/${file.id}`), 'Evidence removed; historical copy retained.')}>Remove {file.original_filename}</button>}</div>)}</div>
    {data.can_review && <div className="mt-4 border-t pt-4"><label>DCU review remarks<textarea aria-label="DCU review remarks" maxLength={4000} disabled={busy} value={reviewRemarks} onChange={e => setReviewRemarks(e.target.value)} /></label><div className="mt-3 flex flex-wrap gap-3"><button className="btn-primary" disabled={busy} onClick={() => review('VERIFY')}>Verify</button><button className="btn-secondary" disabled={busy} onClick={() => review('REQUEST_CORRECTION')}>Request correction</button><button className="btn-secondary text-red-700" disabled={busy} onClick={() => review('REJECT')}>Reject</button></div></div>}
    <h4 className="mt-4 font-semibold">Review history</h4><ol className="mt-2 space-y-2">{data.reviews.map(r => <li key={r.id} className="border-l-2 border-line pl-3 text-sm">Version {r.revision} · {r.decision.replaceAll('_', ' ')} · {r.reviewed_at}<p className="whitespace-pre-wrap">{r.remarks}</p></li>)}</ol>
    {error && <p role="alert" className="mt-3 text-red-700">{error}</p>}{notice && <p role="status" className="mt-3 text-emerald-800">{notice}</p>}
    {pending && <ConfirmDialog title={`Confirm ${pending.toLowerCase().replaceAll('_', ' ')}?`} body="This decision is recorded against the current evidence version. Verified and rejected submissions are locked." confirmLabel="Record DCU decision" busy={busy} onCancel={() => setPending(null)} onConfirm={() => void perform(async () => { await api.post(`${BASE}/submissions/${data.id}/review`, { decision: pending, revision: data.revision, remarks: reviewRemarks || null }); setPending(null) }, 'Review recorded.')} />}
  </section>
}

export default function YearlySubmissions({ run, year, user, onSaved }: { run: Run; year: number; user: User; onSaved: () => void }) {
  const client = useQueryClient()
  const [searchText, setSearchText] = useState(''); const search = useDebouncedSearch(searchText)
  const [status, setStatus] = useState(''); const [sbu, setSbu] = useState('')
  const [page, setPage] = usePageFor(`${run.id}:${search}:${status}:${sbu}`)
  const [selected, setSelected] = useState<string | null>(null)
  const [busy, setBusy] = useState(false); const [error, setError] = useState('')
  const query = toQuery({ year, run_id: run.id, q: search, status, sbu_id: sbu, page, page_size: 25 })
  const list = useQuery({ queryKey: ['yearly-submissions', query], queryFn: () => api.get<Page>(`${BASE}/submissions?${query}`) })
  const units = useQuery({ queryKey: ['yearly-units', run.id], queryFn: () => api.get<{ sbus: { id: string; name: string }[] }>(`${BASE}/runs/${run.id}/completions?page_size=1`) })
  const detail = useQuery({ queryKey: ['yearly-submission', selected], queryFn: () => api.get<Detail>(`${BASE}/submissions/${selected}`), enabled: !!selected })
  function refresh() { void client.invalidateQueries({ queryKey: ['yearly-submissions'] }); void client.invalidateQueries({ queryKey: ['yearly-submission'] }); onSaved() }
  async function create() { setBusy(true); setError(''); try { const saved = await api.post<Detail>(`${BASE}/runs/${run.id}/submission`, { remarks: '' }); setSelected(saved.id); refresh() } catch(e) { setError(e instanceof Error ? e.message : 'Unable to create draft') } finally { setBusy(false) } }
  return <section className="mt-4 border-t pt-4" aria-label="Yearly submissions">
    <h3 className="font-semibold text-navy">{user.role === 'DCU' ? 'DCU review queue' : 'ALC submissions'}</h3>
    <div className="my-3 flex flex-wrap gap-3"><label className="flex-1">Search ALC<input aria-label="Search submissions" value={searchText} maxLength={180} onChange={e => setSearchText(e.target.value)} /></label><label>Submission status<select aria-label="Submission status" value={status} onChange={e => setStatus(e.target.value)}><option value="">All statuses</option>{['DRAFT','SUBMITTED','RESUBMITTED','CORRECTION_REQUIRED','VERIFIED','REJECTED'].map(s => <option key={s} value={s}>{s.replaceAll('_', ' ')}</option>)}</select></label><label>SBU<select aria-label="Submission SBU" value={sbu} onChange={e => setSbu(e.target.value)}><option value="">All authorized SBUs</option>{units.data?.sbus.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label></div>
    {error && <p role="alert">{error}</p>}
    {list.isLoading ? <Loading /> : list.error || !list.data ? <ErrorState error={list.error} /> : <>
      <p className="mb-3 text-sm">Verified: {list.data.summary.VERIFIED ?? 0} · Pending review: {(list.data.summary.SUBMITTED ?? 0) + (list.data.summary.RESUBMITTED ?? 0)} · Correction: {list.data.summary.CORRECTION_REQUIRED ?? 0} · Rejected: {list.data.summary.REJECTED ?? 0}</p>
      {!list.data.items.length ? <Empty title="No submissions match" message="Try another filter or wait for activity activation." /> : <div className="table-wrap"><table className="table-dense"><thead><tr><th>ALC</th><th>SBU</th><th>Status</th><th>Deadline</th><th>Year verified / pending / correction</th><th>Action</th></tr></thead><tbody>{list.data.items.map(row => <tr key={row.id}><td>{row.alc_code} · {row.alc_name}</td><td>{row.sbu_name}</td><td><Badge status={row.status} /></td><td>{row.deadline}</td><td>{row.yearly_verified_count} / {row.yearly_pending_count} / {row.yearly_correction_count}</td><td>{row.submission_id ? (row.status !== 'DRAFT' || user.role === 'ALC' ? <button className="btn-secondary" onClick={() => setSelected(row.submission_id)}>Open submission</button> : 'Awaiting ALC submission') : user.role === 'ALC' && ['ACTIVE','MARKING'].includes(run.status) && (!run.scheduled_end_date || today() <= run.scheduled_end_date) ? <button className="btn-primary" disabled={busy} onClick={() => void create()}>Create draft</button> : 'Not submitted'}</td></tr>)}</tbody></table></div>}
      <Pager page={list.data.page} pages={list.data.pages} total={list.data.total} noun="participants" onPage={setPage} />
    </>}
    {selected && <><button className="btn-ghost" onClick={() => setSelected(null)}>Close submission</button>{detail.isLoading ? <Loading /> : detail.error || !detail.data ? <ErrorState error={detail.error} /> : <Editor key={`${detail.data.id}:${detail.data.updated_at ?? detail.data.revision}`} data={detail.data} onSaved={refresh} />}</>}
  </section>
}
