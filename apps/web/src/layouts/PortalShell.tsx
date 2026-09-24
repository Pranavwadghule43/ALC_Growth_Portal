import { useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Activity, BarChart3, BookOpen, Building2, CalendarCheck, ClipboardCheck, FileBarChart, Gauge, LogOut, Menu, Network, PlusCircle, Settings, Users, X } from 'lucide-react'
import { api } from '../lib/api'
import type { User } from '../types'

// DCU: operational oversight of its own SBUs and ALCs. No ALC authoring, no user management,
// no audit logs and no global DCU administration.
const dcuNav = [
  ['Dashboard', '/portal', Gauge], ['SBUs', '/portal/sbus', Network], ['ALCs', '/portal/alcs', Building2], ['Activities', '/portal/activities', Activity],
  ['Verification', '/portal/verification', ClipboardCheck], ['Partners', '/portal/partners', Users], ['Reports', '/portal/reports', FileBarChart], ['Profile', '/portal/profile', Settings]
] as const
const sbuNav = [
  ['Dashboard', '/portal', Gauge], ['ALCs', '/portal/alcs', Building2], ['Activities', '/portal/activities', Activity],
  ['Verification', '/portal/verification', ClipboardCheck], ['Partners', '/portal/partners', Users], ['Reports', '/portal/reports', FileBarChart], ['Profile', '/portal/profile', Settings]
] as const
const alcNav = [
  ['Dashboard', '/portal', Gauge], ['Add Activity', '/portal/activities/new', PlusCircle], ['My Activities', '/portal/activities', Activity], ['Partners', '/portal/partners', Users],
  ['Tasks', '/portal/tasks', CalendarCheck], ['30-Day Challenge', '/portal/challenge', ClipboardCheck], ['Performance', '/portal/performance', BarChart3], ['Growth Resources', '/portal/resources', BookOpen], ['Profile', '/portal/profile', Settings]
] as const

// Sidebar active-state matcher. Kept explicit so sibling routes that share a
// prefix don't both highlight: /portal/activities/new must not also activate
// "My Activities" (/portal/activities). Dashboard (/portal) stays exact-match.
function isNavActive(href: string, pathname: string): boolean {
  if (href === '/portal') return pathname === '/portal'
  if (href === '/portal/activities/new') return pathname === '/portal/activities/new'
  if (href === '/portal/activities') {
    return (
      pathname === '/portal/activities' ||
      (pathname.startsWith('/portal/activities/') && pathname !== '/portal/activities/new')
    )
  }
  return pathname === href || pathname.startsWith(`${href}/`)
}

export default function PortalShell({ user }: { user: User }) {
  const [open, setOpen] = useState(false); const navigate = useNavigate(); const client = useQueryClient(); const { pathname } = useLocation()
  const isDcu = user.role === 'DCU'
  const isSupervisor = isDcu || user.role === 'SBU'
  const fullNav = isDcu ? dcuNav : isSupervisor ? sbuNav : alcNav
  // While a password change is required, only Profile is reachable (the route guard enforces it too).
  const locked = user.must_change_password
  const nav = locked ? fullNav.filter(([, to]) => to === '/portal/profile') : fullNav
  const primary = isDcu ? (user.dcu?.name ?? user.username) : isSupervisor ? (user.sbu?.name ?? user.username) : (user.alc?.alc_name ?? user.username)
  const secondary = isDcu ? (user.dcu?.code ?? 'DCU') : isSupervisor ? (user.sbu?.code ?? 'SBU') : (user.alc?.alc_code ?? 'ALC')
  async function logout() { await api.post('/auth/logout'); client.clear(); navigate('/login') }
  return <div className="min-h-screen bg-canvas">
    <header className="fixed inset-x-0 top-0 z-40 flex h-16 items-center border-b bg-white px-4 lg:hidden"><button onClick={() => setOpen(true)} aria-label="Open menu"><Menu/></button><div className="ml-3"><b className="text-navy">ALC Growth Portal</b><p className="text-xs text-slate-500">{primary}</p></div></header>
    {open && <div className="fixed inset-0 z-40 bg-black/40 lg:hidden" onClick={() => setOpen(false)}/>}
    <aside className={`fixed inset-y-0 left-0 z-50 flex w-64 flex-col bg-navy text-white transition-transform lg:translate-x-0 ${open ? 'translate-x-0' : '-translate-x-full'}`}>
      <div className="flex h-20 items-center justify-between border-b border-white/10 px-5"><div><p className="text-lg font-bold">ALC Growth Portal</p><p className="text-xs text-slate-300">{isDcu ? 'DCU workspace' : isSupervisor ? 'SBU workspace' : 'Growth & collaboration'}</p></div><button onClick={() => setOpen(false)} className="lg:hidden"><X/></button></div>
      <nav className="flex-1 space-y-1 overflow-y-auto p-3">{locked && <p className="mb-2 rounded-md bg-amber-400/15 px-3 py-2 text-xs text-amber-100">Change your password to unlock the rest of the portal.</p>}{nav.map(([label, to, Icon]) => <NavLink key={to} to={to} onClick={() => setOpen(false)} className={`flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium ${isNavActive(to, pathname) ? 'bg-white text-navy' : 'text-slate-200 hover:bg-white/10'}`}><Icon className="h-4 w-4"/>{label}</NavLink>)}</nav>
      <div className="border-t border-white/10 p-4"><div className="mb-3 flex items-center gap-3"><div className="flex h-9 w-9 items-center justify-center rounded-full bg-teal font-bold">{primary[0]?.toUpperCase()}</div><div className="min-w-0"><p className="truncate text-sm font-semibold">{primary}</p><p className="truncate text-xs text-slate-300">{secondary}</p></div></div><button className="flex w-full items-center gap-2 text-sm text-slate-200 hover:text-white" onClick={logout}><LogOut className="h-4 w-4"/>Sign out</button></div>
    </aside>
    <main className="min-h-screen pt-20 lg:ml-64 lg:pt-0"><div className="mx-auto max-w-[1500px] p-4 sm:p-6 lg:p-8"><Outlet context={user}/></div></main>
  </div>
}
