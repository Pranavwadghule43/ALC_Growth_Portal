import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FileText, History, Image as ImageIcon, RotateCcw, X } from 'lucide-react'
import { api } from '../lib/api'
import type { Activity, Decision, Evidence, RemovedEvidence, Review, Revision } from '../types'
import { Badge, ConfirmDialog, formatDate, formatNumber } from './ui'

// Shared building blocks for every activity review page (DCU, SBU, Admin). Authorization
// always happens server-side; these components only decide what to offer.

export function formatDateTime(value?: string | null) {
  return value ? new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
}

// Latest resubmission time, if the activity was ever resubmitted (revision 2+).
export function resubmittedAt(activity: Activity) {
  const revisions = activity.revisions ?? []
  return revisions.length > 1 ? revisions[revisions.length - 1]?.created_at : undefined
}

// True when the activity was sent back for correction at least once.
export function hadCorrection(activity: Activity) {
  return activity.reviews.some(r => r.new_status === 'CORRECTION_REQUIRED')
}

export function ActivityDetails({ activity: a }: { activity: Activity }) {
  const facts: [string, string][] = [
    ['Activity type', a.activity_type], ['Activity date', formatDate(a.activity_date)], ['Partner', a.partner?.partner_name ?? '—'],
    ['Ecosystem', a.ecosystem], ['Collaboration', a.collaboration_type ?? '—'], ['Location', a.location],
    ['Submitted', formatDateTime(a.submitted_at)], ['Resubmitted', formatDateTime(resubmittedAt(a))], ['Verified', formatDateTime(a.verified_at)],
  ]
  return <section className="panel p-5"><h2 className="mb-4 font-bold text-navy">Activity details</h2>
    <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{facts.map(([k, v]) => <div key={k}><dt className="text-xs font-semibold uppercase text-slate-500">{k}</dt><dd className="mt-1 text-sm font-medium">{v}</dd></div>)}</dl>
    <div className="mt-5 grid gap-4 sm:grid-cols-3">{([['Learner reach', a.learners_reached], ['Leads', a.leads_generated], ['Admissions', a.admissions_generated]] as const).map(([k, v]) => <div key={k} className="rounded-md bg-slate-50 p-4"><p className="text-xs text-slate-500">{k}</p><b className="text-xl">{formatNumber(v)}</b></div>)}</div>
    <div className="mt-5"><h3 className="text-sm font-semibold">Description</h3><p className="mt-1 whitespace-pre-wrap text-sm text-slate-600">{a.description}</p><h3 className="mt-4 text-sm font-semibold">Outcome</h3><p className="mt-1 whitespace-pre-wrap text-sm text-slate-600">{a.outcome}</p></div>
  </section>
}

function EvidenceCard({ evidence, accessPath, onPreview, tag }: { evidence: Evidence; accessPath: (id: string) => string; onPreview: (url: string, name: string) => void; tag?: string }) {
  const isPdf = evidence.mime_type === 'application/pdf'
  const { data, isError } = useQuery({ queryKey: ['evidence-url', accessPath(evidence.id)], queryFn: () => api.get<{ url: string }>(accessPath(evidence.id)) })
  const url = data?.url
  function open() { if (!url) return; if (isPdf) window.open(url, '_blank', 'noopener,noreferrer'); else onPreview(url, evidence.original_filename) }
  return <button onClick={open} disabled={!url} title={isPdf ? 'Open PDF in a new tab' : 'Preview image'} className="group flex flex-col overflow-hidden rounded-md border text-left hover:border-teal disabled:cursor-wait">
    <div className="flex h-32 items-center justify-center bg-slate-50">{isPdf ? <FileText className="h-9 w-9 text-red-700" /> : url ? <img src={url} alt={evidence.original_filename} className="h-full w-full object-cover" /> : <ImageIcon className="h-8 w-8 text-teal" />}</div>
    <span className="min-w-0 p-3">{tag && <span className="mb-1 inline-block rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-900">{tag}</span>}<b className="block truncate text-sm">{evidence.original_filename}</b><small className="text-slate-500">{isError ? 'Unavailable' : `${isPdf ? 'PDF · opens in new tab' : 'Image · click to preview'} · ${(evidence.file_size / 1024 / 1024).toFixed(1)} MB`}</small></span>
  </button>
}

// Which submission(s) a removed file was part of, e.g. "Removed · was in submission 1".
function removedTag(e: RemovedEvidence) {
  const list = e.submitted_in.join(', ')
  return e.recorded ? `Removed · was in submission ${list}` : `Removed · earlier submission`
}

