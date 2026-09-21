import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { Activity, Page } from '../types'
import { Badge, Empty, ErrorState, formatDate, Loading, PageHeader } from '../components/ui'

type Row = { activity: Activity; alc: { id: string; alc_code: string; alc_name: string; status: string } }

export default function SbuActivities({ queueOnly = false }: { queueOnly?: boolean }) {
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const params = new URLSearchParams({ page: String(page), page_size: '25', ...(queueOnly ? { queue_only: 'true' } : {}), ...(status ? { status } : {}), ...(search ? { search } : {}) })
  const { data, isLoading, error } = useQuery({ queryKey: ['sbu-activities', queueOnly, page, status, search], queryFn: () => api.get<Page<Row>>(`/portal/verification?${params}`) })
  return <><PageHeader title={queueOnly ? 'Verification Queue' : 'Activities'} description={queueOnly ? 'Activities from your assigned ALCs awaiting a review decision.' : 'All activities submitted by your assigned ALCs.'} />
    <div className="panel mb-4 grid gap-3 p-4 md:grid-cols-3">
      <input placeholder="Search activity or ALC" value={search} onChange={e => { setSearch(e.target.value); setPage(1) }} />
      {!queueOnly && <select value={status} onChange={e => { setStatus(e.target.value); setPage(1) }}><option value="">All statuses</option>{['SUBMITTED', 'RESUBMITTED', 'UNDER_REVIEW', 'CORRECTION_REQUIRED', 'VERIFIED', 'REJECTED'].map(s => <option key={s}>{s}</option>)}</select>}
    </div>
    {isLoading ? <Loading /> : error ? <ErrorState error={error} /> : !data?.items.length ? <Empty title="Nothing to review" message="Activities from your ALCs will appear here." /> : <><div className="table-wrap"><table><thead><tr><th>Activity</th><th>ALC</th><th>Date</th><th>Evidence</th><th>Status</th></tr></thead><tbody>{data.items.map(({ activity: a, alc }) => <tr key={a.id}><td><Link to={`/portal/activities/${a.id}`} className="font-semibold text-navy hover:text-teal">{a.activity_number}</Link><p className="text-xs text-slate-500">{a.activity_type}</p></td><td>{alc.alc_code}<p className="text-xs text-slate-500">{alc.alc_name}</p></td><td>{formatDate(a.activity_date)}</td><td>{a.evidence.length}</td><td><Badge status={a.status} /></td></tr>)}</tbody></table></div>
      <div className="mt-4 flex items-center justify-between text-sm"><span>Page {data.page} of {Math.max(data.pages, 1)} · {data.total} records</span><div className="flex gap-2"><button className="btn-secondary" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</button><button className="btn-secondary" disabled={page >= data.pages} onClick={() => setPage(p => p + 1)}>Next</button></div></div></>}
  </>
}
