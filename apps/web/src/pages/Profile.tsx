import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { ShieldAlert } from 'lucide-react'
import { api } from '../lib/api'
import type { User } from '../types'
import { PageHeader } from '../components/ui'

export default function Profile() {
  const user = useOutletContext<User>(); const client = useQueryClient(); const navigate = useNavigate()
  const forced = user.must_change_password
  const [form, setForm] = useState({ current_password: '', new_password: '', confirm_password: '' })
  const [message, setMessage] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  async function save(e: React.FormEvent) {
    e.preventDefault(); setMessage(''); setError('')
    if (form.new_password !== form.confirm_password) { setError('The new passwords do not match.'); return }
    if (form.new_password === form.current_password) { setError('The new password must be different from the current password.'); return }
    setBusy(true)
    try {
      await api.post('/auth/change-password', { current_password: form.current_password, new_password: form.new_password })
      setForm({ current_password: '', new_password: '', confirm_password: '' })
      await client.invalidateQueries({ queryKey: ['me'] })
      if (forced) { navigate(user.role === 'ADMIN' ? '/admin' : '/portal', { replace: true }); return }
      setMessage('Password changed. Your other signed-in browsers have been signed out.')
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to change password') }
    finally { setBusy(false) }
  }
  return <><PageHeader title="Profile & Security" description="Keep your account credentials secure." />
    <div className="max-w-xl space-y-5">
      <section className="panel p-5"><h2 className="font-bold text-navy">Account</h2><dl className="mt-3 grid grid-cols-[8rem_1fr] gap-y-2 text-sm"><dt className="text-slate-500">Signed in as</dt><dd className="break-words font-medium">{user.username}</dd><dt className="text-slate-500">Role</dt><dd className="font-medium">{user.role}</dd>{user.alc && <><dt className="text-slate-500">Centre</dt><dd className="break-words font-medium">{user.alc.alc_code} · {user.alc.alc_name}</dd></>}{user.sbu && <><dt className="text-slate-500">SBU</dt><dd className="break-words font-medium">{user.sbu.code} · {user.sbu.name}</dd></>}{user.dcu && <><dt className="text-slate-500">DCU</dt><dd className="break-words font-medium">{user.dcu.name}</dd></>}</dl></section>
      {forced && <div role="alert" className="flex gap-3 rounded-lg border border-amber-300 bg-amber-50 p-4"><ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" /><div><p className="font-semibold text-amber-900">Please set a new password</p><p className="mt-1 text-sm text-amber-800">Your password was reset or is temporary. Enter the temporary password you were given as the current password, then choose your own new one. The rest of the portal unlocks once it is changed.</p></div></div>}
      <form className="panel space-y-4 p-6" onSubmit={save}>
        <h2 className="font-bold text-navy">Change password</h2>
        <div><label htmlFor="current_password">{forced ? 'Current (temporary) password' : 'Current password'}</label><input id="current_password" className="mt-1" type="password" required autoComplete="current-password" value={form.current_password} onChange={e => setForm({ ...form, current_password: e.target.value })} /></div>
        <div><label htmlFor="new_password">New password</label><input id="new_password" className="mt-1" type="password" required minLength={12} autoComplete="new-password" value={form.new_password} onChange={e => setForm({ ...form, new_password: e.target.value })} /><p className="mt-1 text-xs text-slate-500">At least 12 characters, different from your current password.</p></div>
        <div><label htmlFor="confirm_password">Confirm new password</label><input id="confirm_password" className="mt-1" type="password" required minLength={12} autoComplete="new-password" value={form.confirm_password} onChange={e => setForm({ ...form, confirm_password: e.target.value })} />{form.confirm_password && form.confirm_password !== form.new_password && <p className="mt-1 text-xs text-red-700">Does not match the new password yet.</p>}</div>
        {message && <p role="status" className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">{message}</p>}
        {error && <p role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
        <button className="btn-primary" disabled={busy}>{busy ? 'Changing…' : 'Change password'}</button>
      </form>
    </div>
  </>
}