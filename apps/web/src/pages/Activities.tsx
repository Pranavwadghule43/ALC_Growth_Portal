import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertTriangle, Download, Plus } from 'lucide-react'
import { api } from '../lib/api'
import type { Activity, Page, Status } from '../types'
import { Badge, Empty, ErrorState, formatDate, Loading, PageHeader } from '../components/ui'

const STATUSES: Status[] = ['DRAFT','SUBMITTED','UNDER_REVIEW','CORRECTION_REQUIRED','RESUBMITTED','VERIFIED','REJECTED']
const REVIEW_LABEL: Record<string,string> = { VERIFY:'Verified', REQUEST_CORRECTION:'Correction', REJECT:'Rejected' }

function rowAction(a: Activity) {
  if (a.status === 'CORRECTION_REQUIRED') return { label: 'Fix & resubmit', className: 'btn-primary' }
  if (a.status === 'DRAFT') return { label: 'Continue editing', className: 'btn-secondary' }
  return { label: 'View', className: 'btn-secondary' }
}

export default function Activities() {
  const [params, setParams] = useSearchParams()
  const initial = params.get('status') as Status | null
  const [page,setPage] = useState(1); const [status,setStatusState] = useState<Status | ''>(initial && STATUSES.includes(initial) ? initial : ''); const [search,setSearch] = useState('')
  function setStatus(next: Status | '') { setStatusState(next); setPage(1); const p = new URLSearchParams(params); if (next) p.set('status', next); else p.delete('status'); setParams(p, { replace: true }) }
  const query = new URLSearchParams({ page: String(page), page_size: '25' }); if (status) query.set('status', status); if (search) query.set('search', search)
  const { data,isLoading,error } = useQuery({ queryKey:['activities',page,status,search], queryFn:()=>api.get<Page<Activity>>(`/portal/activities?${query}`) })
  const correctionOnly = status === 'CORRECTION_REQUIRED'
  return <><PageHeader title="My Activities" description="Draft, submitted, and reviewed activities for your centre." actions={<div className="flex gap-2"><a className="btn-secondary" href={api.downloadUrl('/portal/reports/activities.csv')}><Download className="h-4 w-4"/>Export CSV</a><Link className="btn-primary" to="/portal/activities/new"><Plus className="h-4 w-4"/>Add activity</Link></div>}/>
    <div className="panel mb-4 grid gap-3 p-4 md:grid-cols-[1fr_240px_auto]"><input placeholder="Search activity number or type" value={search} onChange={(e)=>{setSearch(e.target.value);setPage(1)}}/><select value={status} onChange={(e)=>setStatus(e.target.value as Status|'')}><option value="">All statuses</option>{STATUSES.map(s=><option key={s} value={s}>{s.replaceAll('_',' ')}</option>)}</select><button type="button" onClick={()=>setStatus(correctionOnly?'':'CORRECTION_REQUIRED')} className={`inline-flex items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold ${correctionOnly?'border-amber-400 bg-amber-100 text-amber-900':'border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100'}`}><AlertTriangle className="h-4 w-4"/>{correctionOnly?'Showing: needs correction':'Needs correction'}</button></div>
    {isLoading ? <Loading/> : error ? <ErrorState error={error}/> : !data?.items.length ? <Empty title={correctionOnly?'Nothing needs correction':'No activities found'} message={correctionOnly?'No reviewer has asked for changes on any activity.':'Create an activity or adjust the current filters.'}/> : <><div className="table-wrap"><table><thead><tr><th>Activity</th><th>Date</th><th>Partner</th><th>Evidence</th><th>Status</th><th>Latest reviewer remark</th><th>Updated</th><th className="text-right">Action</th></tr></thead><tbody>{data.items.map(a=>{ const last=[...a.reviews].reverse().find(r=>r.remark); const act=rowAction(a); return <tr key={a.id} className={a.status==='CORRECTION_REQUIRED'?'bg-amber-50/60':undefined}><td><Link to={`/portal/activities/${a.id}`} className="font-semibold text-navy hover:text-teal">{a.activity_number}</Link><p className="text-xs text-slate-500">{a.activity_type}</p></td><td className="whitespace-nowrap">{formatDate(a.activity_date)}</td><td className="max-w-[14rem] truncate" title={a.partner?.partner_name}>{a.partner?.partner_name ?? '—'}</td><td>{a.evidence.length}</td><td><Badge status={a.status}/></td><td className="max-w-xs text-slate-600">{last ? <><span className="text-xs font-semibold uppercase text-slate-500">{REVIEW_LABEL[last.action] ?? last.action}</span><p className="line-clamp-2" title={last.remark}>{last.remark}</p></> : '—'}</td><td className="whitespace-nowrap">{formatDate(a.updated_at)}</td><td className="text-right"><Link to={`/portal/activities/${a.id}`} className={`${act.className} whitespace-nowrap`}>{act.label}</Link></td></tr> })}</tbody></table></div><div className="mt-4 flex items-center justify-between text-sm"><span>Page {data.page} of {Math.max(data.pages,1)} · {data.total} records</span><div className="flex gap-2"><button className="btn-secondary" disabled={page<=1} onClick={()=>setPage(p=>p-1)}>Previous</button><button className="btn-secondary" disabled={page>=data.pages} onClick={()=>setPage(p=>p+1)}>Next</button></div></div></>}
  </>
}