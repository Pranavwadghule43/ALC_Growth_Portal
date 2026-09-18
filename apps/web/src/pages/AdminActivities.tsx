import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router-dom'
import { api } from '../lib/api'
import type { Activity, Alc, Page } from '../types'
import { Badge, Empty, ErrorState, formatDate, Loading, PageHeader } from '../components/ui'

type Row = { activity: Activity; alc: Alc }
export default function AdminActivities() {
  const queue = useLocation().pathname.includes('verification')
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ search: '', status: '', activity_type: '', ecosystem: '', date_from: '', date_to: '' })
  const params = new URLSearchParams({ page: String(page), page_size: '25' })
  if (queue) params.set('queue_only', 'true')
  Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value) })
  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-activities', queue, page, filters],
    queryFn: () => api.get<Page<Row>>(`/admin/${queue ? 'verification-queue' : 'activities'}?${params}`),
  })
  function update(key: keyof typeof filters, value: string) { setFilters(current => ({ ...current, [key]: value })); setPage(1) }
  return <>
    <PageHeader title={queue ? 'Verification Queue' : 'All Activities'} description={queue ? 'Submitted and resubmitted activities awaiting an administrator decision.' : 'Search and review activities across all ALCs.'} />
    <div className="panel mb-4 grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-6">
      <input className="xl:col-span-2" aria-label="Search activities" placeholder="ALC code, centre, partner or activity" value={filters.search} onChange={e => update('search', e.target.value)} />
      <select aria-label="Status filter" value={filters.status} onChange={e => update('status', e.target.value)}><option value="">All statuses</option>{['DRAFT', 'SUBMITTED', 'UNDER_REVIEW', 'CORRECTION_REQUIRED', 'RESUBMITTED', 'VERIFIED', 'REJECTED'].map(s => <option key={s}>{s}</option>)}</select>
      <input aria-label="Activity type filter" placeholder="Activity type" value={filters.activity_type} onChange={e => update('activity_type', e.target.value)} />
      <input aria-label="Ecosystem filter" placeholder="Ecosystem" value={filters.ecosystem} onChange={e => update('ecosystem', e.target.value)} />
      <div className="grid grid-cols-2 gap-2 xl:col-span-1"><input aria-label="Submitted from" type="date" value={filters.date_from} onChange={e => update('date_from', e.target.value)} /><input aria-label="Submitted to" type="date" value={filters.date_to} onChange={e => update('date_to', e.target.value)} /></div>
    </div>
    {isLoading ? <Loading /> : error ? <ErrorState error={error} /> : !data?.items.length ? <Empty title="No activities found" message={queue ? 'No activities currently require review for these filters.' : 'No activities match these filters.'} /> : <>
      <div className="table-wrap"><table><thead><tr><th>Activity ID</th><th>ALC</th><th>Type / Partner</th><th>Activity date</th><th>Submitted</th><th>Evidence</th><th>Status</th></tr></thead><tbody>{data.items.map(({ activity: a, alc }) => <tr key={a.id}><td><Link to={`/admin/activities/${a.id}`} className="font-semibold text-navy hover:text-teal">{a.activity_number}</Link></td><td><b>{alc.alc_code}</b><p className="text-xs text-slate-500">{alc.alc_name}</p></td><td>{a.activity_type}<p className="text-xs text-slate-500">{a.partner?.partner_name ?? 'No partner'}</p></td><td>{formatDate(a.activity_date)}</td><td>{formatDate(a.submitted_at)}</td><td>{a.evidence.length}</td><td><Badge status={a.status} /></td></tr>)}</tbody></table></div>
      <div className="mt-4 flex items-center justify-between text-sm"><span>{data.total} records · page {data.page} of {Math.max(data.pages, 1)}</span><div className="flex gap-2"><button className="btn-secondary" disabled={page === 1} onClick={() => setPage(x => x - 1)}>Previous</button><button className="btn-secondary" disabled={page >= data.pages} onClick={() => setPage(x => x + 1)}>Next</button></div></div>
    </>}
  </>
}
