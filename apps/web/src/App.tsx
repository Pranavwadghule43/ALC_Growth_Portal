import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from './lib/api'
import type { Role, User } from './types'
import Shell from './components/Shell'
import { Loading } from './components/ui'
const Login = lazy(() => import('./pages/Login'))
const AlcDashboard = lazy(() => import('./pages/AlcDashboard'))
const AdminDashboard = lazy(() => import('./pages/AdminDashboard'))
const Activities = lazy(() => import('./pages/Activities'))
const ActivityEditor = lazy(() => import('./pages/ActivityEditor'))
const AdminActivities = lazy(() => import('./pages/AdminActivities'))
const ReviewActivity = lazy(() => import('./pages/ReviewActivity'))
const Partners = lazy(() => import('./pages/Partners'))
const Tasks = lazy(() => import('./pages/Tasks'))
const Challenge = lazy(() => import('./pages/Challenge'))
const Resources = lazy(() => import('./pages/Resources'))
const AdminDirectory = lazy(() => import('./pages/AdminDirectory'))
const AlcDetail = lazy(() => import('./pages/AlcDetail'))
const AdminPartners = lazy(() => import('./pages/AdminPartners'))
const AdminChallenge = lazy(() => import('./pages/AdminChallenge'))
const AdminUsers = lazy(() => import('./pages/AdminUsers'))
const AdminAudit = lazy(() => import('./pages/AdminAudit'))
const Reports = lazy(() => import('./pages/Reports'))
const Profile = lazy(() => import('./pages/Profile'))
const Settings = lazy(() => import('./pages/Settings'))
const Performance = lazy(() => import('./pages/Performance'))

function Protected({ role }: { role: Role }) {
  const location=useLocation();const {data,isLoading}=useQuery({queryKey:['me'],queryFn:()=>api.get<User>('/auth/me'),retry:false})
  if(isLoading)return <div className="p-8"><Loading label="Checking your session"/></div>
  if(!data)return <Navigate to="/login" replace state={{from:location}}/>
  if(data.role!==role)return <Navigate to={data.role==='ADMIN'?'/admin':'/alc'} replace/>
  if(data.must_change_password && !location.pathname.endsWith('/profile')) return <Navigate to={data.role==='ADMIN'?'/admin/profile':'/alc/profile'} replace/>
  return <Shell user={data}/>
}

export default function App(){return <Suspense fallback={<div className="p-8"><Loading label="Loading workspace"/></div>}><Routes>
  <Route path="/login" element={<Login/>}/>
  <Route element={<Protected role="ALC"/>}><Route path="/alc" element={<AlcDashboard/>}/><Route path="/alc/activities" element={<Activities/>}/><Route path="/alc/activities/new" element={<ActivityEditor/>}/><Route path="/alc/activities/:id" element={<ActivityEditor/>}/><Route path="/alc/partners" element={<Partners/>}/><Route path="/alc/tasks" element={<Tasks/>}/><Route path="/alc/challenge" element={<Challenge/>}/><Route path="/alc/performance" element={<Performance/>}/><Route path="/alc/resources" element={<Resources/>}/><Route path="/alc/profile" element={<Profile/>}/></Route>
  <Route element={<Protected role="ADMIN"/>}><Route path="/admin" element={<AdminDashboard/>}/><Route path="/admin/verification" element={<AdminActivities/>}/><Route path="/admin/activities" element={<AdminActivities/>}/><Route path="/admin/activities/:id" element={<ReviewActivity/>}/><Route path="/admin/alcs" element={<AdminDirectory/>}/><Route path="/admin/alcs/:id" element={<AlcDetail/>}/><Route path="/admin/partners" element={<AdminPartners/>}/><Route path="/admin/challenge" element={<AdminChallenge/>}/><Route path="/admin/reports" element={<Reports/>}/><Route path="/admin/users" element={<AdminUsers/>}/><Route path="/admin/audit" element={<AdminAudit/>}/><Route path="/admin/settings" element={<Settings/>}/><Route path="/admin/profile" element={<Profile/>}/></Route>
  <Route path="*" element={<Navigate to="/login" replace/>}/>
  </Routes></Suspense>}
