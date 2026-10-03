import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { Empty, ErrorState, formatNumber, Loading } from './ui'

interface UnitBrief { code: string; name: string }
export interface RegionTop10Item {
  rank: number; alc_id: string; alc_code: string; alc_name: string; sbu: UnitBrief | null; dcu: UnitBrief | null
  verified_leads: number; verified_admissions: number; activities_done: number; partners: number; score: number
}
export interface RegionTop10Data { period: string; generated_at: string; items: RegionTop10Item[] }

// Region Top 10 (``GET /leaderboard/region-top10``): the same regional ranking for ADMIN, DCU,
// SBU and ALC. Owns its query, so a failure here never breaks the dashboard it sits in. Rows
// deliberately do not link to ALC detail pages (most ALCs are outside the viewer's scope).
export function RegionTop10() {
  const q = useQuery({ queryKey: ['region-top10'], queryFn: () => api.get<RegionTop10Data>('/leaderboard/region-top10') })
  return <section className="mt-8" aria-labelledby="region-top10-title">
    <div className="mb-3"><h2 id="region-top10-title" className="font-bold text-navy">Region Top 10</h2><p className="text-xs text-slate-500">Lifetime verified performance of active ALCs across the region. Score weights verified leads, verified admissions, verified activities and active partners equally (25% each), each as a regional percentile.</p></div>
    {q.isLoading ? <Loading label="Loading Region Top 10" /> : q.error || !q.data ? <ErrorState error={q.error} /> : !q.data.items.length ? <Empty title="No ranked ALCs yet" message="ALCs appear here once they have verified activities or active partners." /> : <div className="table-wrap"><table className="table-dense">
      <thead><tr><th className="text-right">Rank</th><th>ALC Code</th><th>ALC Name</th><th>SBU</th><th>DCU</th><th className="text-right">Verified Leads</th><th className="text-right">Verified Admissions</th><th className="text-right">Verified Activities</th><th className="text-right">Active Partners</th><th className="text-right">Score</th></tr></thead>
      <tbody>{q.data.items.map(x => <tr key={x.alc_id}>
        <td className="text-right font-bold text-navy">{x.rank}</td><td className="font-semibold text-navy">{x.alc_code}</td><td>{x.alc_name}</td>
        <td>{x.sbu?.code ?? '—'}</td><td className="text-sm text-slate-600">{x.dcu?.name ?? '—'}</td>
        <td className="text-right">{formatNumber(x.verified_leads)}</td><td className="text-right">{formatNumber(x.verified_admissions)}</td><td className="text-right">{formatNumber(x.activities_done)}</td><td className="text-right">{formatNumber(x.partners)}</td>
        <td className="text-right font-semibold text-navy">{x.score.toFixed(2)}</td>
      </tr>)}</tbody>
    </table></div>}
  </section>
}

// Static, read-only explanation of the approved Region Top 10 scoring rules (equal 25% weights
// over regional percentiles). Shown to ADMIN / DCU / SBU below the leaderboard; never to ALC
// users. It makes no API call and changes nothing about how scores are calculated.
const SCORE_METRICS = ['Verified Leads', 'Verified Admissions', 'Verified Activities', 'Active Partners'] as const
const EXAMPLE_SCORES = [['Verified Leads Score', 80], ['Verified Admissions Score', 60], ['Verified Activities Score', 100], ['Active Partners Score', 40]] as const
const SCORE_NOTES = [
  'Leaderboard values are lifetime totals.',
  'Only VERIFIED activities contribute to Verified Leads, Verified Admissions and Verified Activities.',
  'Only ACTIVE partners are counted.',
  'Only ACTIVE ALCs under an ACTIVE SBU, ACTIVE DCU and ACTIVE RCU participate.',
  'The complete ALC → SBU → DCU → RCU hierarchy is required.',
  'ALCs with zero across all four metrics are excluded.',
  'Percentile comparison uses all eligible regional ALCs before the Top 10 is selected.',
  'Ranking uses the full, unrounded score.',
  'The displayed score is rounded to 2 decimal places.',
  'If exact final scores are equal, ALC Code is used only to give a consistent order.',
]

export function RegionTop10ScoreReference() {
  return <section className="mt-6 panel p-5" aria-labelledby="region-top10-reference-title">
    <div className="mb-4"><h2 id="region-top10-reference-title" className="font-bold text-navy">How the Region Top 10 Score Is Calculated</h2><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">For reference only</p></div>
    <p className="text-sm text-slate-700">Each of the four metrics has equal importance. There are four approved performance metrics and each is given equal importance. Therefore each metric contributes 25% of the final score.</p>
    <ul aria-label="Metric weights" className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{SCORE_METRICS.map(m => <li key={m} className="rounded-lg border border-line bg-tint p-4 text-center"><p className="text-sm font-semibold text-navy">{m}</p><p className="mt-1 text-2xl font-bold text-brand">25%</p></li>)}</ul>
    <div className="mt-5 grid gap-5 lg:grid-cols-2">
      <div className="min-w-0">
        <h3 className="text-sm font-bold text-navy">How it works</h3>
        <div className="mt-2 space-y-2 text-sm text-slate-700">
          <p>Each eligible ALC is compared with all other eligible ALCs in the region for each of the four performance metrics.</p>
          <p>Each metric is converted into a regional score from 0 to 100 based on the ALC’s position compared with the other eligible ALCs.</p>
          <p>A higher percentile score means the ALC performs better than more ALCs in the region for that metric.</p>
          <p>The four metric scores are then given equal weight to calculate the final Region Top 10 score.</p>
        </div>
        <h3 className="mt-4 text-sm font-bold text-navy">Final Score</h3>
        <div aria-label="Final score formula" className="mt-2 rounded-lg border border-line bg-tint-soft p-4 text-sm leading-7 text-navy"><p className="font-semibold">Final Score =</p><p>(Verified Leads Score × 25%)</p><p>+ (Verified Admissions Score × 25%)</p><p>+ (Verified Activities Score × 25%)</p><p>+ (Active Partners Score × 25%)</p></div>
      </div>
      <div className="min-w-0">
        <h3 className="text-sm font-bold text-navy">Example</h3>
        <p className="text-xs text-slate-500">Illustration only, not real leaderboard data.</p>
        <div className="mt-2 table-wrap shadow-none"><table className="table-dense"><thead><tr><th>Metric score</th><th className="text-right">Score</th><th className="text-right">Weight</th><th className="text-right">Contribution</th></tr></thead>
          <tbody>{EXAMPLE_SCORES.map(([label, value]) => <tr key={label}><td>{label}</td><td className="text-right">{value}</td><td className="text-right">× 25%</td><td className="text-right">{(value * 0.25).toFixed(2)}</td></tr>)}</tbody>
          <tfoot><tr className="border-t bg-tint font-semibold text-navy"><td className="px-3 py-3" colSpan={3}>Final Score = 20 + 15 + 25 + 10</td><td className="px-3 py-3 text-right">70.00</td></tr></tfoot>
        </table></div>
      </div>
    </div>
    <h3 className="mt-5 text-sm font-bold text-navy">Notes</h3>
    <ul aria-label="Score notes" className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">{SCORE_NOTES.map(n => <li key={n}>{n}</li>)}</ul>
  </section>
}
