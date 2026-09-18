import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Activity, BarChart3, BookOpen, Building2, CalendarCheck, ClipboardCheck, FileBarChart, Gauge, LogOut, Menu, PlusCircle, Settings, ShieldCheck, Users, X } from 'lucide-react'
import { api } from '../lib/api'
import type { User } from '../types'

const adminNav = [
  ['Dashboard', '/admin', Gauge], ['Verification Queue', '/admin/verification', ClipboardCheck], ['Activities', '/admin/activities', Activity], ['ALCs', '/admin/alcs', Building2],
  ['Partners', '/admin/partners', Users], ['30-Day Challenge', '/admin/challenge', CalendarCheck], ['Reports', '/admin/reports', FileBarChart], ['Users', '/admin/users', ShieldCheck], ['Audit Logs', '/admin/audit', BookOpen], ['Settings', '/admin/settings', Settings]
] as const
const alcNav = [
  ['Dashboard', '/alc', Gauge], ['Add Activity', '/alc/activities/new', PlusCircle], ['My Activities', '/alc/activities', Activity], ['Partners', '/alc/partners', Users],
  ['Tasks', '/alc/tasks', CalendarCheck], ['30-Day Challenge', '/alc/challenge', ClipboardCheck], ['Performance', '/alc/performance', BarChart3], ['Growth Resources', '/alc/resources', BookOpen], ['Profile', '/alc/profile', Settings]
] as const

export default function Shell({ user }: { user: User }) {
  const [open, setOpen] = useState(false); const navigate = useNavigate(); const client = useQueryClient()
  const nav = user.role === 'ADMIN' ? adminNav : alcNav
  async function logout() { await api.post('/auth/logout'); client.clear(); navigate('/login') }
  return <div className="min-h-screen bg-canvas">
    <header className="fixed inset-x-0 top-0 z-40 flex h-16 items-center border-b bg-white px-4 lg:hidden"><button onClick={() => setOpen(true)} aria-label="Open menu"><Menu/></button><div className="ml-3"><b className="text-navy">ALC Growth Portal</b><p className="text-xs text-slate-500">{user.alc?.alc_name ?? 'Administration'}</p></div></header>
    {open && <div className="fixed inset-0 z-40 bg-black/40 lg:hidden" onClick={() => setOpen(false)}/>} 
    <aside className={`fixed inset-y-0 left-0 z-50 flex w-64 flex-col bg-navy text-white transition-transform lg:translate-x-0 ${open ? 'translate-x-0' : '-translate-x-full'}`}>
      <div className="flex h-20 items-center justify-between border-b border-white/10 px-5"><div><p className="text-lg font-bold">ALC Growth Portal</p><p className="text-xs text-slate-300">Growth & collaboration</p></div><button onClick={() => setOpen(false)} className="lg:hidden"><X/></button></div>
      <nav className="flex-1 space-y-1 overflow-y-auto p-3">{nav.map(([label, to, Icon]) => <NavLink key={to} to={to} end={to === '/admin' || to === '/alc'} onClick={() => setOpen(false)} className={({ isActive }) => `flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium ${isActive ? 'bg-white text-navy' : 'text-slate-200 hover:bg-white/10'}`}><Icon className="h-4 w-4"/>{label}</NavLink>)}</nav>
      <div className="border-t border-white/10 p-4"><div className="mb-3 flex items-center gap-3"><div className="flex h-9 w-9 items-center justify-center rounded-full bg-teal font-bold">{(user.alc?.alc_name ?? user.username)[0]}</div><div className="min-w-0"><p className="truncate text-sm font-semibold">{user.alc?.alc_name ?? user.username}</p><p className="truncate text-xs text-slate-300">{user.alc?.alc_code ?? 'Administrator'}</p></div></div><button className="flex w-full items-center gap-2 text-sm text-slate-200 hover:text-white" onClick={logout}><LogOut className="h-4 w-4"/>Sign out</button></div>
    </aside>
    <main className="min-h-screen pt-20 lg:ml-64 lg:pt-0"><div className="mx-auto max-w-[1500px] p-4 sm:p-6 lg:p-8"><Outlet/></div></main>
  </div>
}
