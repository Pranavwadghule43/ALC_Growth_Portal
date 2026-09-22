import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { Activity, Page } from '../types'
import { Badge, Empty, ErrorState, formatDate, Loading, PageHeader, RowActions } from '../components/ui'

type Row = { activity: Activity; alc: { id: string; alc_code: string; alc_name: string; status: string } }
type AlcRow = { id: string; alc_code: string; alc_name: string }

const activityTypes = ['Prospect outreach', 'Partner meeting', 'Pilot programme', 'Collaboration event', 'Career awareness session', 'Admission campaign', 'Community engagement', 'Training programme']

export default function SbuActivities({ queueOnly = false }: { queueOnly?: boolean }) {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ status: '', alc_id: '', activity_type: '', date_from: '', date_to: '', search: '' })
  const update = (patch: Partial<typeof filters>) => { setFilters(f => ({ ...f, ...patch })); setPage(1) }
  const alcs = useQuery({ queryKey: ['sbu-alcs-filter'], queryFn: () => api.get<Page<AlcRow>>('/portal/alcs?page=1&page_size=100') })
  const params = new URLSearchParams({ page: String(page), page_size: '25', ...(queueOnly ? { queue_only: 'true' } : {}), ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) })
  const { data, isLoading, error } = useQuery({ queryKey: ['sbu-activities', queueOnly, page, filters], queryFn: () => api.get<Page<Row>>(`/portal/verification?${params}`) })
  return <><PageHeader title={queueOnly ? 'Verification Queue' : 'Activities'} description={queueOnly ? 'Activities from your assigned ALCs awaiting a review decision.' : 'All activities submitted by your assigned ALCs.'} />
    <div className="panel mb-4 grid gap-3 p-4 md:grid-cols-2 xl:grid-cols-4">
      <input placeholder="Search activity or ALC" value={filters.search} onChange={e => update({ search: e.target.value })} />
      <select value={filters.alc_id} onChange={e => update({ alc_id: e.target.value })}><option value="">All ALCs</option>{alcs.data?.items.map(a => <option key={a.id} value={a.id}>{a.alc_code} · {a.alc_name}</option>)}</select>
      <select value={filters.activity_type} onChange={e => update({ activity_type: e.target.value })}><option value="">All types</option>{activityTypes.map(t => <option key={t} value={t}>{t}</option>)}</select>
      {!queueOnly && <select value={filters.status} onChange={e => update({ status: e.target.value })}><option value="">All statuses</option>{['SUBMITTED', 'RESUBMITTED', 'UNDER_REVIEW', 'CORRECTION_REQUIRED', 'VERIFIED', 'REJECTED'].map(s => <option key={s}>{s}</option>)}</select>}
      <div><label className="text-xs text-slate-500">From</label><input type="date" value={filters.date_from} onChange={e => update({ date_from: e.target.value })} /></div>
      <div><label className="text-xs text-slate-500">To</label><input type="date" value={filters.date_to} onChange={e => update({ date_to: e.target.value })} /></div>
    </div>
    {isLoading ? <Loading /> : error ? <ErrorState error={error} /> : !data?.items.length ? <Empty title="Nothing to review" message="Activities from your ALCs will appear here." /> : <><div className="table-wrap"><table><thead><tr><th>Activity</th><th>ALC</th><th>Activity date</th><th>Submitted</th><th>Evidence</th><th>Status</th><th>Actions</th></tr></thead><tbody>{data.items.map(({ activity: a, alc }) => <tr key={a.id}><td><Link to={`/portal/activities/${a.id}`} className="font-semibold text-navy hover:text-teal">{a.activity_number}</Link><p className="text-xs text-slate-500">{a.activity_type}</p></td><td>{alc.alc_code}<p className="text-xs text-slate-500">{alc.alc_name}</p></td><td>{formatDate(a.activity_date)}</td><td>{formatDate(a.submitted_at)}</td><td>{a.evidence.length}</td><td><Badge status={a.status} /></td><td><RowActions base="/portal/activities" id={a.id} status={a.status} /></td></tr>)}</tbody></table></div>
      <div className="mt-4 flex items-center justify-between text-sm"><span>Page {data.page} of {Math.max(data.pages, 1)} · {data.total} records</span><div className="flex gap-2"><button className="btn-secondary" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</button><button className="btn-secondary" disabled={page >= data.pages} onClick={() => setPage(p => p + 1)}>Next</button></div></div></>}
  </>
}