// Current evidence first, then evidence the ALC removed during a correction after a reviewer
// had seen it. Removed files are opened through the same authorised access routes.
export function EvidenceGallery({ evidence, removed = [], accessPath }: { evidence: Evidence[]; removed?: RemovedEvidence[]; accessPath: (id: string) => string }) {
  const [preview, setPreview] = useState<{ url: string; name: string } | null>(null)
  const onPreview = (url: string, name: string) => setPreview({ url, name })
  return <section className="panel p-5"><h2 className="font-bold text-navy">Current evidence ({evidence.length})</h2>
    {evidence.length ? <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{evidence.map(e => <EvidenceCard key={e.id} evidence={e} accessPath={accessPath} onPreview={onPreview} />)}</div> : <p className="mt-3 text-sm text-slate-500">No evidence was attached to this activity.</p>}
    {removed.length > 0 && <div className="mt-6 border-t pt-5"><h3 className="font-bold text-navy">Historical / removed evidence ({removed.length})</h3>
      <p className="mt-1 text-sm text-slate-500">Removed by the ALC during a correction. Kept because a reviewer saw it in an earlier submission.</p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{removed.map(e => <EvidenceCard key={e.id} evidence={e} accessPath={accessPath} onPreview={onPreview} tag={removedTag(e)} />)}</div>
    </div>}
    {preview && <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/85 p-6" onClick={() => setPreview(null)} role="dialog" aria-label="Evidence preview">
      <div className="mb-3 flex w-full max-w-5xl items-center justify-between text-white"><span className="truncate text-sm">{preview.name}</span><button onClick={() => setPreview(null)} aria-label="Close preview"><X className="h-7 w-7" /></button></div>
      <img src={preview.url} alt={preview.name} className="max-h-[80vh] max-w-5xl rounded object-contain" onClick={e => e.stopPropagation()} />
    </div>}
  </section>
}

const roleLabel = (role?: string | null) => role === 'ADMIN' ? 'Admin' : role ?? 'Reviewer'
const statusLabel = (status: string) => status.replaceAll('_', ' ').toLowerCase().replace(/^\w/, c => c.toUpperCase())
const actionVerb: Record<string, string> = { VERIFY: 'verified', REQUEST_CORRECTION: 'requested a correction', REJECT: 'rejected' }

// Evidence recorded with a submission (older submissions did not record it).
function submittedEvidence(r: Revision) {
  const files = r.snapshot.evidence
  if (!Array.isArray(files)) return ''
  return `Evidence: ${files.map(f => (f as { original_filename?: string }).original_filename ?? 'file').join(', ') || 'none'}`
}

type HistoryEntry = { key: string; at: string; title: string; detail?: string; remark?: string; tone: 'submit' | 'review' | 'change' }

function historyEntries(reviews: Review[], revisions: Revision[]): HistoryEntry[] {
  const entries: HistoryEntry[] = revisions.map(r => ({
    key: `rev-${r.id}`, at: r.created_at, tone: 'submit',
    title: r.revision_number === 1 ? 'ALC submitted the activity' : `ALC resubmitted (revision ${r.revision_number})`,
    detail: [r.change_summary, submittedEvidence(r)].filter(Boolean).join(' · '),
  }))
  reviews.forEach(r => entries.push(r.is_decision_change
    ? { key: `review-${r.id}`, at: r.reviewed_at, tone: 'change', title: `${roleLabel(r.reviewer_role)} changed the decision`, detail: `${statusLabel(r.previous_status)} → ${statusLabel(r.new_status)}`, remark: r.remark }
    : { key: `review-${r.id}`, at: r.reviewed_at, tone: 'review', title: `${roleLabel(r.reviewer_role)} ${actionVerb[r.action] ?? r.action.toLowerCase()}`, detail: `${statusLabel(r.previous_status)} → ${statusLabel(r.new_status)}`, remark: r.remark }))
  return entries.sort((a, b) => a.at.localeCompare(b.at))
}

