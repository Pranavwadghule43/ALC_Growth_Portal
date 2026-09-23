import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { ClipboardCheck, Eye } from 'lucide-react'
import { api } from '../lib/api'
import { ACTIVITY_TYPES, toQuery, WORKFLOW_STATUSES } from '../lib/constants'
import type { AlcOption, Page, QueueRow, SbuRow } from '../types'
import { Badge, Empty, ErrorState, Filter, formatDate, formatNumber, Loading, PageHeader, Pager, REVIEWABLE_STATUSES } from '../components/ui'
import { formatDateTime, hadCorrection, resubmittedAt } from '../components/review'

// DCU activity monitoring (all submitted-workflow activities) and verification queue
// (submitted / resubmitted awaiting a decision). Every filter is applied server-side and can
// only narrow the DCU's own scope.
export default function DcuActivities({ queueOnly = false }: { queueOnly?: boolean }) {
  const [params] = useSearchParams()
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ sbu_id: params.get('sbu_id') ?? '', alc_id: params.get('alc_id') ?? '', status: params.get('status') ?? '', activity_type: '', date_from: '', date_to: '', search: '' })
  const update = (patch: Partial<typeof filters>) => { setFilters(f => ({ ...f, ...patch })); setPage(1) }
  const sbus = useQuery({ queryKey: ['dcu-sbus'], queryFn: () => api.get<{ items: SbuRow[] }>('/portal/sbus') })
  const alcs = useQuery({ queryKey: ['alc-options', filters.sbu_id], queryFn: () => api.get<{ items: AlcOption[] }>(`/portal/lookups/alcs${filters.sbu_id ? `?sbu_id=${filters.sbu_id}` : ''}`) })
  const query = toQuery({ page, page_size: 25, queue_only: queueOnly, ...filters, status: queueOnly ? '' : filters.status, search: filters.search.trim() })
  const { data, isLoading, error } = useQuery({ queryKey: ['dcu-activities', query], queryFn: () => api.get<Page<QueueRow>>(`/portal/verification?${query}`), placeholderData: prev => prev })
  const reviewBase = queueOnly ? '/portal/verification' : '/portal/activities'
  return <><PageHeader title={queueOnly ? 'Verification Queue' : 'Activity Monitoring'} description={queueOnly ? 'Submitted and resubmitted activities across your DCU awaiting a review decision, oldest submissions last.' : 'Every submitted activity across your SBUs and ALCs. Drafts stay private to each ALC.'} />
    <div className="panel mb-4 grid gap-3 p-4 md:grid-cols-4">
      <Filter label="SBU"><select value={filters.sbu_id} onChange={e => update({ sbu_id: e.target.value, alc_id: '' })}><option value="">All SBUs</option>{sbus.data?.items.map(s => <option key={s.id} value={s.id}>{s.code}</option>)}</select></Filter>
      <Filter label="ALC"><select value={filters.alc_id} onChange={e => update({ alc_id: e.target.value })}><option value="">{alcs.isLoading ? 'Loading ALCs…' : `All ALCs (${formatNumber(alcs.data?.items.length ?? 0)})`}</option>{alcs.data?.items.map(a => <option key={a.id} value={a.id}>{a.alc_code} · {a.alc_name}</option>)}</select></Filter>
      {!queueOnly && <Filter label="Status"><select value={filters.status} onChange={e => update({ status: e.target.value })}><option value="">All statuses</option>{WORKFLOW_STATUSES.map(s => <option key={s} value={s}>{s.replaceAll('_', ' ')}</option>)}</select></Filter>}
      <Filter label="Activity type"><select value={filters.activity_type} onChange={e => update({ activity_type: e.target.value })}><option value="">All types</option>{ACTIVITY_TYPES.map(t => <option key={t}>{t}</option>)}</select></Filter>
      <Filter label="Activity date from"><input type="date" value={filters.date_from} onChange={e => update({ date_from: e.target.value })} /></Filter>
      <Filter label="Activity date to"><input type="date" value={filters.date_to} onChange={e => update({ date_to: e.target.value })} /></Filter>
      <Filter label="Search"><input placeholder="Activity number, ALC code or name" value={filters.search} onChange={e => update({ search: e.target.value })} /></Filter>
    </div>
    {isLoading ? <Loading /> : error ? <ErrorState error={error} /> : !data?.items.length ? <Empty title={queueOnly ? 'Queue is clear' : 'No activities'} message={queueOnly ? 'No submitted or resubmitted activities match these filters.' : 'No submitted activities match these filters.'} /> : <><div className="table-wrap"><table className="table-dense">
      {queueOnly
        ? <thead><tr><th>ALC</th><th>SBU</th><th>Activity</th><th>Submission time</th><th>Current status</th><th className="text-right">Evidence</th><th>Previous correction</th><th>Actions</th></tr></thead>
        : <thead><tr><th>Activity</th><th>ALC</th><th>SBU</th><th>Activity type</th><th>Status</th><th>Submitted / resubmitted</th><th className="text-right">Evidence</th><th className="text-right" title="Learner reach · Leads · Admissions">Learners · Leads · Adm.</th><th>Actions</th></tr></thead>}
      <tbody>{data.items.map(({ activity: a, alc, sbu }) => {
        const reviewable = REVIEWABLE_STATUSES.includes(a.status)
        const action = <Link to={`${reviewBase}/${a.id}`} className={reviewable ? 'inline-flex items-center gap-1 rounded-md bg-teal px-2 py-1 text-xs font-semibold text-white hover:bg-teal/90' : 'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-semibold text-navy hover:border-teal hover:text-teal'}>{reviewable ? <><ClipboardCheck className="h-3.5 w-3.5" />Review</> : <><Eye className="h-3.5 w-3.5" />View</>}</Link>
        const alcCell = <td><Link to={`/portal/alcs/${alc.id}`} className="font-medium hover:text-teal">{alc.alc_code}</Link><p className="text-xs text-slate-500">{alc.alc_name}</p></td>
        const resub = resubmittedAt(a)
        return queueOnly
          ? <tr key={a.id}>{alcCell}<td>{sbu?.code ?? '—'}</td><td><Link to={`${reviewBase}/${a.id}`} className="font-semibold text-navy hover:text-teal">{a.activity_number}</Link><p className="text-xs text-slate-500">{a.activity_type} · {formatDate(a.activity_date)}</p></td><td className="text-sm">{formatDateTime(resub ?? a.submitted_at)}</td><td><Badge status={a.status} /></td><td className="text-right">{a.evidence.length}</td><td>{hadCorrection(a) ? <span className="text-sm font-medium text-amber-800">Yes</span> : <span className="text-sm text-slate-400">No</span>}</td><td>{action}</td></tr>
          : <tr key={a.id}><td><Link to={`${reviewBase}/${a.id}`} className="font-semibold text-navy hover:text-teal">{a.activity_number}</Link><p className="text-xs text-slate-500">{formatDate(a.activity_date)}</p></td>{alcCell}<td>{sbu?.code ?? '—'}</td><td>{a.activity_type}</td><td><Badge status={a.status} /></td><td className="text-sm">{formatDateTime(a.submitted_at)}{resub && <p className="text-xs text-violet-700">Resubmitted {formatDateTime(resub)}</p>}</td><td className="text-right">{a.evidence.length}</td><td className="whitespace-nowrap text-right tabular-nums">{formatNumber(a.learners_reached)} · {formatNumber(a.leads_generated)} · {formatNumber(a.admissions_generated)}</td><td>{action}</td></tr>
      })}</tbody>
    </table></div><Pager page={data.page} pages={data.pages} total={data.total} noun="activities" onPage={setPage} /></>}
  </>
}
