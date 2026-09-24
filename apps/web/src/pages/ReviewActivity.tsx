import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { ChevronLeft } from 'lucide-react'
import { api } from '../lib/api'
import type { Activity, Alc, Sbu, UnitRef } from '../types'
import { Badge, ErrorState, Loading, PageHeader, REVIEWABLE_STATUSES, Toast } from '../components/ui'
import { ActivityDetails, DecisionPanel, EvidenceGallery, ReviewHistory } from '../components/review'

// Admin activity review. Admin may review pending activities and, like a DCU, change a
// final (verified / rejected) decision with a mandatory reason; history is append-only.
export default function ReviewActivity() {
  const { id } = useParams(); const client = useQueryClient(); const [toast, setToast] = useState('')
  const query = useQuery({ queryKey: ['admin-activity', id], queryFn: () => api.get<{ activity: Activity; alc: Alc; sbu?: Sbu | null; dcu?: UnitRef | null; can_change_decision: boolean }>(`/admin/activities/${id}`) })
  async function done(message: string) { setToast(message); await client.invalidateQueries() }
  if (query.isLoading) return <Loading />
  if (query.error || !query.data) return <ErrorState error={query.error} />
  const { activity: a, alc, sbu, dcu } = query.data
  return <>
    <Link to="/admin/verification" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-slate-500 hover:text-teal"><ChevronLeft className="h-4 w-4" />Verification queue</Link>
    <PageHeader title={a.activity_number} description={`${alc.alc_code} · ${alc.alc_name}`} actions={<Badge status={a.status} />} />
    <div className="grid gap-6 xl:grid-cols-[1.3fr_.7fr]">
      <div className="space-y-6">
        <ActivityDetails activity={a} />
        <EvidenceGallery evidence={a.evidence} removed={a.removed_evidence} accessPath={e => `/admin/evidence/${e}/access`} />
        <ReviewHistory reviews={a.reviews} revisions={a.revisions ?? []} />
      </div>
      <aside className="space-y-5 xl:sticky xl:top-6 xl:self-start">
        <section className="panel p-5"><h2 className="font-bold text-navy">ALC</h2><p className="mt-3 text-sm font-semibold">{alc.alc_name}</p><p className="text-sm text-slate-500">{alc.alc_code}</p><p className="mt-2 text-xs text-slate-500">SBU: <span className="font-medium text-navy">{sbu ? `${sbu.code} · ${sbu.name}` : 'Unassigned'}</span></p><p className="mt-1 text-xs text-slate-500">DCU: <span className="font-medium text-navy">{dcu?.name ?? 'Unassigned'}</span></p></section>
        <DecisionPanel basePath={`/admin/activities/${a.id}`} status={a.status} canReview={REVIEWABLE_STATUSES.includes(a.status)} canChangeDecision={query.data.can_change_decision} onDone={done} />
      </aside>
    </div>
    {toast && <Toast message={toast} onClose={() => setToast('')} />}
  </>
}
