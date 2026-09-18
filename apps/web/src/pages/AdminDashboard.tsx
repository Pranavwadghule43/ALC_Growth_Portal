import { useQuery } from '@tanstack/react-query'
import { Cell, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../lib/api'
import { ErrorState, formatNumber, Loading, MetricCard, PageHeader } from '../components/ui'

interface AdminData { total_alcs: number; active_alcs: number; activities_submitted: number; pending_verification: number; verified_activities: number; correction_required: number; rejected_activities: number; verified_learners: number; verified_leads: number; verified_admissions: number; active_partnerships: number; status_distribution: {name:string;value:number}[]; submission_trend:{date:string;count:number}[] }
const colors = ['#087b78','#2563eb','#7c3aed','#d97706','#059669','#dc2626','#64748b']
export default function AdminDashboard() {
  const { data, isLoading, error } = useQuery({ queryKey: ['admin-dashboard'], queryFn: () => api.get<AdminData>('/admin/dashboard') })
  if (isLoading) return <Loading/>; if (error || !data) return <ErrorState error={error}/>
  const cards = [['Total ALCs',data.total_alcs],['Active ALCs',data.active_alcs],['Pending verification',data.pending_verification],['Verified activities',data.verified_activities],['Correction required',data.correction_required],['Verified learners',formatNumber(data.verified_learners)],['Verified leads',formatNumber(data.verified_leads)],['Verified admissions',formatNumber(data.verified_admissions)],['Active partnerships',data.active_partnerships]] as const
  return <><PageHeader title="Administration Overview" description="System-wide performance. Outcome metrics include verified activities only."/><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">{cards.map(([label,value]) => <MetricCard key={label} label={label} value={value} hint={label.startsWith('Verified') ? 'Verified data only' : undefined}/>)}</div>
    <div className="mt-6 grid gap-6 xl:grid-cols-[1.4fr_1fr]"><section className="panel p-5"><h2 className="font-bold text-navy">Activity submissions over time</h2><p className="mb-4 text-xs text-slate-500">Last 30 activity dates</p><div className="h-72"><ResponsiveContainer width="100%" height="100%"><LineChart data={data.submission_trend}><XAxis dataKey="date" tick={{fontSize:11}}/><YAxis allowDecimals={false}/><Tooltip/><Line dataKey="count" stroke="#087b78" strokeWidth={2} dot={false}/></LineChart></ResponsiveContainer></div></section><section className="panel p-5"><h2 className="font-bold text-navy">Verification status</h2><p className="mb-4 text-xs text-slate-500">All recorded activities</p><div className="h-72"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={data.status_distribution} dataKey="value" nameKey="name" innerRadius={50} outerRadius={90}>{data.status_distribution.map((_, i) => <Cell key={i} fill={colors[i % colors.length]}/>)}</Pie><Tooltip/></PieChart></ResponsiveContainer></div></section></div>
  </>
}

