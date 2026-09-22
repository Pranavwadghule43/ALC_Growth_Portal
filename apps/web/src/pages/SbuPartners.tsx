import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { Badge, Empty, ErrorState, formatDate, Loading, PageHeader } from '../components/ui'

interface SbuPartner {
  id: string; alc_id: string; alc_code: string; alc_name: string
  partner_name: string; partner_type: string; ecosystem: string
  contact_person?: string; phone?: string; email?: string; status: string
  activity_count: number; last_activity?: string
}

export default function SbuPartners() {
  const [search, setSearch] = useState(''); const [alcId, setAlcId] = useState('')
  const { data, isLoading, error } = useQuery({ queryKey: ['sbu-partners'], queryFn: () => api.get<SbuPartner[]>('/portal/sbu/partners') })
  const alcOptions = useMemo(() => {
    const map = new Map<string, string>()
    ;(data ?? []).forEach(p => map.set(p.alc_id, `${p.alc_code} · ${p.alc_name}`))
    return Array.from(map, ([id, label]) => ({ id, label }))
  }, [data])
  const rows = (data ?? []).filter(p => (!alcId || p.alc_id === alcId) && (!search || p.partner_name.toLowerCase().includes(search.toLowerCase())))
  return <><PageHeader title="Partners" description="Partners maintained by the ALCs assigned to your SBU." />
    <div className="panel mb-4 grid gap-3 p-4 md:grid-cols-2">
      <input placeholder="Search partner name" value={search} onChange={e => setSearch(e.target.value)} />
      <select value={alcId} onChange={e => setAlcId(e.target.value)}><option value="">All ALCs</option>{alcOptions.map(a => <option key={a.id} value={a.id}>{a.label}</option>)}</select>
    </div>
    {isLoading ? <Loading /> : error ? <ErrorState error={error} /> : !rows.length ? <Empty title="No partners" message="Partners created by your assigned ALCs will appear here." /> : <div className="table-wrap"><table><thead><tr><th>Partner</th><th>ALC</th><th>Type</th><th>Ecosystem</th><th>Contact</th><th>Activities</th><th>Last activity</th><th>Status</th></tr></thead><tbody>{rows.map(p => <tr key={p.id}><td className="font-semibold">{p.partner_name}</td><td>{p.alc_code}<p className="text-xs text-slate-500">{p.alc_name}</p></td><td>{p.partner_type}</td><td>{p.ecosystem}</td><td>{p.contact_person ?? '—'}<p className="text-xs text-slate-500">{p.phone ?? p.email}</p></td><td>{p.activity_count}</td><td>{formatDate(p.last_activity)}</td><td><Badge status={p.status} /></td></tr>)}</tbody></table></div>}
  </>
}