// One chronological timeline of submissions, resubmissions, review decisions and decision
// changes. Every entry is shown; nothing is ever collapsed or overwritten.
export function ReviewHistory({ reviews, revisions }: { reviews: Review[]; revisions: Revision[] }) {
  const entries = historyEntries(reviews, revisions)
  const dot = { submit: 'border-slate-300', review: 'border-teal', change: 'border-amber-500' }
  return <section className="panel p-5"><h2 className="mb-4 flex items-center gap-2 font-bold text-navy"><History className="h-4 w-4" />Review &amp; submission history</h2>
    {entries.length ? <ol className="space-y-4">{entries.map(e => <li key={e.key} className={`border-l-2 pl-4 ${dot[e.tone]}`}>
      <div className="flex flex-wrap items-baseline gap-x-2"><b className="text-sm">{e.title}</b>{e.tone === 'change' && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-900">Decision change</span>}<span className="text-xs text-slate-500">{formatDateTime(e.at)}</span></div>
      {e.detail && <p className="mt-0.5 text-xs text-slate-500">{e.detail}</p>}
      {e.remark && <p className="mt-1 rounded bg-slate-50 px-3 py-2 text-sm text-slate-700">“{e.remark}”</p>}
    </li>)}</ol> : <p className="text-sm text-slate-500">No history yet.</p>}
  </section>
}

const decisionLabel: Record<Decision, string> = { VERIFY: 'Verify', REQUEST_CORRECTION: 'Request Correction', REJECT: 'Reject' }
const decisionPath: Record<Decision, string> = { VERIFY: 'verify', REQUEST_CORRECTION: 'request-correction', REJECT: 'reject' }
const decisionResult: Record<Decision, string> = { VERIFY: 'VERIFIED', REQUEST_CORRECTION: 'CORRECTION_REQUIRED', REJECT: 'REJECTED' }
const decisionTone: Record<Decision, 'primary' | 'warning' | 'danger'> = { VERIFY: 'primary', REQUEST_CORRECTION: 'warning', REJECT: 'danger' }

// Review / change-decision panel. ``basePath`` is the activity's API path, e.g.
// ``/portal/activities/{id}`` or ``/admin/activities/{id}``.
export function DecisionPanel({ basePath, status, canReview, canChangeDecision, onDone }: { basePath: string; status: string; canReview: boolean; canChangeDecision: boolean; onDone: (message: string) => void }) {
  const [remark, setRemark] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const [changing, setChanging] = useState(false); const [pending, setPending] = useState<Decision | null>(null)
  if (!canReview && !canChangeDecision) return <section className="panel p-5"><h2 className="font-bold text-navy">Decision</h2><p className="mt-2 text-sm text-slate-600">Current status: <Badge status={status} /></p><p className="mt-3 text-sm text-slate-500">{status === 'CORRECTION_REQUIRED' ? 'Waiting for the ALC to correct and resubmit.' : 'No review action is available for this activity.'}</p></section>
  const change = !canReview && canChangeDecision
  const options = (Object.keys(decisionLabel) as Decision[]).filter(d => !change || decisionResult[d] !== status)
  function request(decision: Decision) {
    setError('')
    const needsReason = change || decision !== 'VERIFY'
    if (needsReason && !remark.trim()) { setError(change ? 'A reason is required to change a decision.' : 'A reason is required for correction or rejection.'); return }
    setPending(decision)
  }
  async function confirm() {
    if (!pending) return
    setBusy(true); setError('')
    try {
      if (change) await api.post(`${basePath}/change-decision`, { decision: pending, remark: remark.trim() })
      else await api.post(`${basePath}/${decisionPath[pending]}`, { remark: remark.trim() || null })
      const result = statusLabel(decisionResult[pending])
      setRemark(''); setChanging(false); setPending(null)
      onDone(change ? `Decision changed to ${result}.` : `Activity ${result.toLowerCase()}.`)
    } catch (e) { setError(e instanceof Error ? e.message : 'The decision could not be saved'); setPending(null) }
    finally { setBusy(false) }
  }
  return <section className="panel p-5">
    <h2 className="font-bold text-navy">{change ? 'Final decision' : 'Review decision'}</h2>
    <p className="mt-2 text-sm text-slate-600">Current status: <Badge status={status} /></p>
    {change && !changing ? <><p className="mt-3 text-sm text-slate-500">This decision is final. You can re-review it; the earlier decision stays in the history.</p><button className="btn-secondary mt-4 w-full" onClick={() => setChanging(true)}><RotateCcw className="h-4 w-4" />Change Decision</button></> : <>
      <label className="mt-4 block" htmlFor="decision-remark">{change ? 'Reason for changing the decision (required)' : 'Remark / reason'}</label>
      <textarea id="decision-remark" className="mt-1.5 min-h-28" value={remark} onChange={e => setRemark(e.target.value)} placeholder={change ? 'Explain why the earlier decision is being changed' : 'Optional for Verify; required for correction and rejection'} />
      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
      <div className="mt-4 space-y-2">{options.map(d => <button key={d} disabled={busy} onClick={() => request(d)} className={d === 'VERIFY' ? 'btn-primary w-full' : d === 'REJECT' ? 'btn-danger w-full' : 'btn-secondary w-full border-amber-400 text-amber-900'}>{decisionLabel[d]}</button>)}</div>
      {change && <button className="mt-3 w-full text-sm text-slate-500 hover:text-navy" disabled={busy} onClick={() => { setChanging(false); setError('') }}>Cancel</button>}
    </>}
    {pending && <ConfirmDialog title={change ? `Change decision to ${decisionLabel[pending]}?` : `${decisionLabel[pending]} this activity?`} tone={decisionTone[pending]} busy={busy} confirmLabel={change ? 'Change decision' : decisionLabel[pending]} onCancel={() => setPending(null)} onConfirm={confirm}
      body={<><p>Status will become <b>{statusLabel(decisionResult[pending])}</b>. The decision is recorded in the review history and audit log.</p>{remark.trim() && <p className="mt-2 rounded bg-slate-50 px-3 py-2">“{remark.trim()}”</p>}</>} />}
  </section>
}
