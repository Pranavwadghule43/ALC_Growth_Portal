import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Page } from '../types'
import { ErrorState, Loading, PageHeader } from '../components/ui'
interface Row{id:string;alc_code:string;alc_name:string;prospects:number;meetings:number;pilots:number;partnerships:number}
export default function AdminChallenge(){const q=useQuery({queryKey:['admin-challenge'],queryFn:()=>api.get<Page<Row>&{targets:Record<string,number>}>('/admin/challenge?page=1&page_size=100')});return <><PageHeader title="30-Day Challenge" description="Progress calculated from verified activities and active partner records."/>{q.isLoading?<Loading/>:q.error?<ErrorState error={q.error}/>:<div className="table-wrap"><table><thead><tr><th>ALC</th>{Object.keys(q.data?.targets??{}).map(k=><th key={k} className="capitalize">{k}</th>)}</tr></thead><tbody>{q.data?.items.map(r=><tr key={r.id}><td><b>{r.alc_code}</b><p className="text-xs text-slate-500">{r.alc_name}</p></td>{Object.entries(q.data!.targets).map(([k,target])=><td key={k}><b>{r[k as keyof Row] as number}</b><span className="text-slate-400"> / {target}</span></td>)}</tr>)}</tbody></table></div>}</>}

