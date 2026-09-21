import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { Activity, Alc, Partner } from '../types'
import { Badge, ErrorState, formatDate, Loading, MetricCard, PageHeader } from '../components/ui'

export default function SbuAlcDetail() {
  const { id } = useParams()
  const [password, setPassword] = useState(''); const [message, setMessage] = useState(''); const [error, setError] = useState('')
  const q = useQuery({ queryKey: ['sbu-alc-detail', id], queryFn: () => api.get<{ alc: Alc; activities: Activity[]; partners: Partner[] }>(`/portal/alcs/${id}`) })
  async function resetPassword(e: React.FormEvent) {
    e.preventDefault(); setMessage(''); setError('')
    try { await api.post(`/portal/alcs/${id}/reset-password`, { password }); setPassword(''); setMessage('Temporary password set. The ALC must change it at next login.') }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Password reset failed') }
  }
  if (q.isLoading) return <Loading />; if (q.error || !q.data) return <ErrorState error={q.error} />
  const d = q.data
  return <><PageHeader title={d.alc.alc_name} description={`ALC Code ${d.alc.alc_code}`} actions={<Badge status={d.alc.status} />} />
    <div className="grid gap-4 sm:grid-cols-3"><MetricCard label="Activities" value={d.activities.length} /><MetricCard label="Verified" value={d.activities.filter(a => a.status === 'VERIFIED').length} /><MetricCard label="Partners" value={d.partners.length} /></div>
    <section className="panel mt-6 max-w-xl p-5"><h2 className="font-bold text-navy">Reset ALC password</h2><p className="mt-1 text-sm text-slate-500">Set a temporary password for this centre's login. The current password is never shown.</p><form onSubmit={resetPassword} className="mt-4"><label>New temporary password</label><input className="mt-1" type="password" minLength={12} required autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} />{message && <p className="mt-2 text-sm text-emerald-700">{message}</p>}{error && <p className="mt-2 text-sm text-red-700">{error}</p>}<button className="btn-primary mt-3">Reset password</button></form></section>
    <h2 className="mb-3 mt-7 font-bold text-navy">Recent activities</h2><div className="table-wrap"><table><thead><tr><th>Activity</th><th>Type</th><th>Date</th><th>Status</th></tr></thead><tbody>{d.activities.map(a => <tr key={a.id}><td><Link className="font-semibold text-navy hover:text-teal" to={`/portal/activities/${a.id}`}>{a.activity_number}</Link></td><td>{a.activity_type}</td><td>{formatDate(a.activity_date)}</td><td><Badge status={a.status} /></td></tr>)}</tbody></table></div>
  </>
}
