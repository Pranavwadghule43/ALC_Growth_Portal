import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Page } from '../types'
import { ErrorState, formatDate, Loading, PageHeader } from '../components/ui'
interface Log{id:string;action:string;entity_type:string;entity_id?:string;actor_role?:string;alc_id?:string;ip_address?:string;created_at:string;audit_metadata:Record<string,unknown>}
export default function AdminAudit(){const q=useQuery({queryKey:['audit'],queryFn:()=>api.get<Page<Log>>('/admin/audit-logs?page=1&page_size=100')});return <><PageHeader title="Audit Logs" description="Append-only record of security and workflow actions."/>{q.isLoading?<Loading/>:q.error?<ErrorState error={q.error}/>:<div className="table-wrap"><table><thead><tr><th>Time</th><th>Action</th><th>Actor</th><th>Entity</th><th>IP address</th></tr></thead><tbody>{q.data?.items.map(x=><tr key={x.id}><td>{formatDate(x.created_at)}</td><td className="font-semibold">{x.action.replaceAll('_',' ')}</td><td>{x.actor_role??'System'}</td><td>{x.entity_type}<p className="max-w-[180px] truncate text-xs text-slate-500">{x.entity_id}</p></td><td>{x.ip_address??'—'}</td></tr>)}</tbody></table></div>}</>}

