import { Outlet, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Activity, BookOpen, Building2, CalendarCheck, ClipboardCheck, FileBarChart, Gauge, Network, Settings, ShieldCheck, Users } from 'lucide-react'
import { api } from '../lib/api'
import type { User } from '../types'
import TopHeader, { type NavItem } from './TopHeader'

// The fourth field is the smallest viewport at which the item sits directly in the header;
// 'more' items (lower-frequency administration) always live under "More" (see TopHeader).
const adminNav: readonly NavItem[] = [
  ['Dashboard', '/admin', Gauge, 'lg'], ['Partners', '/admin/partners', Users, 'lg'], ['Verification Queue', '/admin/verification', ClipboardCheck, 'lg'], ['Activities', '/admin/activities', Activity, 'lg'], ['ALCs', '/admin/alcs', Building2, 'lg'],
  ['SBUs', '/admin/sbus', Network, 'lg'], ['Growth Challenge', '/admin/challenge', CalendarCheck, 'xl'], ['Reports', '/admin/reports', FileBarChart, '2xl'], ['Users', '/admin/users', ShieldCheck, 'more'], ['Audit Logs', '/admin/audit', BookOpen, 'more'], ['Settings', '/admin/settings', Settings, 'more']
]

export default function AdminShell({ user }: { user: User }) {
  const navigate = useNavigate(); const client = useQueryClient()
  const locked = user.must_change_password
  const nav = locked ? [] : adminNav
  async function logout() { await api.post('/auth/logout'); client.clear(); navigate('/admin/login') }
  return <div className="min-h-screen bg-canvas">
    <TopHeader home="/admin" nav={nav} label="Super Admin console" account={{ name: user.username, detail: 'Super Admin' }} onLogout={logout}
      notice={locked ? 'Change your password on this page to unlock the admin console.' : undefined}/>
    <main><div className="mx-auto max-w-[1500px] p-4 sm:p-6 lg:p-8"><Outlet context={user}/></div></main>
  </div>
}
