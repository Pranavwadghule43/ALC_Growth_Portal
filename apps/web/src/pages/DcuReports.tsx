import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { api } from '../lib/api'
import { toQuery } from '../lib/constants'
import type { AlcOption, SbuRow, SbuStats } from '../types'
import { ErrorState, Filter, formatNumber, Loading, PageHeader } from '../components/ui'

type SummaryRow = SbuStats & { sbu_id: string; sbu_code: string; sbu_name: string }

// DCU reports. Every export is scoped server-side to the DCU; filters only narrow it.
export default function DcuReports() {
  const [filters, setFilters] = useState({ sbu_id: '', alc_id: '', date_from: '', date_to: '' })
  const sbus = useQuery({ queryKey: ['dcu-sbus'], queryFn: () => api.get<{ items: SbuRow[] }>('/portal/sbus') })
  const alcs = useQuery({ queryKey: ['alc-options', filters.sbu_id], queryFn: () => api.get<{ items: AlcOption[] }>(`/portal/lookups/alcs${filters.sbu_id ? `?sbu_id=${filters.sbu_id}` : ''}`) })
  const query = toQuery(filters)
  const summary = useQuery({ queryKey: ['dcu-report-summary', query], queryFn: () => api.get<{ items: SummaryRow[]; totals: Omit<SbuStats, 'inactive_alcs'> }>(`/portal/reports/sbu-summary?${query}`) })
  const dated = toQuery({ date_from: filters.date_from, date_to: filters.date_to })
  const exports: [string, string, string][] = [
    ['SBU-wise performance', 'ALCs, partners, status counts and verified outcomes per SBU.', `/portal/reports/sbu-performance.csv?${query}`],
    ['ALC-wise activities', 'Every submitted activity with its ALC, SBU, status and metrics.', `/portal/reports/activities.csv?${query}`],
    ['Verification status by ALC', 'Per-ALC counts by status with verified learner reach, leads and admissions.', `/portal/reports/verification-status.csv?${query}`],
    ['Partner report', 'Partners with their ALC, SBU, activity count and last activity (not date-filtered).', `/portal/reports/partners.csv?${toQuery({ sbu_id: filters.sbu_id, alc_id: filters.alc_id })}`],
  ]
  return <><PageHeader title="Reports" description="SBU and ALC performance for your DCU. Exports contain only your DCU's data." />
    <section className="panel mb-6 grid gap-4 p-5 md:grid-cols-4">
      <Filter label="SBU"><select value={filters.sbu_id} onChange={e => setFilters(f => ({ ...f, sbu_id: e.target.value, alc_id: '' }))}><option value="">All SBUs</option>{sbus.data?.items.map(s => <option key={s.id} value={s.id}>{s.code}</option>)}</select></Filter>
      <Filter label="ALC"><select value={filters.alc_id} onChange={e => setFilters(f => ({ ...f, alc_id: e.target.value }))}><option value="">All ALCs</option>{alcs.data?.items.map(a => <option key={a.id} value={a.id}>{a.alc_code} · {a.alc_name}</option>)}</select></Filter>
      <Filter label="Activity date from"><input type="date" value={filters.date_from} onChange={e => setFilters(f => ({ ...f, date_from: e.target.value }))} /></Filter>
      <Filter label="Activity date to"><input type="date" value={filters.date_to} onChange={e => setFilters(f => ({ ...f, date_to: e.target.value }))} /></Filter>
    </section>
    <section className="mb-6"><div className="mb-3 flex items-baseline justify-between gap-4"><h2 className="font-bold text-navy">SBU-wise performance{dated ? ' (activity date filtered)' : ''}</h2><p className="text-xs text-slate-500">Learners, leads and admissions count verified activities only.</p></div>
      {summary.isLoading ? <Loading /> : summary.error ? <ErrorState error={summary.error} /> : <div className="table-wrap"><table className="table-dense">
        <thead><tr><th>SBU</th><th className="text-right">ALCs</th><th className="text-right">Active</th><th className="text-right">Partners</th><th className="text-right">Submitted</th><th className="text-right">Pending</th><th className="text-right">Correction</th><th className="text-right">Verified</th><th className="text-right">Rejected</th><th className="text-right">Learners</th><th className="text-right">Leads</th><th className="text-right">Admissions</th></tr></thead>
        <tbody>{summary.data?.items.map(r => <tr key={r.sbu_id}><td className="whitespace-nowrap font-semibold" title={r.sbu_name}>{r.sbu_code}</td>{[r.alcs, r.active_alcs, r.partners, r.activities, r.pending, r.corrections, r.verified, r.rejected, r.learners, r.leads, r.admissions].map((v, i) => <td key={i} className="text-right">{formatNumber(v)}</td>)}</tr>)}</tbody>
        {summary.data && <tfoot><tr className="border-t bg-slate-50 font-semibold"><td className="px-4 py-3">Total</td>{[summary.data.totals.alcs, summary.data.totals.active_alcs, summary.data.totals.partners, summary.data.totals.activities, summary.data.totals.pending, summary.data.totals.corrections, summary.data.totals.verified, summary.data.totals.rejected, summary.data.totals.learners, summary.data.totals.leads, summary.data.totals.admissions].map((v, i) => <td key={i} className="px-4 py-3 text-right">{formatNumber(v)}</td>)}</tr></tfoot>}
      </table></div>}
    </section>
    <section className="grid gap-4 lg:grid-cols-2">{exports.map(([title, text, href]) => <div key={title} className="panel flex flex-col p-5"><h2 className="font-bold text-navy">{title}</h2><p className="mt-1 flex-1 text-sm text-slate-500">{text}</p><a className="btn-secondary mt-4 self-start" href={api.downloadUrl(href)}><Download className="h-4 w-4" />Download CSV</a></div>)}</section>
  </>
}
