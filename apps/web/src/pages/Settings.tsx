import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { ErrorState, Loading, PageHeader } from '../components/ui'
export default function Settings(){const q=useQuery({queryKey:['settings'],queryFn:()=>api.get<Record<string,unknown>>('/admin/settings')});return <><PageHeader title="System Settings" description="Effective security and evidence-upload policy."/>{q.isLoading?<Loading/>:q.error?<ErrorState error={q.error}/>:<div className="panel max-w-2xl divide-y">{Object.entries(q.data??{}).map(([k,v])=><div key={k} className="flex items-start justify-between gap-8 p-5"><span className="text-sm font-semibold capitalize text-slate-600">{k.replaceAll('_',' ')}</span><span className="text-right text-sm text-navy">{Array.isArray(v)?v.join(', '):String(v)}</span></div>)}</div>}</>}

