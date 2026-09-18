import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Download, Plus } from 'lucide-react'
import { api } from '../lib/api'
import type { Activity, Page, Status } from '../types'
import { Badge, Empty, ErrorState, formatDate, Loading, PageHeader } from '../components/ui'

export default function Activities() {
  const [page,setPage] = useState(1); const [status,setStatus] = useState<Status | ''>(''); const [search,setSearch] = useState('')
  const params = new URLSearchParams({ page: String(page), page_size: '25' }); if (status) params.set('status', status); if (search) params.set('search', search)
  const { data,isLoading,error } = useQuery({ queryKey:['activities',page,status,search], queryFn:()=>api.get<Page<Activity>>(`/alc/activities?${params}`) })
  return <><PageHeader title="My Activities" description="Draft, submitted, and reviewed activities for your centre." actions={<div className="flex gap-2"><a className="btn-secondary" href={api.downloadUrl('/alc/reports/activities.csv')}><Download className="h-4 w-4"/>Export CSV</a><Link className="btn-primary" to="/alc/activities/new"><Plus className="h-4 w-4"/>Add activity</Link></div>}/>
    <div className="panel mb-4 grid gap-3 p-4 md:grid-cols-[1fr_220px]"><input placeholder="Search activity number or type" value={search} onChange={(e)=>{setSearch(e.target.value);setPage(1)}}/><select value={status} onChange={(e)=>{setStatus(e.target.value as Status|'');setPage(1)}}><option value="">All statuses</option>{['DRAFT','SUBMITTED','UNDER_REVIEW','CORRECTION_REQUIRED','RESUBMITTED','VERIFIED','REJECTED'].map(s=><option key={s}>{s}</option>)}</select></div>
    {isLoading ? <Loading/> : error ? <ErrorState error={error}/> : !data?.items.length ? <Empty title="No activities found" message="Create an activity or adjust the current filters."/> : <><div className="table-wrap"><table><thead><tr><th>Activity</th><th>Date</th><th>Partner</th><th>Evidence</th><th>Status</th><th>Admin remark</th><th>Updated</th></tr></thead><tbody>{data.items.map(a=><tr key={a.id}><td><Link to={`/alc/activities/${a.id}`} className="font-semibold text-navy hover:text-teal">{a.activity_number}</Link><p className="text-xs text-slate-500">{a.activity_type}</p></td><td>{formatDate(a.activity_date)}</td><td>{a.partner?.partner_name ?? '—'}</td><td>{a.evidence.length}</td><td><Badge status={a.status}/></td><td className="max-w-xs text-slate-600">{[...a.reviews].reverse().find(r=>r.remark)?.remark ?? '—'}</td><td>{formatDate(a.updated_at)}</td></tr>)}</tbody></table></div><div className="mt-4 flex items-center justify-between text-sm"><span>Page {data.page} of {Math.max(data.pages,1)} · {data.total} records</span><div className="flex gap-2"><button className="btn-secondary" disabled={page<=1} onClick={()=>setPage(p=>p-1)}>Previous</button><button className="btn-secondary" disabled={page>=data.pages} onClick={()=>setPage(p=>p+1)}>Next</button></div></div></>}
  </>
}

