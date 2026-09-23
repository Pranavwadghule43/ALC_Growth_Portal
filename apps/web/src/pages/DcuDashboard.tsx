import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import { api } from '../lib/api'
import type { Activity, SbuStats, UnitRef } from '../types'
import { Badge, ErrorState, formatDate, formatNumber, Loading, MetricCard, PageHeader } from '../components/ui'

interface DcuDash extends Omit<SbuStats, 'alcs'> {
  role: string; unit: (UnitRef & { type: string }) | null; sbus: number; assigned_alcs: number
  sbu_breakdown: (SbuStats & { id: string; code: string; name: string; is_active: boolean })[]
  recent_activities: { activity: Activity; alc: { id: string; alc_code: string; alc_name: string }; sbu_code?: string | null }[]
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="mb-6"><h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h2><div className="grid gap-4 md:grid-cols-3 xl:grid-cols-6">{children}</div></section>
}

export default function DcuDashboard() {
  const navigate = useNavigate()
  const { data, isLoading, error } = useQuery({ queryKey: ['dcu-dashboard'], queryFn: () => api.get<DcuDash>('/portal/dashboard') })
  if (isLoading) return <Loading label="Loading dashboard" />
  if (error || !data) return <ErrorState error={error} />
  const title = data.unit?.name ?? 'DCU Dashboard'
  return <><PageHeader title={title} description={`DCU Dashboard · ${data.unit?.code ?? ''} · verified performance across your SBUs and ALCs`} actions={<Link to="/portal/verification" className="btn-primary">Verification queue{data.pending ? ` (${data.pending})` : ''}</Link>} />
    <Group title="Network">
      <MetricCard label="Total SBUs" value={data.sbus} />
      <MetricCard label="Total ALCs" value={formatNumber(data.assigned_alcs)} />
      <MetricCard label="Active ALCs" value={formatNumber(data.active_alcs)} />
      <MetricCard label="Inactive ALCs" value={formatNumber(data.inactive_alcs)} />
      <MetricCard label="Total partners" value={formatNumber(data.partners)} />
    </Group>
    <Group title="Activity workflow">
      <MetricCard label="Submitted activities" value={formatNumber(data.activities)} hint="All activities in the review workflow" />
      <MetricCard label="Pending verification" value={formatNumber(data.pending)} hint="Submitted, resubmitted or under review" />
      <MetricCard label="Correction required" value={formatNumber(data.corrections)} />
      <MetricCard label="Resubmitted" value={formatNumber(data.resubmitted)} />
      <MetricCard label="Verified" value={formatNumber(data.verified)} />
      <MetricCard label="Rejected" value={formatNumber(data.rejected)} />
    </Group>
    <Group title="Verified outcomes">
      <MetricCard label="Verified learner reach" value={formatNumber(data.learners)} />
      <MetricCard label="Verified leads" value={formatNumber(data.leads)} />
      <MetricCard label="Verified admissions" value={formatNumber(data.admissions)} />
    </Group>

    <section className="mb-8"><div className="mb-3 flex items-center justify-between"><h2 className="font-bold text-navy">SBU breakdown</h2><Link className="text-sm font-semibold text-teal" to="/portal/sbus">SBU directory</Link></div>
      <div className="table-wrap"><table className="table-dense"><thead><tr><th>SBU</th><th className="text-right">ALCs</th><th className="text-right">Active</th><th className="text-right">Pending verification</th><th className="text-right">Verified</th><th className="text-right">Partners</th><th className="text-right">Verified learners</th><th /></tr></thead>
        <tbody>{data.sbu_breakdown.length ? data.sbu_breakdown.map(s => <tr key={s.id} className="cursor-pointer hover:bg-teal/5" onClick={() => navigate(`/portal/sbus/${s.id}`)}>
          <td><Link to={`/portal/sbus/${s.id}`} className="font-semibold text-navy hover:text-teal" onClick={e => e.stopPropagation()}>{s.code}</Link><p className="text-xs text-slate-500">{s.name}</p></td>
          <td className="text-right">{formatNumber(s.alcs)}</td><td className="text-right">{formatNumber(s.active_alcs)}</td><td className="text-right">{formatNumber(s.pending)}</td><td className="text-right">{formatNumber(s.verified)}</td><td className="text-right">{formatNumber(s.partners)}</td><td className="text-right">{formatNumber(s.learners)}</td><td className="text-right"><ChevronRight className="ml-auto h-4 w-4 text-slate-400" /></td>
        </tr>) : <tr><td colSpan={8} className="p-5 text-sm text-slate-500">No SBUs are assigned to this DCU yet.</td></tr>}</tbody>
        {data.sbu_breakdown.length > 0 && <tfoot><tr className="border-t bg-slate-50 font-semibold"><td className="px-4 py-3">Total</td><td className="px-4 py-3 text-right">{formatNumber(data.assigned_alcs)}</td><td className="px-4 py-3 text-right">{formatNumber(data.active_alcs)}</td><td className="px-4 py-3 text-right">{formatNumber(data.pending)}</td><td className="px-4 py-3 text-right">{formatNumber(data.verified)}</td><td className="px-4 py-3 text-right">{formatNumber(data.partners)}</td><td className="px-4 py-3 text-right">{formatNumber(data.learners)}</td><td /></tr></tfoot>}
      </table></div>
    </section>

    <section><div className="mb-3 flex items-center justify-between"><h2 className="font-bold text-navy">Recent activity</h2><Link className="text-sm font-semibold text-teal" to="/portal/activities">Activity monitoring</Link></div>
      <div className="table-wrap"><table><thead><tr><th>Activity</th><th>ALC</th><th>SBU</th><th>Activity date</th><th>Status</th></tr></thead><tbody>{data.recent_activities.length ? data.recent_activities.map(({ activity: a, alc, sbu_code }) => <tr key={a.id}><td><Link to={`/portal/activities/${a.id}`} className="font-semibold text-navy hover:text-teal">{a.activity_number}</Link><p className="text-xs text-slate-500">{a.activity_type}</p></td><td><Link to={`/portal/alcs/${alc.id}`} className="hover:text-teal">{alc.alc_code}</Link><p className="text-xs text-slate-500">{alc.alc_name}</p></td><td>{sbu_code ?? '—'}</td><td>{formatDate(a.activity_date)}</td><td><Badge status={a.status} /></td></tr>) : <tr><td colSpan={5} className="p-5 text-sm text-slate-500">No submitted activities yet.</td></tr>}</tbody></table></div>
    </section>
  </>
}
