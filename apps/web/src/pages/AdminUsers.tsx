import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Search, X } from 'lucide-react'
import { api } from '../lib/api'
import { toQuery } from '../lib/constants'
import type { Alc, Dcu, Page, Sbu, User } from '../types'
import { Badge, Empty, ErrorState, Filter, Loading, PageHeader, Pager } from '../components/ui'

const SEARCH_DEBOUNCE_MS = 350

const empty = { username: '', email: '', role: 'ALC', alc_id: '', sbu_id: '', dcu_id: '', password: '', must_change_password: true }

export default function AdminUsers() {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState(empty)
  const [alcSearch, setAlcSearch] = useState('')
  const [resetUser, setResetUser] = useState<User | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [deleteUser, setDeleteUser] = useState<User | null>(null)
  const [deleteError, setDeleteError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')
  // Server-side search, filters and pagination: only the current page is ever fetched.
  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState({ role: '', status: '' })
  const [page, setPage] = useState(1)
  useEffect(() => {
    const next = searchText.trim()
    if (next === search) return
    const timer = setTimeout(() => { setSearch(next); setPage(1) }, SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [searchText, search])
  const updateFilters = (patch: Partial<typeof filters>) => { setFilters(f => ({ ...f, ...patch })); setPage(1) }
  const clearSearch = () => { setSearchText(''); setSearch(''); setPage(1) }
  const userQuery = toQuery({ page, page_size: 25, q: search, role: filters.role, status: filters.status })
  const users = useQuery({ queryKey: ['users', userQuery], queryFn: () => api.get<Page<User>>(`/admin/users?${userQuery}`), placeholderData: prev => prev })
  // After a delete/deactivate the current page can end up past the last page: step back.
  const lastPage = users.data?.pages
  useEffect(() => { if (lastPage !== undefined && page > 1 && page > lastPage) setPage(Math.max(lastPage, 1)) }, [page, lastPage])
  const alcs = useQuery({ queryKey: ['alcs-options', alcSearch], queryFn: () => api.get<Page<Alc>>(`/admin/alcs?page=1&page_size=100&search=${encodeURIComponent(alcSearch)}`) })
  const sbus = useQuery({ queryKey: ['sbus-options'], queryFn: () => api.get<Page<Sbu>>('/admin/sbus?page=1&page_size=100') })
  const dcus = useQuery({ queryKey: ['dcus-options'], queryFn: () => api.get<{ items: Dcu[] }>('/admin/dcus') })

  async function save(event: React.FormEvent) {
    event.preventDefault(); setError('')
    try {
      // Send exactly the one assignment key the role requires (ALC → alc_id, SBU → sbu_id,
      // DCU → dcu_id; ADMIN → none). The backend re-validates every combination.
      const assignment = form.role === 'ALC' ? { alc_id: form.alc_id } : form.role === 'SBU' ? { sbu_id: form.sbu_id } : form.role === 'DCU' ? { dcu_id: form.dcu_id } : {}
      await api.post('/admin/users', { username: form.username, email: form.email || null, role: form.role, password: form.password, must_change_password: form.must_change_password, ...assignment })
      setOpen(false); setForm(empty); client.invalidateQueries({ queryKey: ['users'] })
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to create user') }
  }
  async function toggle(user: User) {
    if (!confirm(`${user.is_active ? 'Deactivate' : 'Activate'} ${user.username}?`)) return
    await api.patch(`/admin/users/${user.id}`, { is_active: !user.is_active })
    client.invalidateQueries({ queryKey: ['users'] })
  }
  async function applyReset(event: React.FormEvent) {
    event.preventDefault(); if (!resetUser) return; setError('')
    try {
      await api.patch(`/admin/users/${resetUser.id}`, { password: resetPassword, must_change_password: true })
      setResetUser(null); setResetPassword(''); client.invalidateQueries({ queryKey: ['users'] })
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Password reset failed') }
  }
  async function confirmDelete() {
    if (!deleteUser) return; setDeleteError(''); setDeleting(true)
    try {
      await api.delete(`/admin/users/${deleteUser.id}`)
      setDeleteUser(null); client.invalidateQueries({ queryKey: ['users'] })
    } catch (caught) { setDeleteError(caught instanceof Error ? caught.message : 'Unable to delete account') }
    finally { setDeleting(false) }
  }
  return <>
    <PageHeader title="User Management" description="Create accounts, reset passwords, and control access." actions={<button className="btn-primary" onClick={() => setOpen(!open)}>{open ? 'Close' : 'Create user'}</button>} />
    {open && <form onSubmit={save} className="panel mb-5 grid gap-4 p-5 md:grid-cols-2">
      <div><label>Username</label><input className="mt-1" required value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></div>
      <div><label>Email (optional)</label><input className="mt-1" type="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></div>
      <div><label>Role</label><select className="mt-1" value={form.role} onChange={e => setForm({ ...form, role: e.target.value })}><option>ALC</option><option>SBU</option><option>DCU</option><option>ADMIN</option></select></div>
      {form.role === 'ALC' && <div><label>ALC</label><input className="mb-2 mt-1" placeholder="Search code or centre name" value={alcSearch} onChange={e => setAlcSearch(e.target.value)} /><select required value={form.alc_id} onChange={e => setForm({ ...form, alc_id: e.target.value })}><option value="">Select ALC</option>{alcs.data?.items.map(a => <option key={a.id} value={a.id}>{a.alc_code} · {a.alc_name}</option>)}</select></div>}
      {form.role === 'SBU' && <div><label>SBU</label><select className="mt-1" required value={form.sbu_id} onChange={e => setForm({ ...form, sbu_id: e.target.value })}><option value="">Select SBU</option>{sbus.data?.items.map(s => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}</select></div>}
      {form.role === 'DCU' && <div><label htmlFor="user-dcu">DCU</label><select id="user-dcu" className="mt-1" required value={form.dcu_id} onChange={e => setForm({ ...form, dcu_id: e.target.value })}><option value="">{dcus.isLoading ? 'Loading DCUs…' : 'Select DCU'}</option>{dcus.data?.items.map(d => <option key={d.id} value={d.id}>{d.name.replace(/^DCU /, '')} ({d.code})</option>)}</select>{dcus.error && <p className="mt-1 text-xs text-red-700">Unable to load DCUs: {dcus.error instanceof Error ? dcus.error.message : 'request failed'}</p>}{dcus.data && !dcus.data.items.length && <p className="mt-1 text-xs text-slate-500">No DCUs exist yet. Run the hierarchy migration / seed.</p>}</div>}
      <div><label>Temporary password</label><input className="mt-1" type="password" minLength={12} required value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></div>
      {error && <p className="text-sm text-red-700 md:col-span-2">{error}</p>}
      <div className="md:col-span-2"><button className="btn-primary">Create user</button></div>
    </form>}
    {resetUser && <form onSubmit={applyReset} className="panel mb-5 max-w-xl p-5"><h2 className="font-bold text-navy">Reset password for {resetUser.username}</h2><p className="mt-1 text-sm text-slate-500">The user will be required to change this temporary password at next login.</p><label className="mt-4 block">New temporary password</label><input className="mt-1" type="password" autoComplete="new-password" required minLength={12} value={resetPassword} onChange={e => setResetPassword(e.target.value)} />{error && <p className="mt-2 text-sm text-red-700">{error}</p>}<div className="mt-4 flex gap-2"><button className="btn-primary">Reset password</button><button type="button" className="btn-secondary" onClick={() => { setResetUser(null); setResetPassword('') }}>Cancel</button></div></form>}
    <div className="panel mb-4 grid gap-3 p-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
      <Filter label="Search users"><div className="relative"><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" aria-hidden="true" /><input type="text" className="pl-9 pr-9" aria-label="Search users" placeholder="Search by name, username, email, ALC code, SBU or DCU..." value={searchText} onChange={e => setSearchText(e.target.value)} />{searchText && <button type="button" className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 hover:text-navy" aria-label="Clear search" onClick={clearSearch}><X className="h-4 w-4" /></button>}</div></Filter>
      <Filter label="Role"><select value={filters.role} onChange={e => updateFilters({ role: e.target.value })}><option value="">All roles</option><option value="ADMIN">Super Admin</option><option value="DCU">DCU</option><option value="SBU">SBU</option><option value="ALC">ALC</option></select></Filter>
      <Filter label="Status"><select value={filters.status} onChange={e => updateFilters({ status: e.target.value })}><option value="">Active and inactive</option><option value="active">Active</option><option value="inactive">Inactive</option></select></Filter>
    </div>
    {users.isLoading ? <Loading /> : users.error ? <ErrorState error={users.error} /> : !users.data?.items.length ? <Empty title={search ? `No users found for "${search}"` : 'No users match the selected filters'} message={search ? 'Try a different name, username, email, ALC code, SBU or DCU.' : 'Try clearing a filter.'} /> : <><div className={`table-wrap transition-opacity ${users.isFetching ? 'opacity-60' : ''}`} aria-busy={users.isFetching}><table><thead><tr><th>User</th><th>Role</th><th>Assignment</th><th>Status</th><th>Password</th><th>Actions</th></tr></thead><tbody>{users.data.items.map(user => <tr key={user.id}><td><b>{user.username}</b><p className="text-xs text-slate-500">{user.email}</p></td><td>{user.role === 'ADMIN' ? 'Super Admin' : user.role}</td><td>{user.alc ? `${user.alc.alc_code} · ${user.alc.alc_name}` : user.sbu ? `${user.sbu.code} · ${user.sbu.name}` : user.dcu ? `${user.dcu.code} · ${user.dcu.name}` : '—'}</td><td><Badge status={user.is_active ? 'ACTIVE' : 'INACTIVE'} /></td><td>{user.must_change_password ? 'Change required' : 'Current'}</td><td><div className="flex flex-wrap gap-2"><button className="btn-secondary" onClick={() => toggle(user)}>{user.is_active ? 'Deactivate' : 'Activate'}</button><button className="btn-secondary" onClick={() => { setResetUser(user); setError('') }}>Reset password</button>{user.role !== 'ADMIN' && <button className="btn-danger" onClick={() => { setDeleteUser(user); setDeleteError('') }}>Delete Account</button>}</div></td></tr>)}</tbody></table></div><Pager page={users.data.page} pages={users.data.pages} total={users.data.total} noun="users" onPage={setPage} />{users.isFetching && <p className="mt-2 text-xs text-slate-500" role="status">Updating results…</p>}</>}
    {deleteUser && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-labelledby="delete-title">
      <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
        <h2 id="delete-title" className="text-lg font-bold text-navy">Delete User Account</h2>
        <p className="mt-1 text-sm text-slate-500">{deleteUser.username}{deleteUser.alc ? ` · ${deleteUser.alc.alc_code} · ${deleteUser.alc.alc_name}` : deleteUser.sbu ? ` · ${deleteUser.sbu.code}` : deleteUser.dcu ? ` · ${deleteUser.dcu.code}` : ''}</p>
        <p className="mt-4 text-sm text-slate-700">This will permanently delete this login account. The associated ALC/SBU master record and all historical activities, partners, evidence and reports will remain unchanged.</p>
        {deleteError && <p className="mt-3 text-sm text-red-700">{deleteError}</p>}
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" className="btn-secondary" disabled={deleting} onClick={() => setDeleteUser(null)}>Cancel</button>
          <button type="button" className="btn-danger" disabled={deleting} onClick={confirmDelete}>{deleting ? 'Deleting…' : 'Delete Account'}</button>
        </div>
      </div>
    </div>}
  </>
}
