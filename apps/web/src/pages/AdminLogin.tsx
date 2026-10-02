import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Eye, EyeOff, ShieldCheck } from 'lucide-react'
import { api } from '../lib/api'
import type { User } from '../types'
import { FieldError } from '../components/ui'

const schema = z.object({ identifier: z.string().min(2, 'Enter your username or email'), password: z.string().min(8, 'Enter your password') })
type FormValues = z.infer<typeof schema>
export default function AdminLogin() {
  const [error, setError] = useState(''); const [showPassword, setShowPassword] = useState(false); const navigate = useNavigate(); const client = useQueryClient()
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<FormValues>({ resolver: zodResolver(schema) })
  async function submit(values: FormValues) { setError(''); try { const user = await api.post<User>('/auth/admin-login', values); client.setQueryData(['me'], user); navigate('/admin') } catch (e) { setError(e instanceof Error ? e.message : 'Unable to login') } }
  return <main className="grid min-h-screen bg-canvas lg:grid-cols-[1.05fr_.95fr]">
    <section className="hidden border-r-4 border-brand bg-navy p-12 text-white lg:flex lg:flex-col lg:justify-between"><div className="flex items-center gap-4"><img src={`${import.meta.env.BASE_URL}branding/rcu-pune-logo.png`} alt="RCU Pune — Regional Coordination unit, MKCL" width={989} height={592} className="h-16 w-auto rounded-md"/><div><p className="text-lg font-bold">ALC Growth Portal</p><p className="text-xs text-brand-light">Super Admin</p></div></div><div className="max-w-xl"><span className="mb-5 block h-1 w-12 rounded-full bg-brand-red" aria-hidden="true"/><p className="mb-4 text-sm font-semibold uppercase tracking-[.2em] text-brand-light">Administration</p><h1 className="text-5xl font-bold leading-tight">Oversee every centre with confidence.</h1><p className="mt-6 text-lg leading-8 text-white/80">Manage users, review evidence, and keep the portal running with auditable, role-based controls.</p></div><p className="text-xs text-brand-light/80">Restricted access · Administrators only · Auditable actions</p></section>
    <section className="flex items-center justify-center p-5 sm:p-10"><div className="w-full max-w-md rounded-xl border border-t-4 border-line border-t-brand bg-white p-6 shadow-panel sm:p-8"><div className="mb-8 flex items-center gap-3 lg:hidden"><img src={`${import.meta.env.BASE_URL}branding/rcu-pune-logo.png`} alt="RCU Pune — Regional Coordination unit, MKCL" width={989} height={592} className="h-12 w-auto rounded-md"/><div><p className="text-xl font-bold text-navy">ALC Growth Portal</p><p className="text-sm text-slate-500">Super Admin</p></div></div><div className="flex items-center gap-3"><span className="rounded-lg bg-tint p-2 text-brand" aria-hidden="true"><ShieldCheck className="h-5 w-5"/></span><h2 className="text-3xl font-bold text-navy">Admin sign in</h2></div><p className="mt-2 text-sm text-slate-600">Administrator access only. Portal users sign in at the main login page.</p>
      <form className="mt-7 space-y-5" onSubmit={handleSubmit(submit)}><div><label htmlFor="identifier">Username or Email</label><input id="identifier" className="mt-1.5" autoComplete="username" placeholder="Enter your username or email" {...register('identifier')}/><FieldError message={errors.identifier?.message}/></div><div><label htmlFor="password">Password</label><div className="relative mt-1.5"><input id="password" className="pr-11" type={showPassword ? 'text' : 'password'} autoComplete="current-password" placeholder="Enter your password" {...register('password')}/><button type="button" onClick={() => setShowPassword((v) => !v)} aria-label={showPassword ? 'Hide password' : 'Show password'} className="absolute inset-y-0 right-0 flex items-center px-3 text-slate-500 hover:text-navy">{showPassword ? <EyeOff className="h-4 w-4"/> : <Eye className="h-4 w-4"/>}</button></div><FieldError message={errors.password?.message}/></div>{error && <p role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}<button className="btn-primary w-full py-3" disabled={isSubmitting}>{isSubmitting ? 'Signing in…' : 'Login'}</button></form>
      <p className="mt-6 text-center text-xs text-slate-500">Contact your system administrator if you cannot access your account.</p></div></section>
  </main>
}
