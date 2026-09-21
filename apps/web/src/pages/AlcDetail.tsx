import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { Activity, Alc, Page, Partner, Sbu } from '../types'
import { Badge, ErrorState, formatDate, Loading, MetricCard, PageHeader } from '../components/ui'

export default function AlcDetail() {
  const { id } = useParams(); const client = useQueryClient()
  const q = useQuery({ queryKey: ['alc-detail', id], queryFn: () => api.get<{ alc: Alc; activities: Activity[]; partners: Partner[] }>(`/admin/alcs/${id}`) })
  const sbus = useQuery({ queryKey: ['sbus-options'], queryFn: () => api.get<Page<Sbu>>('/admin/sbus?page=1&page_size=100') })
  async function toggle() { if (!q.data || !confirm(`Change account status for ${q.data.alc.alc_name}?`)) return; await api.patch(`/admin/alcs/${id}`, { status: q.data.alc.status === 'ACTIVE' ? 'INACTIVE' : 'ACTIVE' }); client.invalidateQueries({ queryKey: ['alc-detail', id] }); client.invalidateQueries({ queryKey: ['alcs'] }) }
  async function assignSbu(sbu_id: string) { await api.patch(`/admin/alcs/${id}`, { sbu_id: sbu_id || null }); client.invalidateQueries({ queryKey: ['alc-detail', id] }); client.invalidateQueries({ queryKey: ['alcs'] }) }
  if (q.isLoading) return <Loading />; if (q.error || !q.data) return <ErrorState error={q.error} />
  const d = q.data
  return <><PageHeader title={d.alc.alc_name} description={`ALC Code ${d.alc.alc_code}`} actions={<div className="flex items-center gap-3"><Badge status={d.alc.status} /><button className="btn-secondary" onClick={toggle}>{d.alc.status === 'ACTIVE' ? 'Deactivate ALC' : 'Activate ALC'}</button></div>} />
    <div className="grid gap-4 sm:grid-cols-3"><MetricCard label="Activities" value={d.activities.length} /><MetricCard label="Verified" value={d.activities.filter(a => a.status === 'VERIFIED').length} /><MetricCard label="Partners" value={d.partners.length} /></div>
    <section className="panel mt-6 max-w-md p-5"><h2 className="font-bold text-navy">SBU assignment</h2><p className="mt-1 text-sm text-slate-500">Assign this centre to the SBU responsible for its reviews.</p><select className="mt-3" value={d.alc.sbu_id ?? ''} onChange={e => assignSbu(e.target.value)}><option value="">Unassigned</option>{sbus.data?.items.map(s => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}</select></section>
    <h2 className="mb-3 mt-7 font-bold text-navy">Recent activities</h2><div className="table-wrap"><table><thead><tr><th>Activity</th><th>Type</th><th>Date</th><th>Status</th></tr></thead><tbody>{d.activities.map(a => <tr key={a.id}><td><Link className="font-semibold text-navy hover:text-teal" to={`/admin/activities/${a.id}`}>{a.activity_number}</Link></td><td>{a.activity_type}</td><td>{formatDate(a.activity_date)}</td><td><Badge status={a.status} /></td></tr>)}</tbody></table></div>
  </>
}
