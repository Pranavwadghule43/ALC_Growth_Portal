import { lazy, Suspense, ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useOutletContext } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from './lib/api'
import type { Role, User } from './types'
import AdminShell from './layouts/AdminShell'
import PortalShell from './layouts/PortalShell'
import { Loading } from './components/ui'

const Login = lazy(() => import('./pages/Login'))
const AdminLogin = lazy(() => import('./pages/AdminLogin'))
// Operational portal (SBU + ALC)
const AlcDashboard = lazy(() => import('./pages/AlcDashboard'))
const SbuDashboard = lazy(() => import('./pages/SbuDashboard'))
const Activities = lazy(() => import('./pages/Activities'))
const SbuActivities = lazy(() => import('./pages/SbuActivities'))
const ActivityEditor = lazy(() => import('./pages/ActivityEditor'))
const SbuReviewActivity = lazy(() => import('./pages/SbuReviewActivity'))
const Partners = lazy(() => import('./pages/Partners'))
const SbuPartners = lazy(() => import('./pages/SbuPartners'))
const SbuAlcs = lazy(() => import('./pages/SbuAlcs'))
const SbuAlcDetail = lazy(() => import('./pages/SbuAlcDetail'))
const Tasks = lazy(() => import('./pages/Tasks'))
const Challenge = lazy(() => import('./pages/Challenge'))
const Resources = lazy(() => import('./pages/Resources'))
const Performance = lazy(() => import('./pages/Performance'))
const PortalReports = lazy(() => import('./pages/PortalReports'))
const Profile = lazy(() => import('./pages/Profile'))
// Super Admin portal
const AdminDashboard = lazy(() => import('./pages/AdminDashboard'))
const AdminActivities = lazy(() => import('./pages/AdminActivities'))
const ReviewActivity = lazy(() => import('./pages/ReviewActivity'))
const AdminDirectory = lazy(() => import('./pages/AdminDirectory'))
const AlcDetail = lazy(() => import('./pages/AlcDetail'))
const AdminSbus = lazy(() => import('./pages/AdminSbus'))
const AdminSbuDetail = lazy(() => import('./pages/AdminSbuDetail'))
const AdminPartners = lazy(() => import('./pages/AdminPartners'))
const AdminChallenge = lazy(() => import('./pages/AdminChallenge'))
const AdminUsers = lazy(() => import('./pages/AdminUsers'))
const AdminAudit = lazy(() => import('./pages/AdminAudit'))
const Reports = lazy(() => import('./pages/Reports'))
const Settings = lazy(() => import('./pages/Settings'))

function useMe() {
  return useQuery({ queryKey: ['me'], queryFn: () => api.get<User>('/auth/me'), retry: false })
}

function landing(role: Role) {
  return role === 'ADMIN' ? '/admin' : '/portal'
}

function AdminGuard() {
  const location = useLocation(); const { data, isLoading } = useMe()
  if (isLoading) return <div className="p-8"><Loading label="Checking your session" /></div>
  if (!data) return <Navigate to="/admin/login" replace state={{ from: location }} />
  if (data.role !== 'ADMIN') return <Navigate to={landing(data.role)} replace />
  if (data.must_change_password && !location.pathname.endsWith('/profile')) return <Navigate to="/admin/profile" replace />
  return <AdminShell user={data} />
}

function PortalGuard() {
  const location = useLocation(); const { data, isLoading } = useMe()
  if (isLoading) return <div className="p-8"><Loading label="Checking your session" /></div>
  if (!data) return <Navigate to="/login" replace state={{ from: location }} />
  if (data.role === 'ADMIN') return <Navigate to="/admin" replace />
  if (data.must_change_password && !location.pathname.endsWith('/profile')) return <Navigate to="/portal/profile" replace />
  return <PortalShell user={data} />
}

// DCU and SBU both supervise many ALCs and share the supervisor views; the backend bounds
// each to its own hierarchy scope.
const SUPERVISORS: Role[] = ['DCU', 'SBU']
const isSupervisor = (role: Role) => SUPERVISORS.includes(role)

// Restrict a portal route to the given operational roles; redirect the rest to /portal.
function RoleOnly({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const user = useOutletContext<User>()
  return roles.includes(user.role) ? <>{children}</> : <Navigate to="/portal" replace />
}
function PortalHome() { return isSupervisor(useOutletContext<User>().role) ? <SbuDashboard /> : <AlcDashboard /> }
function PortalActivities() { return isSupervisor(useOutletContext<User>().role) ? <SbuActivities /> : <Activities /> }
function PortalActivityDetail() { return isSupervisor(useOutletContext<User>().role) ? <SbuReviewActivity /> : <ActivityEditor /> }
function PortalPartners() { return isSupervisor(useOutletContext<User>().role) ? <SbuPartners /> : <Partners /> }

export default function App() {
  return <Suspense fallback={<div className="p-8"><Loading label="Loading workspace" /></div>}><Routes>
    <Route path="/login" element={<Login />} />
    <Route path="/admin/login" element={<AdminLogin />} />

    <Route element={<PortalGuard />}>
      <Route path="/portal" element={<PortalHome />} />
      <Route path="/portal/activities" element={<PortalActivities />} />
      <Route path="/portal/activities/new" element={<RoleOnly roles={['ALC']}><ActivityEditor /></RoleOnly>} />
      <Route path="/portal/activities/:id" element={<PortalActivityDetail />} />
      <Route path="/portal/partners" element={<PortalPartners />} />
      <Route path="/portal/reports" element={<PortalReports />} />
      <Route path="/portal/profile" element={<Profile />} />
      <Route path="/portal/alcs" element={<RoleOnly roles={SUPERVISORS}><SbuAlcs /></RoleOnly>} />
      <Route path="/portal/alcs/:id" element={<RoleOnly roles={SUPERVISORS}><SbuAlcDetail /></RoleOnly>} />
      <Route path="/portal/verification" element={<RoleOnly roles={SUPERVISORS}><SbuActivities queueOnly /></RoleOnly>} />
      <Route path="/portal/tasks" element={<RoleOnly roles={['ALC']}><Tasks /></RoleOnly>} />
      <Route path="/portal/challenge" element={<RoleOnly roles={['ALC']}><Challenge /></RoleOnly>} />
      <Route path="/portal/performance" element={<RoleOnly roles={['ALC']}><Performance /></RoleOnly>} />
      <Route path="/portal/resources" element={<RoleOnly roles={['ALC']}><Resources /></RoleOnly>} />
    </Route>

    <Route element={<AdminGuard />}>
      <Route path="/admin" element={<AdminDashboard />} />
      <Route path="/admin/verification" element={<AdminActivities />} />
      <Route path="/admin/activities" element={<AdminActivities />} />
      <Route path="/admin/activities/:id" element={<ReviewActivity />} />
      <Route path="/admin/alcs" element={<AdminDirectory />} />
      <Route path="/admin/alcs/:id" element={<AlcDetail />} />
      <Route path="/admin/sbus" element={<AdminSbus />} />
      <Route path="/admin/sbus/:id" element={<AdminSbuDetail />} />
      <Route path="/admin/partners" element={<AdminPartners />} />
      <Route path="/admin/challenge" element={<AdminChallenge />} />
      <Route path="/admin/reports" element={<Reports />} />
      <Route path="/admin/users" element={<AdminUsers />} />
      <Route path="/admin/audit" element={<AdminAudit />} />
      <Route path="/admin/settings" element={<Settings />} />
      <Route path="/admin/profile" element={<Profile />} />
    </Route>

    <Route path="*" element={<Navigate to="/login" replace />} />
  </Routes></Suspense>
}
