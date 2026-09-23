import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { ChevronLeft } from 'lucide-react'
import { api } from '../lib/api'
import type { SbuStats, UnitRef } from '../types'
import { Badge, ErrorState, formatNumber, Loading, MetricCard, PageHeader } from '../components/ui'
import { AlcDirectory } from '../components/directory'

interface SbuDetail { sbu: UnitRef & { is_active: boolean; dcu: UnitRef | null }; stats: SbuStats }

export default function DcuSbuDetail() {
  const { id } = useParams()
  const q = useQuery({ queryKey: ['dcu-sbu', id], queryFn: () => api.get<SbuDetail>(`/portal/sbus/${id}`) })
  if (q.isLoading) return <Loading />
  if (q.error || !q.data) return <ErrorState error={q.error} />
  const { sbu, stats } = q.data
  return <>
    <Link to="/portal/sbus" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-slate-500 hover:text-teal"><ChevronLeft className="h-4 w-4" />All SBUs</Link>
    <PageHeader title={`${sbu.code} · ${sbu.name}`} description={`${sbu.dcu?.name ?? 'No DCU'} → ${sbu.code} → ${formatNumber(stats.alcs)} ALCs`} actions={<Badge status={sbu.is_active ? 'ACTIVE' : 'INACTIVE'} />} />
    <section className="panel mb-6 grid gap-4 p-5 md:grid-cols-4">{[['SBU code', sbu.code], ['SBU name', sbu.name], ['DCU', sbu.dcu?.name ?? '—'], ['Status', sbu.is_active ? 'Active' : 'Inactive']].map(([k, v]) => <div key={k}><p className="text-xs font-semibold uppercase text-slate-500">{k}</p><p className="mt-1 font-medium">{v}</p></div>)}</section>
    <div className="mb-3 grid gap-4 md:grid-cols-4 xl:grid-cols-8">
      <MetricCard label="Total ALCs" value={formatNumber(stats.alcs)} />
      <MetricCard label="Active ALCs" value={formatNumber(stats.active_alcs)} />
      <MetricCard label="Partners" value={formatNumber(stats.partners)} />
      <MetricCard label="Submitted" value={formatNumber(stats.activities)} hint="In the review workflow" />
      <MetricCard label="Pending" value={formatNumber(stats.pending)} />
      <MetricCard label="Correction required" value={formatNumber(stats.corrections)} />
      <MetricCard label="Verified" value={formatNumber(stats.verified)} />
      <MetricCard label="Rejected" value={formatNumber(stats.rejected)} />
    </div>
    <div className="mb-3 mt-7 flex items-center justify-between"><h2 className="font-bold text-navy">ALCs in {sbu.code}</h2><Link to={`/portal/verification?sbu_id=${sbu.id}`} className="text-sm font-semibold text-teal">Pending verification for {sbu.code}</Link></div>
    <AlcDirectory fixedSbuId={sbu.id} />
  </>
}
