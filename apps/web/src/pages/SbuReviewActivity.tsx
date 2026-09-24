import { useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useParams } from 'react-router-dom'
import { ChevronLeft } from 'lucide-react'
import { api } from '../lib/api'
import type { Activity, UnitRef } from '../types'
import { Badge, ErrorState, Loading, PageHeader, Toast } from '../components/ui'
import { ActivityDetails, DecisionPanel, EvidenceGallery, ReviewHistory } from '../components/review'

interface ReviewPayload {
  activity: Activity; alc: { id: string; alc_code: string; alc_name: string; status: string }
  sbu: UnitRef | null; dcu: UnitRef | null; can_review: boolean; can_change_decision: boolean
}

// SBU activity review, built on the same shared review components as the DCU and Admin pages:
// activity details, evidence gallery (image preview / PDF), combined history and a sticky
// decision panel. After a decision the page stays open, shows a toast and refetches everything,
// so the new status, history entry, queue and dashboard counts are current immediately.
// SBU never gets "Change Decision" on VERIFIED/REJECTED activities (DCU/Admin only), so it is
// hard-wired off here as well as refused by the backend.
export default function SbuReviewActivity() {
  const { id } = useParams(); const { pathname } = useLocation(); const client = useQueryClient()
  const [toast, setToast] = useState(''); const closeToast = useCallback(() => setToast(''), [])
  const q = useQuery({ queryKey: ['sbu-review', id], queryFn: () => api.get<ReviewPayload>(`/portal/verification/${id}`) })
  const fromQueue = pathname.startsWith('/portal/verification')
  async function done(message: string) { setToast(message); await client.invalidateQueries() }
  if (q.isLoading) return <Loading />
  if (q.error || !q.data) return <ErrorState error={q.error} />
  const { activity: a, alc, sbu } = q.data
  return <>
    <Link to={fromQueue ? '/portal/verification' : '/portal/activities'} className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-slate-500 hover:text-teal"><ChevronLeft className="h-4 w-4" />{fromQueue ? 'Verification queue' : 'Activities'}</Link>
    <PageHeader title={a.activity_number} description={`${alc.alc_code} · ${alc.alc_name}`} actions={<Badge status={a.status} />} />
    <div className="grid gap-6 xl:grid-cols-[1.3fr_.7fr]">
      <div className="min-w-0 space-y-6">
        <ActivityDetails activity={a} />
        <EvidenceGallery evidence={a.evidence} removed={a.removed_evidence} accessPath={e => `/portal/evidence/${e}/access`} />
        <ReviewHistory reviews={a.reviews} revisions={a.revisions ?? []} />
      </div>
      <aside className="space-y-5 xl:sticky xl:top-6 xl:self-start">
        <section className="panel p-5"><h2 className="font-bold text-navy">Centre</h2><dl className="mt-3 space-y-2 text-sm">{[['ALC code', alc.alc_code], ['ALC name', alc.alc_name], ['SBU', sbu ? `${sbu.code} · ${sbu.name}` : '—']].map(([k, v]) => <div key={k} className="flex justify-between gap-3"><dt className="text-slate-500">{k}</dt><dd className="break-words text-right font-medium">{v}</dd></div>)}</dl><Link to={`/portal/alcs/${alc.id}`} className="mt-4 inline-block text-sm font-semibold text-teal">Open ALC</Link></section>
        <DecisionPanel basePath={`/portal/activities/${a.id}`} status={a.status} canReview={q.data.can_review} canChangeDecision={false} onDone={done} />
      </aside>
    </div>
    {toast && <Toast message={toast} onClose={closeToast} />}
  </>
}
