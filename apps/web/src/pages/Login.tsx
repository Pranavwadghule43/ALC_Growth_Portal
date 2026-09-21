import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Building2, Eye, EyeOff } from 'lucide-react'
import { api } from '../lib/api'
import type { User } from '../types'
import { FieldError } from '../components/ui'

const schema = z.object({ identifier: z.string().min(2, 'Enter your username or ALC code'), password: z.string().min(8, 'Enter your password') })
type FormValues = z.infer<typeof schema>
export default function Login() {
  const [error, setError] = useState(''); const [showPassword, setShowPassword] = useState(false); const navigate = useNavigate(); const client = useQueryClient()
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<FormValues>({ resolver: zodResolver(schema) })
  async function submit(values: FormValues) { setError(''); try { const user = await api.post<User>('/auth/login', values); client.setQueryData(['me'], user); navigate(user.role === 'ADMIN' ? '/admin' : '/portal') } catch (e) { setError(e instanceof Error ? e.message : 'Unable to login') } }
  return <main className="grid min-h-screen lg:grid-cols-[1.05fr_.95fr]">
    <section className="hidden bg-navy p-12 text-white lg:flex lg:flex-col lg:justify-between"><div className="flex items-center gap-3"><div className="rounded-lg bg-white/10 p-2"><Building2/></div><div><p className="text-lg font-bold">ALC Growth Portal</p><p className="text-xs text-slate-300">Authorized Learning Centres</p></div></div><div className="max-w-xl"><p className="mb-4 text-sm font-semibold uppercase tracking-[.2em] text-teal-200">Evidence-led growth</p><h1 className="text-5xl font-bold leading-tight">Turn local collaboration into verified impact.</h1><p className="mt-6 text-lg leading-8 text-slate-300">Record activities, submit supporting evidence, resolve reviews, and track only the outcomes that have been verified.</p></div><p className="text-xs text-slate-400">Secure institutional workspace · Role-based access · Auditable reviews</p></section>
    <section className="flex items-center justify-center p-5 sm:p-10"><div className="w-full max-w-md"><div className="mb-8 lg:hidden"><p className="text-xl font-bold text-navy">ALC Growth Portal</p><p className="text-sm text-slate-500">Authorized Learning Centres</p></div><h2 className="text-3xl font-bold text-navy">Welcome back</h2><p className="mt-2 text-sm text-slate-600">Sign in securely to your workspace.</p>
      <form className="mt-7 space-y-5" onSubmit={handleSubmit(submit)}><div><label htmlFor="identifier">Username / ALC Code</label><input id="identifier" className="mt-1.5" autoComplete="username" placeholder="Enter your username or ALC code" {...register('identifier')}/><FieldError message={errors.identifier?.message}/></div><div><label htmlFor="password">Password</label><div className="relative mt-1.5"><input id="password" className="pr-11" type={showPassword ? 'text' : 'password'} autoComplete="current-password" placeholder="Enter your password" {...register('password')}/><button type="button" onClick={() => setShowPassword((v) => !v)} aria-label={showPassword ? 'Hide password' : 'Show password'} className="absolute inset-y-0 right-0 flex items-center px-3 text-slate-500 hover:text-navy">{showPassword ? <EyeOff className="h-4 w-4"/> : <Eye className="h-4 w-4"/>}</button></div><FieldError message={errors.password?.message}/></div>{error && <p role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}<button className="btn-primary w-full py-3" disabled={isSubmitting}>{isSubmitting ? 'Signing in…' : 'Sign In'}</button></form>
      <p className="mt-6 text-center text-xs text-slate-500">Contact your portal administrator if you cannot access your account.</p></div></section>
  </main>
}
