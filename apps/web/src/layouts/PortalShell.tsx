import { Outlet, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Activity, BarChart3, BookOpen, Building2, CalendarCheck, ClipboardCheck, FileBarChart, Gauge, Network, PlusCircle, Settings, Users } from 'lucide-react'
import { api } from '../lib/api'
import type { User } from '../types'
import TopHeader, { type NavItem } from './TopHeader'

// Header navigation per role. The fourth field is the smallest viewport at which the item sits
// directly in the header; below it the item is listed under "More" (see TopHeader).
// DCU: operational oversight of its own SBUs and ALCs. No ALC authoring, no user management,
// no audit logs and no global DCU administration.
const dcuNav: readonly NavItem[] = [
  ['Dashboard', '/portal', Gauge, 'lg'], ['SBUs', '/portal/sbus', Network, 'lg'], ['ALCs', '/portal/alcs', Building2, 'lg'], ['Partners', '/portal/partners', Users, 'lg'], ['Activities', '/portal/activities', Activity, 'lg'],
  ['Verification', '/portal/verification', ClipboardCheck, 'lg'], ['Reports', '/portal/reports', FileBarChart, 'lg'], ['Profile', '/portal/profile', Settings, 'xl']
]
const sbuNav: readonly NavItem[] = [
  ['Dashboard', '/portal', Gauge, 'lg'], ['ALCs', '/portal/alcs', Building2, 'lg'], ['Partners', '/portal/partners', Users, 'lg'], ['Activities', '/portal/activities', Activity, 'lg'],
  ['Verification', '/portal/verification', ClipboardCheck, 'lg'], ['Reports', '/portal/reports', FileBarChart, 'lg'], ['Profile', '/portal/profile', Settings, 'xl']
]
const alcNav: readonly NavItem[] = [
  ['Dashboard', '/portal', Gauge, 'lg'], ['Partners', '/portal/partners', Users, 'lg'], ['Add Activity', '/portal/activities/new', PlusCircle, 'lg'], ['My Activities', '/portal/activities', Activity, 'lg'],
  ['Tasks', '/portal/tasks', CalendarCheck, 'lg'], ['Growth Challenge', '/portal/challenge', ClipboardCheck, 'lg'], ['Performance', '/portal/performance', BarChart3, 'xl'], ['Growth Resources', '/portal/resources', BookOpen, '2xl'], ['Profile', '/portal/profile', Settings, '2xl']
]

export default function PortalShell({ user }: { user: User }) {
  const navigate = useNavigate(); const client = useQueryClient()
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
    <TopHeader home="/portal" nav={nav} label={isDcu ? 'DCU workspace' : isSupervisor ? 'SBU workspace' : 'ALC workspace'}
      account={{ name: primary, detail: secondary, profile: '/portal/profile' }} onLogout={logout}
      notice={locked ? 'Change your password to unlock the rest of the portal.' : undefined}/>
    <main><div className="mx-auto max-w-[1500px] p-4 sm:p-6 lg:p-8"><Outlet context={user}/></div></main>
  </div>
}
