import { useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { DcuOption, Hierarchy, Sbu, User } from '../types'
import { Badge, ConfirmDialog, ErrorState, Loading, MetricCard, PageHeader, Toast } from '../components/ui'

interface AlcRow { id: string; alc_code: string; alc_name: string; status: string }

const dcuLabel = (name?: string | null) => (name ?? '—').replace(/^DCU /, '')

export default function AdminSbuDetail() {
  const { id } = useParams(); const client = useQueryClient()
  const q = useQuery({ queryKey: ['admin-sbu-detail', id], queryFn: () => api.get<{ sbu: Sbu; hierarchy: Hierarchy; alcs: AlcRow[]; users: User[] }>(`/admin/sbus/${id}`) })
  const dcus = useQuery({ queryKey: ['dcus-options'], queryFn: () => api.get<{ items: DcuOption[] }>('/admin/dcus') })
  const [target, setTarget] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null)
  const closeToast = useCallback(() => setToast(null), [])
  if (q.isLoading) return <Loading />; if (q.error || !q.data) return <ErrorState error={q.error} />
  const d = q.data
  const current = d.hierarchy
  // Valid targets: active DCUs other than the current one (the backend re-validates the RCU).
  const targets = (dcus.data?.items ?? []).filter(x => x.is_active && x.id !== d.sbu.dcu_id)
  const chosen = targets.find(x => x.id === target)

  async function move() {
    if (!chosen) return
    setBusy(true)
    try {
      await api.patch(`/admin/sbus/${id}/dcu`, { dcu_id: chosen.id })
      setToast({ message: `${d.sbu.name} moved to ${dcuLabel(chosen.name)}.`, tone: 'success' })
      setTarget('')
      for (const key of [['admin-sbu-detail', id], ['admin-sbus'], ['sbus-options'], ['dcus-options'], ['alcs'], ['alc-detail']]) client.invalidateQueries({ queryKey: key })
    } catch (caught) { setToast({ message: caught instanceof Error ? caught.message : 'Unable to move the SBU', tone: 'error' }) }
    finally { setBusy(false); setConfirming(false) }
  }

  return <><PageHeader title={d.sbu.name} description={`SBU Code ${d.sbu.code}`} actions={<Badge status={d.sbu.is_active ? 'ACTIVE' : 'INACTIVE'} />} />
    <div className="grid gap-4 sm:grid-cols-3"><MetricCard label="Assigned ALCs" value={d.alcs.length} /><MetricCard label="SBU users" value={d.users.length} /><MetricCard label="Status" value={d.sbu.is_active ? 'Active' : 'Inactive'} /></div>
    <section className="panel mt-6 max-w-2xl p-5"><h2 className="font-bold text-navy">DCU assignment</h2><p className="mt-1 text-sm text-slate-500">Move this SBU and all of its ALCs to another DCU. Nothing is copied: the ALCs, their history and the SBU login stay as they are; DCU access follows immediately.</p>
      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2"><div><dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">RCU</dt><dd className="mt-1 font-medium">{current.rcu?.name ?? '—'}</dd></div><div><dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">Current DCU</dt><dd className="mt-1 font-medium">{current.dcu ? dcuLabel(current.dcu.name) : 'Unassigned'}</dd></div></dl>
      <div className="mt-4 flex flex-wrap items-end gap-3"><div className="min-w-64 flex-1"><label htmlFor="move-dcu" className="text-xs font-semibold uppercase tracking-wide text-slate-500">Move to DCU</label><select id="move-dcu" className="mt-1" value={target} disabled={busy} onChange={e => setTarget(e.target.value)}><option value="">{dcus.isLoading ? 'Loading DCUs…' : 'Select an active DCU'}</option>{targets.map(x => <option key={x.id} value={x.id}>{dcuLabel(x.name)} ({x.code})</option>)}</select></div><button type="button" className="btn-primary" disabled={!chosen || busy} onClick={() => setConfirming(true)}>Move SBU</button></div>
      {dcus.error && <p className="mt-2 text-xs text-red-700">Unable to load DCUs: {dcus.error instanceof Error ? dcus.error.message : 'request failed'}</p>}
    </section>
    <h2 className="mb-3 mt-7 font-bold text-navy">Assigned ALCs</h2><div className="table-wrap"><table><thead><tr><th>ALC</th><th>Name</th><th>Status</th></tr></thead><tbody>{d.alcs.length ? d.alcs.map(a => <tr key={a.id}><td><Link className="font-semibold text-navy hover:text-teal" to={`/admin/alcs/${a.id}`}>{a.alc_code}</Link></td><td>{a.alc_name}</td><td><Badge status={a.status} /></td></tr>) : <tr><td colSpan={3} className="p-5 text-sm text-slate-500">No ALCs assigned yet. Assign ALCs from the ALC directory.</td></tr>}</tbody></table></div>
    <h2 className="mb-3 mt-7 font-bold text-navy">SBU users</h2><div className="table-wrap"><table><thead><tr><th>User</th><th>Email</th><th>Status</th></tr></thead><tbody>{d.users.length ? d.users.map(u => <tr key={u.id}><td className="font-semibold">{u.username}</td><td>{u.email ?? '—'}</td><td><Badge status={u.is_active ? 'ACTIVE' : 'INACTIVE'} /></td></tr>) : <tr><td colSpan={3} className="p-5 text-sm text-slate-500">No SBU users yet. Create one from User Management.</td></tr>}</tbody></table></div>
    {confirming && chosen && <ConfirmDialog title="Move SBU" tone="warning" busy={busy} confirmLabel="Move SBU" onCancel={() => setConfirming(false)} onConfirm={move}
      body={<><p>Move {d.sbu.name} from {current.dcu ? dcuLabel(current.dcu.name) : 'no DCU'} to {dcuLabel(chosen.name)}?</p><p className="mt-2 font-semibold text-amber-800">All {d.alcs.length} ALC{d.alcs.length === 1 ? '' : 's'} under this SBU will now belong to {dcuLabel(chosen.name)}.</p><p className="mt-2 text-slate-500">The SBU login and every ALC's history are unchanged.</p></>} />}
    {toast && <Toast message={toast.message} tone={toast.tone} onClose={closeToast} />}
  </>
}
