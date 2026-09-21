import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { Activity } from '../types'
import { Badge, ErrorState, formatDate, formatNumber, Loading, MetricCard, PageHeader } from '../components/ui'

interface SbuDash {
  assigned_alcs: number; activities: number; submitted: number; pending: number; verified: number
  corrections: number; rejected: number; learners: number; leads: number; admissions: number
  recent_activities: { activity: Activity; alc: { id: string; alc_code: string; alc_name: string } }[]
}

export default function SbuDashboard() {
  const { data, isLoading, error } = useQuery({ queryKey: ['sbu-dashboard'], queryFn: () => api.get<SbuDash>('/portal/dashboard') })
  if (isLoading) return <Loading label="Loading dashboard" />
  if (error || !data) return <ErrorState error={error} />
  return <><PageHeader title="SBU Dashboard" description="Verified performance across the ALCs assigned to your SBU." actions={<Link to="/portal/verification" className="btn-primary">Verification queue</Link>} />
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="Assigned ALCs" value={data.assigned_alcs} />
      <MetricCard label="Awaiting verification" value={data.pending} hint="Submitted, resubmitted or under review" />
      <MetricCard label="Verified activities" value={data.verified} />
      <MetricCard label="Correction / rejected" value={`${data.corrections} / ${data.rejected}`} />
      <MetricCard label="Verified learners" value={formatNumber(data.learners)} />
      <MetricCard label="Verified leads" value={formatNumber(data.leads)} />
      <MetricCard label="Verified admissions" value={formatNumber(data.admissions)} />
      <MetricCard label="Total submitted" value={data.activities} />
    </div>
    <div className="mt-6"><div className="mb-3 flex items-center justify-between"><h2 className="font-bold text-navy">Recent activity across your ALCs</h2><Link className="text-sm font-semibold text-teal" to="/portal/activities">View all</Link></div>
      <div className="table-wrap"><table><thead><tr><th>Activity</th><th>ALC</th><th>Date</th><th>Status</th></tr></thead><tbody>{data.recent_activities.length ? data.recent_activities.map(({ activity: a, alc }) => <tr key={a.id}><td><Link to={`/portal/activities/${a.id}`} className="font-semibold text-navy hover:text-teal">{a.activity_number}</Link><p className="text-xs text-slate-500">{a.activity_type}</p></td><td>{alc.alc_code}<p className="text-xs text-slate-500">{alc.alc_name}</p></td><td>{formatDate(a.activity_date)}</td><td><Badge status={a.status} /></td></tr>) : <tr><td colSpan={4} className="p-5 text-sm text-slate-500">No submitted activities yet.</td></tr>}</tbody></table></div>
    </div>
  </>
}
