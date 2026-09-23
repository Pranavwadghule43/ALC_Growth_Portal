import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { FileText, Image as ImageIcon, X } from 'lucide-react'
import { api } from '../lib/api'
import type { Activity, Alc, Evidence } from '../types'
import { Badge, ErrorState, formatDate, formatNumber, Loading, PageHeader } from '../components/ui'
import { ReviewHistory } from '../components/review'

function EvidenceCard({ evidence, onView }: { evidence: Evidence; onView: (url: string) => void }) {
  const isPdf = evidence.mime_type === 'application/pdf'
  const { data } = useQuery({ queryKey: ['evidence-url', evidence.id], queryFn: () => api.get<{ url: string }>(`/portal/evidence/${evidence.id}/access`) })
  const url = data?.url
  function open() { if (!url) return; if (isPdf) window.open(url, '_blank', 'noopener,noreferrer'); else onView(url) }
  return <button onClick={open} disabled={!url} className="group flex flex-col overflow-hidden rounded-md border text-left hover:border-teal">
    <div className="flex h-28 items-center justify-center bg-slate-50">{isPdf ? <FileText className="h-8 w-8 text-red-700" /> : url ? <img src={url} alt={evidence.original_filename} className="h-full w-full object-cover" /> : <ImageIcon className="h-8 w-8 text-teal" />}</div>
    <span className="min-w-0 p-3"><b className="block truncate text-sm">{evidence.original_filename}</b><small className="text-slate-500">{isPdf ? 'PDF' : 'Image'} · {(evidence.file_size / 1024 / 1024).toFixed(1)} MB</small></span>
  </button>
}

export default function SbuReviewActivity() {
  const { id } = useParams(); const navigate = useNavigate(); const client = useQueryClient()
  const [remark, setRemark] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false); const [lightbox, setLightbox] = useState<string | null>(null)
  const query = useQuery({ queryKey: ['sbu-review', id], queryFn: () => api.get<{ activity: Activity; alc: Alc }>(`/portal/verification/${id}`) })
  async function decide(action: 'verify' | 'request-correction' | 'reject') { if (action !== 'verify' && !remark.trim()) { setError('A reason is required for correction or rejection.'); return } if (!confirm(`Confirm ${action.replace('-', ' ')}? This decision will be recorded in the audit history.`)) return; setBusy(true); setError(''); try { await api.post(`/portal/activities/${id}/${action}`, { remark: remark || null }); await client.invalidateQueries({ queryKey: ['sbu-activities'] }); navigate('/portal/verification') } catch (e) { setError(e instanceof Error ? e.message : 'Review failed') } finally { setBusy(false) } }
  if (query.isLoading) return <Loading />; if (query.error || !query.data) return <ErrorState error={query.error} />
  const { activity: a, alc } = query.data; const reviewable = ['SUBMITTED', 'RESUBMITTED', 'UNDER_REVIEW'].includes(a.status); const revisions = a.revisions ?? []
  return <><PageHeader title={a.activity_number} description={`${alc.alc_code} · ${alc.alc_name}`} actions={<Badge status={a.status} />} /><div className="grid gap-6 xl:grid-cols-[1.25fr_.75fr]"><div className="space-y-6"><section className="panel p-5"><h2 className="mb-4 font-bold text-navy">Activity details</h2><dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{[['Type', a.activity_type], ['Partner', a.partner?.partner_name ?? '—'], ['Ecosystem', a.ecosystem], ['Collaboration', a.collaboration_type ?? '—'], ['Activity date', formatDate(a.activity_date)], ['Location', a.location], ['Submitted', formatDate(a.submitted_at)]].map(([k, v]) => <div key={k}><dt className="text-xs font-semibold uppercase text-slate-500">{k}</dt><dd className="mt-1 text-sm font-medium">{v}</dd></div>)}</dl><div className="mt-5 grid gap-4 sm:grid-cols-3"><div className="rounded-md bg-slate-50 p-4"><p className="text-xs text-slate-500">Learners reached</p><b className="text-xl">{formatNumber(a.learners_reached)}</b></div><div className="rounded-md bg-slate-50 p-4"><p className="text-xs text-slate-500">Leads generated</p><b className="text-xl">{formatNumber(a.leads_generated)}</b></div><div className="rounded-md bg-slate-50 p-4"><p className="text-xs text-slate-500">Admissions</p><b className="text-xl">{formatNumber(a.admissions_generated)}</b></div></div><div className="mt-5"><h3 className="text-sm font-semibold">Description</h3><p className="mt-1 whitespace-pre-wrap text-sm text-slate-600">{a.description}</p><h3 className="mt-4 text-sm font-semibold">Outcome</h3><p className="mt-1 whitespace-pre-wrap text-sm text-slate-600">{a.outcome}</p></div></section>
        <section className="panel p-5"><h2 className="font-bold text-navy">Evidence ({a.evidence.length})</h2>{a.evidence.length ? <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{a.evidence.map(e => <EvidenceCard key={e.id} evidence={e} onView={setLightbox} />)}</div> : <p className="mt-3 text-sm text-slate-500">No evidence was attached to this activity.</p>}</section>
        <ReviewHistory reviews={a.reviews} revisions={revisions} /></div>
      <aside className="space-y-5 lg:sticky lg:top-6 lg:self-start"><section className="panel p-5"><h2 className="font-bold text-navy">ALC</h2><p className="mt-3 text-sm font-semibold">{alc.alc_name}</p><p className="text-sm text-slate-500">{alc.alc_code}</p></section>{reviewable && <section className="panel p-5"><h2 className="font-bold text-navy">Review decision</h2><label className="mt-4 block">Remark / reason</label><textarea className="mt-1.5 min-h-28" value={remark} onChange={e => setRemark(e.target.value)} placeholder="Required for correction and rejection" />{error && <p className="mt-3 text-sm text-red-700">{error}</p>}<div className="mt-4 space-y-2"><button disabled={busy} className="btn-primary w-full" onClick={() => decide('verify')}>Verify Activity</button><button disabled={busy} className="btn-secondary w-full border-amber-400 text-amber-900" onClick={() => decide('request-correction')}>Request Correction</button><button disabled={busy} className="btn-danger w-full" onClick={() => decide('reject')}>Reject Activity</button></div></section>}</aside></div>
    {lightbox && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={() => setLightbox(null)}><button className="absolute right-4 top-4 text-white" onClick={() => setLightbox(null)} aria-label="Close"><X className="h-7 w-7" /></button><img src={lightbox} alt="Evidence preview" className="max-h-full max-w-full rounded" onClick={e => e.stopPropagation()} /></div>}
  </>
}