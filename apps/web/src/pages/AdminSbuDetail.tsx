import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { Sbu, User } from '../types'
import { Badge, ErrorState, Loading, MetricCard, PageHeader } from '../components/ui'

interface AlcRow { id: string; alc_code: string; alc_name: string; status: string }

export default function AdminSbuDetail() {
  const { id } = useParams()
  const q = useQuery({ queryKey: ['admin-sbu-detail', id], queryFn: () => api.get<{ sbu: Sbu; alcs: AlcRow[]; users: User[] }>(`/admin/sbus/${id}`) })
  if (q.isLoading) return <Loading />; if (q.error || !q.data) return <ErrorState error={q.error} />
  const d = q.data
  return <><PageHeader title={d.sbu.name} description={`SBU Code ${d.sbu.code}`} actions={<Badge status={d.sbu.is_active ? 'ACTIVE' : 'INACTIVE'} />} />
    <div className="grid gap-4 sm:grid-cols-3"><MetricCard label="Assigned ALCs" value={d.alcs.length} /><MetricCard label="SBU users" value={d.users.length} /><MetricCard label="Status" value={d.sbu.is_active ? 'Active' : 'Inactive'} /></div>
    <h2 className="mb-3 mt-7 font-bold text-navy">Assigned ALCs</h2><div className="table-wrap"><table><thead><tr><th>ALC</th><th>Name</th><th>Status</th></tr></thead><tbody>{d.alcs.length ? d.alcs.map(a => <tr key={a.id}><td><Link className="font-semibold text-navy hover:text-teal" to={`/admin/alcs/${a.id}`}>{a.alc_code}</Link></td><td>{a.alc_name}</td><td><Badge status={a.status} /></td></tr>) : <tr><td colSpan={3} className="p-5 text-sm text-slate-500">No ALCs assigned yet. Assign ALCs from the ALC directory.</td></tr>}</tbody></table></div>
    <h2 className="mb-3 mt-7 font-bold text-navy">SBU users</h2><div className="table-wrap"><table><thead><tr><th>User</th><th>Email</th><th>Status</th></tr></thead><tbody>{d.users.length ? d.users.map(u => <tr key={u.id}><td className="font-semibold">{u.username}</td><td>{u.email ?? '—'}</td><td><Badge status={u.is_active ? 'ACTIVE' : 'INACTIVE'} /></td></tr>) : <tr><td colSpan={3} className="p-5 text-sm text-slate-500">No SBU users yet. Create one from User Management.</td></tr>}</tbody></table></div>
  </>
}
