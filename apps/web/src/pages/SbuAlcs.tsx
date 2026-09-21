import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { Page } from '../types'
import { Badge, Empty, ErrorState, formatDate, formatNumber, Loading, PageHeader } from '../components/ui'

interface AlcRow { id: string; alc_code: string; alc_name: string; status: string; activities: number; verified: number; pending: number; learners: number; last_activity?: string }

export default function SbuAlcs() {
  const [search, setSearch] = useState(''); const [page, setPage] = useState(1)
  const q = useQuery({ queryKey: ['sbu-alcs', search, page], queryFn: () => api.get<Page<AlcRow>>(`/portal/alcs?page=${page}&page_size=25&search=${encodeURIComponent(search)}`) })
  return <><PageHeader title="Assigned ALCs" description="Centres assigned to your SBU, with verified output and pending reviews." />
    <div className="panel mb-4 p-4"><input placeholder="Search ALC code or centre name" value={search} onChange={e => { setSearch(e.target.value); setPage(1) }} /></div>
    {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : !q.data?.items.length ? <Empty title="No assigned ALCs" message="ALCs assigned to your SBU will appear here." /> : <><div className="table-wrap"><table><thead><tr><th>ALC</th><th>Status</th><th>Activities</th><th>Verified</th><th>Pending</th><th>Verified learners</th><th>Last activity</th></tr></thead><tbody>{q.data.items.map(x => <tr key={x.id}><td><Link to={`/portal/alcs/${x.id}`} className="font-semibold text-navy hover:text-teal">{x.alc_code}</Link><p className="text-xs text-slate-500">{x.alc_name}</p></td><td><Badge status={x.status} /></td><td>{x.activities}</td><td>{x.verified}</td><td>{x.pending}</td><td>{formatNumber(x.learners)}</td><td>{formatDate(x.last_activity)}</td></tr>)}</tbody></table></div>
      <div className="mt-4 flex justify-end gap-2"><button className="btn-secondary" disabled={page === 1} onClick={() => setPage(x => x - 1)}>Previous</button><button className="btn-secondary" disabled={page >= q.data.pages} onClick={() => setPage(x => x + 1)}>Next</button></div></>}
  </>
}
