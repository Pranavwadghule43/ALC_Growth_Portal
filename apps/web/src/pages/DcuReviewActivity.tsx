import { useState } from 'react'
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

// DCU activity review: full context, evidence, complete history and a sticky decision panel.
// After a decision the page stays open and refreshes, so the new status and history entry
// are visible immediately; dashboards and reports are refetched too.
export default function DcuReviewActivity() {
  const { id } = useParams(); const { pathname } = useLocation(); const client = useQueryClient()
  const [toast, setToast] = useState('')
  const q = useQuery({ queryKey: ['dcu-review', id], queryFn: () => api.get<ReviewPayload>(`/portal/verification/${id}`) })
  const fromQueue = pathname.startsWith('/portal/verification')
  async function done(message: string) { setToast(message); await client.invalidateQueries() }
  if (q.isLoading) return <Loading />
  if (q.error || !q.data) return <ErrorState error={q.error} />
  const { activity: a, alc, sbu, dcu } = q.data
  return <>
    <Link to={fromQueue ? '/portal/verification' : '/portal/activities'} className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-slate-500 hover:text-teal"><ChevronLeft className="h-4 w-4" />{fromQueue ? 'Verification queue' : 'Activity monitoring'}</Link>
    <PageHeader title={a.activity_number} description={`${alc.alc_code} · ${alc.alc_name} · ${sbu?.code ?? 'No SBU'} · ${dcu?.name ?? 'No DCU'}`} actions={<Badge status={a.status} />} />
    <div className="grid gap-6 xl:grid-cols-[1.3fr_.7fr]">
      <div className="space-y-6">
        <ActivityDetails activity={a} />
        <EvidenceGallery evidence={a.evidence} accessPath={e => `/portal/evidence/${e}/access`} />
        <ReviewHistory reviews={a.reviews} revisions={a.revisions ?? []} />
      </div>
      <aside className="space-y-5 xl:sticky xl:top-6 xl:self-start">
        <section className="panel p-5"><h2 className="font-bold text-navy">Centre</h2><dl className="mt-3 space-y-2 text-sm">{[['ALC code', alc.alc_code], ['ALC name', alc.alc_name], ['SBU', sbu ? `${sbu.code} · ${sbu.name}` : '—'], ['DCU', dcu?.name ?? '—']].map(([k, v]) => <div key={k} className="flex justify-between gap-3"><dt className="text-slate-500">{k}</dt><dd className="text-right font-medium">{v}</dd></div>)}</dl><Link to={`/portal/alcs/${alc.id}`} className="mt-4 inline-block text-sm font-semibold text-teal">Open ALC</Link></section>
        <DecisionPanel basePath={`/portal/activities/${a.id}`} status={a.status} canReview={q.data.can_review} canChangeDecision={q.data.can_change_decision} onDone={done} />
      </aside>
    </div>
    {toast && <Toast message={toast} onClose={() => setToast('')} />}
  </>
}
