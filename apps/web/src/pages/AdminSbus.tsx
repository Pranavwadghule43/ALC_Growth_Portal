import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { Page } from '../types'
import { Badge, ErrorState, Loading, PageHeader } from '../components/ui'

interface SbuRow { id: string; code: string; name: string; is_active: boolean; assigned_alcs: number }
const empty = { code: '', name: '' }

export default function AdminSbus() {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState(empty)
  const [error, setError] = useState('')
  const q = useQuery({ queryKey: ['admin-sbus'], queryFn: () => api.get<Page<SbuRow>>('/admin/sbus?page=1&page_size=100') })
  async function save(event: React.FormEvent) {
    event.preventDefault(); setError('')
    try { await api.post('/admin/sbus', form); setForm(empty); setOpen(false); client.invalidateQueries({ queryKey: ['admin-sbus'] }) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to create SBU') }
  }
  async function toggle(sbu: SbuRow) {
    if (!confirm(`${sbu.is_active ? 'Deactivate' : 'Activate'} ${sbu.code}?`)) return
    await api.patch(`/admin/sbus/${sbu.id}`, { is_active: !sbu.is_active })
    client.invalidateQueries({ queryKey: ['admin-sbus'] })
  }
  return <>
    <PageHeader title="SBU Management" description="Create SBUs, manage their status, and see how many ALCs each one covers." actions={<button className="btn-primary" onClick={() => setOpen(!open)}>{open ? 'Close' : 'Create SBU'}</button>} />
    {open && <form onSubmit={save} className="panel mb-5 grid gap-4 p-5 md:grid-cols-2">
      <div><label>SBU code</label><input className="mt-1" required placeholder="e.g. SBU 4" value={form.code} onChange={e => setForm({ ...form, code: e.target.value })} /></div>
      <div><label>SBU name</label><input className="mt-1" required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></div>
      {error && <p className="text-sm text-red-700 md:col-span-2">{error}</p>}
      <div className="md:col-span-2"><button className="btn-primary">Create SBU</button></div>
    </form>}
    {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : <div className="table-wrap"><table><thead><tr><th>SBU</th><th>Assigned ALCs</th><th>Status</th><th>Actions</th></tr></thead><tbody>{q.data?.items.map(sbu => <tr key={sbu.id}><td><Link to={`/admin/sbus/${sbu.id}`} className="font-semibold text-navy hover:text-teal">{sbu.code}</Link><p className="text-xs text-slate-500">{sbu.name}</p></td><td>{sbu.assigned_alcs}</td><td><Badge status={sbu.is_active ? 'ACTIVE' : 'INACTIVE'} /></td><td><button className="btn-secondary" onClick={() => toggle(sbu)}>{sbu.is_active ? 'Deactivate' : 'Activate'}</button></td></tr>)}</tbody></table></div>}
  </>
}
