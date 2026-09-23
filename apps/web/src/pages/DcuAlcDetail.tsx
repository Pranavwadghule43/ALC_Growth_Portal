import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { ChevronLeft } from 'lucide-react'
import { api } from '../lib/api'
import type { Activity, ActivityMetrics, DirectoryPartner, UnitRef } from '../types'
import { Badge, Empty, ErrorState, formatDate, formatNumber, Loading, MetricCard, PageHeader, RowActions, Toast } from '../components/ui'
import { formatDateTime, resubmittedAt } from '../components/review'

interface AlcDetail {
  alc: { id: string; alc_code: string; alc_name: string; status: string; sbu: UnitRef | null; dcu: UnitRef | null }
  summary: ActivityMetrics & { partners: number; active_partners: number }
  activities: Activity[]; correction_required: Activity[]; partners: DirectoryPartner[]
}

function ActivityTable({ rows }: { rows: Activity[] }) {
  return <div className="table-wrap"><table className="table-dense"><thead><tr><th>Activity</th><th>Status</th><th>Submitted</th><th className="text-right">Evidence</th><th className="text-right">Learners</th><th className="text-right">Leads</th><th className="text-right">Admissions</th><th>Actions</th></tr></thead>
    <tbody>{rows.map(a => <tr key={a.id}><td><Link className="font-semibold text-navy hover:text-teal" to={`/portal/activities/${a.id}`}>{a.activity_number}</Link><p className="text-xs text-slate-500">{a.activity_type} · {formatDate(a.activity_date)}</p></td><td><Badge status={a.status} /></td><td className="text-sm">{formatDateTime(a.submitted_at)}{resubmittedAt(a) && <p className="text-xs text-violet-700">Resubmitted {formatDate(resubmittedAt(a))}</p>}</td><td className="text-right">{a.evidence.length}</td><td className="text-right">{formatNumber(a.learners_reached)}</td><td className="text-right">{formatNumber(a.leads_generated)}</td><td className="text-right">{formatNumber(a.admissions_generated)}</td><td><RowActions base="/portal/activities" id={a.id} status={a.status} /></td></tr>)}</tbody></table></div>
}

// Read-only ALC view for a DCU. The ALC master record is administered by Admin only; a DCU
// may still reset the centre's login password, as SBU supervisors can.
export default function DcuAlcDetail() {
  const { id } = useParams()
  const [password, setPassword] = useState(''); const [toast, setToast] = useState(''); const [error, setError] = useState('')
  const q = useQuery({ queryKey: ['dcu-alc', id], queryFn: () => api.get<AlcDetail>(`/portal/alcs/${id}`) })
  async function resetPassword(e: React.FormEvent) {
    e.preventDefault(); setError('')
    try { await api.post(`/portal/alcs/${id}/reset-password`, { password }); setPassword(''); setToast('Temporary password set. The ALC must change it at next login.') }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Password reset failed') }
  }
  if (q.isLoading) return <Loading />
  if (q.error || !q.data) return <ErrorState error={q.error} />
  const { alc, summary: s, activities, correction_required: corrections, partners } = q.data
  return <>
    <Link to={alc.sbu ? `/portal/sbus/${alc.sbu.id}` : '/portal/alcs'} className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-slate-500 hover:text-teal"><ChevronLeft className="h-4 w-4" />{alc.sbu ? `Back to ${alc.sbu.code}` : 'All ALCs'}</Link>
    <PageHeader title={alc.alc_name} description={`ALC ${alc.alc_code}`} actions={<Badge status={alc.status} />} />
    <section className="panel mb-6 grid gap-4 p-5 md:grid-cols-5">{[['ALC code', alc.alc_code], ['ALC name', alc.alc_name], ['DCU', alc.dcu?.name ?? '—'], ['SBU', alc.sbu ? `${alc.sbu.code} · ${alc.sbu.name}` : 'Unassigned'], ['Status', alc.status === 'ACTIVE' ? 'Active' : 'Inactive']].map(([k, v]) => <div key={k}><p className="text-xs font-semibold uppercase text-slate-500">{k}</p><p className="mt-1 font-medium">{v}</p></div>)}</section>
    <div className="grid gap-4 md:grid-cols-4 xl:grid-cols-8">
      <MetricCard label="Activities" value={formatNumber(s.activities)} hint="Submitted workflow" />
      <MetricCard label="Pending verification" value={formatNumber(s.pending)} />
      <MetricCard label="Correction required" value={formatNumber(s.corrections)} />
      <MetricCard label="Verified" value={formatNumber(s.verified)} />
      <MetricCard label="Rejected" value={formatNumber(s.rejected)} />
      <MetricCard label="Verified learners" value={formatNumber(s.learners)} />
      <MetricCard label="Verified leads / admissions" value={`${formatNumber(s.leads)} / ${formatNumber(s.admissions)}`} />
      <MetricCard label="Partners" value={formatNumber(s.partners)} hint={`${formatNumber(s.active_partners)} active`} />
    </div>
    {corrections.length > 0 && <><h2 className="mb-3 mt-7 font-bold text-navy">Correction required ({corrections.length})</h2><ActivityTable rows={corrections} /></>}
    <h2 className="mb-3 mt-7 font-bold text-navy">Recent activities</h2>
    {activities.length ? <ActivityTable rows={activities} /> : <Empty title="No submitted activities" message="Activities appear here once the ALC submits them. Drafts stay private to the ALC." />}
    <h2 className="mb-3 mt-7 font-bold text-navy">Partners</h2>
    {partners.length ? <div className="table-wrap"><table><thead><tr><th>Partner</th><th>Collaboration type</th><th>Ecosystem</th><th>Contact</th><th className="text-right">Activities</th><th>Last activity</th><th>Status</th></tr></thead><tbody>{partners.map(p => <tr key={p.id}><td className="font-semibold">{p.partner_name}</td><td>{p.partner_type}</td><td>{p.ecosystem}</td><td>{p.contact_person ?? '—'}<p className="text-xs text-slate-500">{p.phone ?? p.email}</p></td><td className="text-right">{p.activity_count}</td><td>{formatDate(p.last_activity ?? undefined)}</td><td><Badge status={p.status} /></td></tr>)}</tbody></table></div> : <Empty title="No partners" message="This centre has not added any partners." />}
    <section className="panel mt-7 max-w-xl p-5"><h2 className="font-bold text-navy">Reset ALC login password</h2><p className="mt-1 text-sm text-slate-500">Set a temporary password for this centre's login. The current password is never shown.</p><form onSubmit={resetPassword} className="mt-4"><label htmlFor="alc-password">New temporary password</label><input id="alc-password" className="mt-1" type="password" minLength={12} required autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} />{error && <p className="mt-2 text-sm text-red-700">{error}</p>}<button className="btn-primary mt-3">Reset password</button></form></section>
    {toast && <Toast message={toast} onClose={() => setToast('')} />}
  </>
}
