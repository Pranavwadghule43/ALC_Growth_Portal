import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Page, Partner } from '../types'
import { Badge, Empty, ErrorState, Loading, PageHeader } from '../components/ui'

interface AlcRow { id: string; alc_code: string; alc_name: string }

export default function SbuPartners() {
  const partners = useQuery({ queryKey: ['sbu-partners'], queryFn: () => api.get<Partner[]>('/portal/partners') })
  const alcs = useQuery({ queryKey: ['sbu-alcs-map'], queryFn: () => api.get<Page<AlcRow>>('/portal/alcs?page=1&page_size=100') })
  const nameFor = (alcId: string) => { const a = alcs.data?.items.find(x => x.id === alcId); return a ? `${a.alc_code} · ${a.alc_name}` : '—' }
  return <><PageHeader title="Partners" description="Partners maintained by the ALCs assigned to your SBU." />
    {partners.isLoading ? <Loading /> : partners.error ? <ErrorState error={partners.error} /> : !partners.data?.length ? <Empty title="No partners" message="Partners created by your assigned ALCs will appear here." /> : <div className="table-wrap"><table><thead><tr><th>Partner</th><th>ALC</th><th>Type</th><th>Ecosystem</th><th>Contact</th><th>Status</th></tr></thead><tbody>{partners.data.map(p => <tr key={p.id}><td className="font-semibold">{p.partner_name}</td><td>{nameFor(p.alc_id)}</td><td>{p.partner_type}</td><td>{p.ecosystem}</td><td>{p.contact_person ?? '—'}<p className="text-xs text-slate-500">{p.phone ?? p.email}</p></td><td><Badge status={p.status} /></td></tr>)}</tbody></table></div>}
  </>
}
