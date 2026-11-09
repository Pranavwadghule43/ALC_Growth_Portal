import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { Badge, ErrorState, formatDate, Loading, MetricCard } from './ui'

interface Run {
  id: string
  status: string
  scheduled_end_date: string | null
}

interface Definition {
  id: string
  activity_code: string
  name: string
  runs: Run[]
}

interface DefinitionPage {
  items: Definition[]
  total: number
  summary: {
    completed_count: number
    total_applicable: number
    completion_percentage: number
  }
}

/** A server-scoped dashboard view; visibility is never filtered in the browser. */
export default function YearlyActivityTracking() {
  const year = new Date().getFullYear()
  const query = useQuery({
    queryKey: ['yearly-definitions', `year=${year}&page=1&page_size=25`],
    queryFn: () => api.get<DefinitionPage>(`/yearly-activities/definitions?year=${year}&page=1&page_size=25`),
    staleTime: 60_000,
    gcTime: 180_000,
  })

  return <section className="mt-6" aria-label="Yearly activity tracking">
    <div className="mb-3 flex items-center justify-between gap-3">
      <div><h2 className="font-bold text-navy">Yearly Activities</h2><p className="text-sm text-slate-500">Activities and progress in your authorized DCU hierarchy.</p></div>
      <Link className="text-sm font-semibold text-teal" to="/portal/yearly-activities">View all</Link>
    </div>
    {query.isLoading ? <Loading label="Loading yearly activities" /> : query.error || !query.data ? <ErrorState error={query.error} /> : <>
      <div className="mb-3 grid gap-3 sm:grid-cols-3">
        <MetricCard label="Activities" value={query.data.total} />
        <MetricCard label="Verified submissions" value={query.data.summary.completed_count} />
        <MetricCard label="Completion" value={`${query.data.summary.completion_percentage.toFixed(1)}%`} hint={`${query.data.summary.total_applicable} applicable participations`} />
      </div>
      <div className="panel divide-y">
        {query.data.items.slice(0, 5).map(activity => {
          const run = activity.runs[0]
          return <div className="flex flex-wrap items-center justify-between gap-3 p-4" key={activity.id}>
            <div><p className="font-semibold text-navy">{activity.name}</p><p className="text-xs text-slate-500">{activity.activity_code}{run?.scheduled_end_date ? ` · due ${formatDate(run.scheduled_end_date)}` : ''}</p></div>
            {run ? <Badge status={run.status} /> : <span className="text-xs text-slate-500">Oversight only</span>}
          </div>
        })}
        {!query.data.items.length && <p className="p-5 text-sm text-slate-500">No yearly activities are assigned to your DCU hierarchy.</p>}
      </div>
    </>}
  </section>
}
