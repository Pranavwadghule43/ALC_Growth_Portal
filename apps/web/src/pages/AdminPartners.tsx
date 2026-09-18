import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Alc, Page, Partner } from '../types'
import { Badge, ErrorState, Loading, PageHeader } from '../components/ui'
type Row={partner:Partner;alc:Alc}
export default function AdminPartners(){const q=useQuery({queryKey:['admin-partners'],queryFn:()=>api.get<Page<Row>>('/admin/partners?page=1&page_size=100')});return <><PageHeader title="Partners" description="Partners maintained by all ALCs."/>{q.isLoading?<Loading/>:q.error?<ErrorState error={q.error}/>:<div className="table-wrap"><table><thead><tr><th>Partner</th><th>ALC</th><th>Type</th><th>Ecosystem</th><th>Location</th><th>Status</th></tr></thead><tbody>{q.data?.items.map(({partner:p,alc})=><tr key={p.id}><td className="font-semibold">{p.partner_name}</td><td>{alc.alc_code}<p className="text-xs text-slate-500">{alc.alc_name}</p></td><td>{p.partner_type}</td><td>{p.ecosystem}</td><td>{p.location??'—'}</td><td><Badge status={p.status}/></td></tr>)}</tbody></table></div>}</>}

