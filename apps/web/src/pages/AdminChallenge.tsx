import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { toQuery } from '../lib/constants'
import { useDebouncedSearch, usePageFor } from '../lib/hooks'
import type { Page } from '../types'
import { Empty, ErrorState, Loading, PageHeader, Pager } from '../components/ui'
interface Row{id:string;alc_code:string;alc_name:string;prospects:number;meetings:number;pilots:number;partnerships:number}
// Server-side paginated and searchable: every ALC is reachable, one page is fetched at a time.
const CHALLENGE_PAGE_SIZE = 100
export default function AdminChallenge(){const [searchText,setSearchText]=useState('');const search=useDebouncedSearch(searchText);const [page,setPage]=usePageFor(search);const query=toQuery({page,page_size:CHALLENGE_PAGE_SIZE,search});const q=useQuery({queryKey:['admin-challenge',query],queryFn:()=>api.get<Page<Row>&{targets:Record<string,number>}>(`/admin/challenge?${query}`),placeholderData:keepPreviousData});return <><PageHeader title="Growth Challenge" description="Progress calculated from verified activities and active partner records."/><div className="panel mb-4 p-4"><input aria-label="Search ALCs" placeholder="Search ALC code or centre name" value={searchText} onChange={e=>setSearchText(e.target.value)}/></div>{q.isLoading?<Loading/>:q.error?<ErrorState error={q.error}/>:!q.data?.items.length?<Empty title="No ALCs match" message="Try a different ALC code or centre name."/>:<><div className="table-wrap"><table><thead><tr><th>ALC</th>{Object.keys(q.data?.targets??{}).map(k=><th key={k} className="capitalize">{k}</th>)}</tr></thead><tbody>{q.data?.items.map(r=><tr key={r.id}><td><b>{r.alc_code}</b><p className="text-xs text-slate-500">{r.alc_name}</p></td>{Object.entries(q.data!.targets).map(([k,target])=><td key={k}><b>{r[k as keyof Row] as number}</b><span className="text-slate-400"> / {target}</span></td>)}</tr>)}</tbody></table></div><Pager page={q.data.page} pages={q.data.pages} total={q.data.total} noun="ALCs" onPage={setPage}/></>}</>}
