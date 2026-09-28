import { useCallback, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { Activity, Alc, DcuOption, Hierarchy, Page, Partner, SbuOption } from '../types'
import { Badge, ConfirmDialog, ErrorState, formatDate, Loading, MetricCard, PageHeader, RowActions, Toast } from '../components/ui'

const dcuLabel = (name?: string | null) => (name ?? '—').replace(/^DCU /, '')

export default function AlcDetail() {
  const { id } = useParams(); const client = useQueryClient()
  const q = useQuery({ queryKey: ['alc-detail', id], queryFn: () => api.get<{ alc: Alc; hierarchy: Hierarchy; activities: Activity[]; partners: Partner[] }>(`/admin/alcs/${id}`) })
  const sbus = useQuery({ queryKey: ['sbus-options'], queryFn: () => api.get<Page<SbuOption>>('/admin/sbus?page=1&page_size=100') })
  const dcus = useQuery({ queryKey: ['dcus-options'], queryFn: () => api.get<{ items: DcuOption[] }>('/admin/dcus') })
  const [target, setTarget] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null)
  const closeToast = useCallback(() => setToast(null), [])
  async function toggle() { if (!q.data || !confirm(`Change account status for ${q.data.alc.alc_name}?`)) return; await api.patch(`/admin/alcs/${id}`, { status: q.data.alc.status === 'ACTIVE' ? 'INACTIVE' : 'ACTIVE' }); client.invalidateQueries({ queryKey: ['alc-detail', id] }); client.invalidateQueries({ queryKey: ['alcs'] }) }

  // Valid targets: active SBUs placed under an active DCU (the backend re-validates the chain).
  const dcuById = useMemo(() => new Map((dcus.data?.items ?? []).map(d => [d.id, d])), [dcus.data])
  const targets = useMemo(() => (sbus.data?.items ?? []).filter(s => s.is_active && s.dcu_id && dcuById.get(s.dcu_id)?.is_active && s.id !== q.data?.alc.sbu_id), [sbus.data, dcuById, q.data])
  const groups = useMemo(() => {
    const byDcu = new Map<string, SbuOption[]>()
    for (const s of targets) byDcu.set(s.dcu_id as string, [...(byDcu.get(s.dcu_id as string) ?? []), s])
    return [...byDcu.entries()].map(([dcuId, items]) => ({ dcu: dcuById.get(dcuId), items })).sort((a, b) => (a.dcu?.name ?? '').localeCompare(b.dcu?.name ?? ''))
  }, [targets, dcuById])
  const chosen = targets.find(s => s.id === target)
  const chosenDcu = chosen?.dcu_id ? dcuById.get(chosen.dcu_id) : undefined

  async function move() {
    if (!q.data || !chosen) return
    setBusy(true)
    try {
      await api.patch(`/admin/alcs/${id}/sbu`, { sbu_id: chosen.id })
      setToast({ message: `ALC ${q.data.alc.alc_code} moved to ${chosen.name}.`, tone: 'success' })
      setTarget('')
      for (const key of [['alc-detail', id], ['alcs'], ['admin-sbus'], ['admin-sbu-detail'], ['sbus-options'], ['dcus-options']]) client.invalidateQueries({ queryKey: key })
    } catch (caught) { setToast({ message: caught instanceof Error ? caught.message : 'Unable to move the ALC', tone: 'error' }) }
    finally { setBusy(false); setConfirming(false) }
  }

  if (q.isLoading) return <Loading />; if (q.error || !q.data) return <ErrorState error={q.error} />
  const d = q.data
  const current = d.hierarchy
  const crossDcu = chosen && current.dcu?.id !== chosen.dcu_id
  return <><PageHeader title={d.alc.alc_name} description={`ALC Code ${d.alc.alc_code}`} actions={<div className="flex items-center gap-3"><Badge status={d.alc.status} /><button className="btn-secondary" onClick={toggle}>{d.alc.status === 'ACTIVE' ? 'Deactivate ALC' : 'Activate ALC'}</button></div>} />
    <div className="grid gap-4 sm:grid-cols-3"><MetricCard label="Activities" value={d.activities.length} /><MetricCard label="Verified" value={d.activities.filter(a => a.status === 'VERIFIED').length} /><MetricCard label="Partners" value={d.partners.length} /></div>
    <section className="panel mt-6 max-w-2xl p-5"><h2 className="font-bold text-navy">SBU assignment</h2><p className="mt-1 text-sm text-slate-500">Move this centre to another SBU. Its activities, evidence, reviews, partners and login stay with it; access follows the new hierarchy immediately.</p>
      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3"><div><dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">RCU</dt><dd className="mt-1 font-medium">{current.rcu?.name ?? '—'}</dd></div><div><dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">DCU</dt><dd className="mt-1 font-medium">{dcuLabel(current.dcu?.name)}</dd></div><div><dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">Current SBU</dt><dd className="mt-1 font-medium">{current.sbu ? `${current.sbu.name} (${current.sbu.code})` : 'Unassigned'}</dd></div></dl>
      <div className="mt-4 flex flex-wrap items-end gap-3"><div className="min-w-64 flex-1"><label htmlFor="move-sbu" className="text-xs font-semibold uppercase tracking-wide text-slate-500">Move to SBU</label><select id="move-sbu" className="mt-1" value={target} disabled={busy} onChange={e => setTarget(e.target.value)}><option value="">{sbus.isLoading || dcus.isLoading ? 'Loading SBUs…' : 'Select an active SBU'}</option>{groups.map(g => <optgroup key={g.dcu?.id} label={dcuLabel(g.dcu?.name)}>{g.items.map(s => <option key={s.id} value={s.id}>{s.name} ({s.code})</option>)}</optgroup>)}</select></div><button type="button" className="btn-primary" disabled={!chosen || busy} onClick={() => setConfirming(true)}>Move ALC</button></div>
      {crossDcu && <p className="mt-2 text-xs text-amber-700">This moves the ALC to a different DCU ({dcuLabel(chosenDcu?.name)}).</p>}
    </section>
    <h2 className="mb-3 mt-7 font-bold text-navy">Recent activities</h2><div className="table-wrap"><table><thead><tr><th>Activity</th><th>Type</th><th>Date</th><th>Status</th><th>Actions</th></tr></thead><tbody>{d.activities.map(a => <tr key={a.id}><td><Link className="font-semibold text-navy hover:text-teal" to={`/admin/activities/${a.id}`}>{a.activity_number}</Link></td><td>{a.activity_type}</td><td>{formatDate(a.activity_date)}</td><td><Badge status={a.status} /></td><td><RowActions base="/admin/activities" id={a.id} status={a.status} /></td></tr>)}</tbody></table></div>
    {confirming && chosen && <ConfirmDialog title="Move ALC" tone="warning" busy={busy} confirmLabel="Move ALC" onCancel={() => setConfirming(false)} onConfirm={move}
      body={<><p>Move ALC {d.alc.alc_code} from {current.sbu?.name ?? 'Unassigned'} to {chosen.name}?</p>{crossDcu && <p className="mt-2 font-semibold text-amber-800">This will move the ALC from {dcuLabel(current.dcu?.name)} to {dcuLabel(chosenDcu?.name)}.</p>}<p className="mt-2 text-slate-500">History, login and password are unchanged.</p></>} />}
    {toast && <Toast message={toast.message} tone={toast.tone} onClose={closeToast} />}
  </>
}
