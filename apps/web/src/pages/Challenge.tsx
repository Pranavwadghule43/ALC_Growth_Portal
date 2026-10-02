import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { challengePeriodSummary, challengeTimePercent, type ChallengePeriod } from '../lib/challenge'
import { Empty, ErrorState, formatDate, Loading, PageHeader } from '../components/ui'

interface ChallengeData extends Partial<ChallengePeriod> {
  targets: Record<string, number>
  achieved: Record<string, number>
  source: string
}

export default function Challenge() {
  const q = useQuery({ queryKey: ['challenge'], queryFn: () => api.get<ChallengeData>('/portal/challenge') })
  if (q.isLoading) return <Loading />
  if (q.error || !q.data) return <ErrorState error={q.error} />
  const data = q.data
  // No challenge is assumed: until an Admin configures a period there is nothing to measure.
  if (data.status === 'NOT_CONFIGURED' || data.configured === false) return <>
    <PageHeader title="Growth Challenge" description="Official progress uses verified activities." />
    <Empty title="No Growth Challenge is running" message="A Growth Challenge period has not been configured yet. Your progress will appear here once it is." />
  </>
  // The period length is whatever is configured (15, 30, 45, 60 ... days); the API sends the day counts.
  const summary = challengePeriodSummary(data)
  const dates = `${formatDate(data.period_start ?? undefined)} – ${formatDate(data.period_end ?? undefined)}`
  return <>
    <PageHeader title="Growth Challenge" description={`${data.name ? `${data.name} · ` : ''}${dates} · ${data.source}`} />
    {summary && <section className="panel mb-5 p-5" aria-label="Challenge period">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-bold text-navy">Challenge period</h2>
        <span className="text-sm text-slate-600">{summary}</span>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-label="Challenge time elapsed" aria-valuemin={0} aria-valuemax={100} aria-valuenow={challengeTimePercent(data)}>
        <div className="h-full bg-navy" style={{ width: `${challengeTimePercent(data)}%` }} />
      </div>
    </section>}
    <div className="grid gap-5 md:grid-cols-2">
      {Object.entries(data.targets).map(([key, target]) => {
        const achieved = data.achieved[key] ?? 0
        const percent = target ? achieved / target * 100 : 0
        return <section key={key} className="panel p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold capitalize text-navy">{key}</h2>
            <span className="text-2xl font-bold">{achieved}<small className="text-sm font-normal text-slate-500"> / {target}</small></span>
          </div>
          <div className="mt-4 h-3 overflow-hidden rounded-full bg-slate-100"><div className="h-full bg-teal" style={{ width: `${Math.min(100, percent)}%` }} /></div>
          <p className="mt-2 text-xs text-slate-500">{Math.round(percent)}% of target</p>
        </section>
      })}
    </div>
  </>
}
