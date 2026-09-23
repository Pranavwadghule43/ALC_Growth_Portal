import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { toQuery } from '../lib/constants'
import type { AlcOption, DirectoryPartner, Page, SbuRow } from '../types'
import { Badge, Empty, ErrorState, Filter, formatDate, formatNumber, Loading, PageHeader, Pager } from '../components/ui'

// Partners of every ALC in the DCU; server-side filtered and paginated.
export default function DcuPartners() {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ sbu_id: '', alc_id: '', search: '', partner_type: '' })
  const update = (patch: Partial<typeof filters>) => { setFilters(f => ({ ...f, ...patch })); setPage(1) }
  const sbus = useQuery({ queryKey: ['dcu-sbus'], queryFn: () => api.get<{ items: SbuRow[] }>('/portal/sbus') })
  const alcs = useQuery({ queryKey: ['alc-options', filters.sbu_id], queryFn: () => api.get<{ items: AlcOption[] }>(`/portal/lookups/alcs${filters.sbu_id ? `?sbu_id=${filters.sbu_id}` : ''}`) })
  const query = toQuery({ page, page_size: 25, ...filters, search: filters.search.trim() })
  const q = useQuery({ queryKey: ['dcu-partners', query], queryFn: () => api.get<Page<DirectoryPartner> & { partner_types: string[] }>(`/portal/partner-directory?${query}`), placeholderData: prev => prev })
  return <><PageHeader title="Partners" description="Partners maintained by the ALCs in your DCU, with their activity history." />
    <div className="panel mb-4 grid gap-3 p-4 md:grid-cols-4">
      <Filter label="SBU"><select value={filters.sbu_id} onChange={e => update({ sbu_id: e.target.value, alc_id: '' })}><option value="">All SBUs</option>{sbus.data?.items.map(s => <option key={s.id} value={s.id}>{s.code}</option>)}</select></Filter>
      <Filter label="ALC"><select value={filters.alc_id} onChange={e => update({ alc_id: e.target.value })}><option value="">All ALCs</option>{alcs.data?.items.map(a => <option key={a.id} value={a.id}>{a.alc_code} · {a.alc_name}</option>)}</select></Filter>
      <Filter label="Partner name"><input placeholder="Search partner name" value={filters.search} onChange={e => update({ search: e.target.value })} /></Filter>
      <Filter label="Collaboration type"><select value={filters.partner_type} onChange={e => update({ partner_type: e.target.value })}><option value="">All types</option>{q.data?.partner_types.map(t => <option key={t}>{t}</option>)}</select></Filter>
    </div>
    {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : !q.data?.items.length ? <Empty title="No partners" message="Partners created by ALCs in your DCU will appear here." /> : <><div className="table-wrap"><table>
      <thead><tr><th>Partner</th><th>ALC</th><th>SBU</th><th>Collaboration type</th><th>Contact</th><th className="text-right">Activities</th><th>Last activity</th><th>Status</th></tr></thead>
      <tbody>{q.data.items.map(p => <tr key={p.id}><td className="font-semibold">{p.partner_name}<p className="text-xs font-normal text-slate-500">{p.ecosystem}{p.location ? ` · ${p.location}` : ''}</p></td><td><Link to={`/portal/alcs/${p.alc_id}`} className="hover:text-teal">{p.alc_code}</Link><p className="text-xs text-slate-500">{p.alc_name}</p></td><td>{p.sbu_code ?? '—'}</td><td>{p.partner_type}</td><td>{p.contact_person ?? '—'}<p className="text-xs text-slate-500">{p.phone ?? p.email}</p></td><td className="text-right">{formatNumber(p.activity_count)}</td><td>{formatDate(p.last_activity ?? undefined)}</td><td><Badge status={p.status} /></td></tr>)}</tbody>
    </table></div><Pager page={q.data.page} pages={q.data.pages} total={q.data.total} noun="partners" onPage={setPage} /></>}
  </>
}
