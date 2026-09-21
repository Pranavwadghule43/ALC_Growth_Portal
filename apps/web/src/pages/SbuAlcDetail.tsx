import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { Activity, Alc, Partner } from '../types'
import { Badge, Empty, ErrorState, formatDate, Loading, MetricCard, PageHeader } from '../components/ui'

const PENDING = ['SUBMITTED', 'RESUBMITTED', 'UNDER_REVIEW']
const ATTENTION = ['CORRECTION_REQUIRED', 'REJECTED']

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
  const verified = d.activities.filter(a => a.status === 'VERIFIED').length
  const pending = d.activities.filter(a => PENDING.includes(a.status)).length
  const attention = d.activities.filter(a => ATTENTION.includes(a.status)).length
  return <><PageHeader title={d.alc.alc_name} description={`ALC Code ${d.alc.alc_code}`} actions={<Badge status={d.alc.status} />} />
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
      <MetricCard label="Activities" value={d.activities.length} />
      <MetricCard label="Verified" value={verified} />
      <MetricCard label="Pending review" value={pending} hint="Submitted, resubmitted or under review" />
      <MetricCard label="Needs attention" value={attention} hint="Correction required or rejected" />
      <MetricCard label="Partners" value={d.partners.length} />
    </div>
    <section className="panel mt-6 max-w-xl p-5"><h2 className="font-bold text-navy">Reset ALC password</h2><p className="mt-1 text-sm text-slate-500">Set a temporary password for this centre's login. The current password is never shown.</p><form onSubmit={resetPassword} className="mt-4"><label>New temporary password</label><input className="mt-1" type="password" minLength={12} required autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} />{message && <p className="mt-2 text-sm text-emerald-700">{message}</p>}{error && <p className="mt-2 text-sm text-red-700">{error}</p>}<button className="btn-primary mt-3">Reset password</button></form></section>
    <h2 className="mb-3 mt-7 font-bold text-navy">Recent activities</h2>
    {d.activities.length ? <div className="table-wrap"><table><thead><tr><th>Activity</th><th>Type</th><th>Date</th><th>Status</th></tr></thead><tbody>{d.activities.map(a => <tr key={a.id}><td><Link className="font-semibold text-navy hover:text-teal" to={`/portal/activities/${a.id}`}>{a.activity_number}</Link></td><td>{a.activity_type}</td><td>{formatDate(a.activity_date)}</td><td><Badge status={a.status} /></td></tr>)}</tbody></table></div> : <Empty title="No activities yet" message="This centre has not submitted any activities." />}
    <h2 className="mb-3 mt-7 font-bold text-navy">Partners</h2>
    {d.partners.length ? <div className="table-wrap"><table><thead><tr><th>Partner</th><th>Type</th><th>Ecosystem</th><th>Contact</th><th>Status</th></tr></thead><tbody>{d.partners.map(p => <tr key={p.id}><td className="font-semibold">{p.partner_name}</td><td>{p.partner_type}</td><td>{p.ecosystem}</td><td>{p.contact_person ?? '—'}<p className="text-xs text-slate-500">{p.phone ?? p.email}</p></td><td><Badge status={p.status} /></td></tr>)}</tbody></table></div> : <Empty title="No partners" message="This centre has not added any partners." />}
  </>
}