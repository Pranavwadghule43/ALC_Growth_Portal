import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Eye } from 'lucide-react'
import { api } from '../lib/api'
import type { SbuRow } from '../types'
import { Badge, Empty, ErrorState, formatNumber, Loading, PageHeader } from '../components/ui'

// SBUs under the signed-in DCU (read-only: SBU edits and reassignment are admin-only).
export default function DcuSbus() {
  const { data, isLoading, error } = useQuery({ queryKey: ['dcu-sbus'], queryFn: () => api.get<{ items: SbuRow[]; total: number }>('/portal/sbus') })
  return <><PageHeader title="SBUs" description="Strategic business units in your DCU, with their ALCs, review workload and partners." />
    {isLoading ? <Loading /> : error ? <ErrorState error={error} /> : !data?.items.length ? <Empty title="No SBUs" message="SBUs assigned to your DCU will appear here." /> : <div className="table-wrap"><table className="table-dense">
      <thead><tr><th>SBU code</th><th>SBU name</th><th>DCU</th><th>Status</th><th className="text-right">Total ALCs</th><th className="text-right">Active ALCs</th><th className="text-right">Pending verification</th><th className="text-right">Verified activities</th><th className="text-right">Partners</th><th>Actions</th></tr></thead>
      <tbody>{data.items.map(s => <tr key={s.id}><td><Link to={`/portal/sbus/${s.id}`} className="font-semibold text-navy hover:text-teal">{s.code}</Link></td><td>{s.name}</td><td>{s.dcu?.name ?? '—'}</td><td><Badge status={s.is_active ? 'ACTIVE' : 'INACTIVE'} /></td><td className="text-right">{formatNumber(s.alcs)}</td><td className="text-right">{formatNumber(s.active_alcs)}</td><td className="text-right">{formatNumber(s.pending)}</td><td className="text-right">{formatNumber(s.verified)}</td><td className="text-right">{formatNumber(s.partners)}</td>
        <td><Link to={`/portal/sbus/${s.id}`} className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-semibold text-navy hover:border-teal hover:text-teal"><Eye className="h-3.5 w-3.5" />View SBU</Link></td></tr>)}</tbody>
      <tfoot><tr className="border-t bg-slate-50 font-semibold"><td className="px-4 py-3" colSpan={4}>{data.total} SBUs</td><td className="px-4 py-3 text-right">{formatNumber(data.items.reduce((n, s) => n + s.alcs, 0))}</td><td className="px-4 py-3 text-right">{formatNumber(data.items.reduce((n, s) => n + s.active_alcs, 0))}</td><td className="px-4 py-3 text-right">{formatNumber(data.items.reduce((n, s) => n + s.pending, 0))}</td><td className="px-4 py-3 text-right">{formatNumber(data.items.reduce((n, s) => n + s.verified, 0))}</td><td className="px-4 py-3 text-right">{formatNumber(data.items.reduce((n, s) => n + s.partners, 0))}</td><td /></tr></tfoot>
    </table></div>}
  </>
}
