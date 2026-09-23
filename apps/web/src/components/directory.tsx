import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Eye } from 'lucide-react'
import { api } from '../lib/api'
import { toQuery } from '../lib/constants'
import type { AlcDirectoryRow, Page, SbuRow } from '../types'
import { Badge, Empty, ErrorState, Filter, formatDate, formatNumber, Loading, Pager } from './ui'

// Server-side paginated, filtered ALC directory (``GET /portal/alcs``). Used by the DCU ALC
// directory and inside an SBU's detail page. Only the current page is ever fetched.
export function AlcDirectory({ fixedSbuId, showSbuFilter = false }: { fixedSbuId?: string; showSbuFilter?: boolean }) {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ search: '', status: '', sbu_id: '' })
  const update = (patch: Partial<typeof filters>) => { setFilters(f => ({ ...f, ...patch })); setPage(1) }
  const sbus = useQuery({ queryKey: ['dcu-sbus'], queryFn: () => api.get<{ items: SbuRow[] }>('/portal/sbus'), enabled: showSbuFilter })
  const sbuId = fixedSbuId ?? filters.sbu_id
  const query = toQuery({ page, page_size: 25, search: filters.search.trim(), status: filters.status, sbu_id: sbuId })
  const q = useQuery({ queryKey: ['alc-directory', query], queryFn: () => api.get<Page<AlcDirectoryRow>>(`/portal/alcs?${query}`), placeholderData: prev => prev })
  const showSbuColumn = !fixedSbuId
  return <>
    <div className={`panel mb-4 grid gap-3 p-4 ${showSbuFilter ? 'md:grid-cols-3' : 'md:grid-cols-2'}`}>
      {showSbuFilter && <Filter label="SBU"><select value={filters.sbu_id} onChange={e => update({ sbu_id: e.target.value })}><option value="">All SBUs</option>{sbus.data?.items.map(s => <option key={s.id} value={s.id}>{s.code} · {formatNumber(s.alcs)} ALCs</option>)}</select></Filter>}
      <Filter label="ALC code or name"><input placeholder="e.g. 57210164 or Jayesh" value={filters.search} onChange={e => update({ search: e.target.value })} /></Filter>
      <Filter label="Status"><select value={filters.status} onChange={e => update({ status: e.target.value })}><option value="">Active and inactive</option><option value="ACTIVE">Active</option><option value="INACTIVE">Inactive</option></select></Filter>
    </div>
    {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : !q.data?.items.length ? <Empty title="No ALCs match" message="Try clearing a filter." /> : <><div className="table-wrap"><table className="table-dense">
      <thead><tr><th>ALC code</th><th>ALC name</th>{showSbuColumn && <><th>SBU</th><th>DCU</th></>}<th>Status</th><th className="text-right">Activities</th><th className="text-right">Pending verification</th><th className="text-right">Partners</th><th>Last activity</th><th>Actions</th></tr></thead>
      <tbody>{q.data.items.map(x => <tr key={x.id}><td><Link to={`/portal/alcs/${x.id}`} className="font-semibold text-navy hover:text-teal">{x.alc_code}</Link></td><td>{x.alc_name}</td>{showSbuColumn && <><td>{x.sbu_code ?? '—'}</td><td className="text-sm text-slate-600">{x.dcu_name ?? '—'}</td></>}<td><Badge status={x.status} /></td><td className="text-right">{formatNumber(x.activities)}</td><td className="text-right">{formatNumber(x.pending)}</td><td className="text-right">{formatNumber(x.partners)}</td><td>{formatDate(x.last_activity ?? undefined)}</td>
        <td><Link to={`/portal/alcs/${x.id}`} className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-semibold text-navy hover:border-teal hover:text-teal"><Eye className="h-3.5 w-3.5" />View</Link></td></tr>)}</tbody>
    </table></div><Pager page={q.data.page} pages={q.data.pages} total={q.data.total} noun="ALCs" onPage={setPage} /></>}
  </>
}
