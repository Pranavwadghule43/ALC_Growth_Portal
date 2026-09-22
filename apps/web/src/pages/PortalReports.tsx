import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { Download } from 'lucide-react'
import { api } from '../lib/api'
import type { Page, User } from '../types'
import { PageHeader } from '../components/ui'

interface AlcRow { id: string; alc_code: string; alc_name: string }

export default function PortalReports() {
  const user = useOutletContext<User>()
  const isSbu = user.role === 'SBU'
  const [filters, setFilters] = useState({ date_from: '', date_to: '', status: '', activity_type: '', alc_id: '' })
  const alcs = useQuery({ queryKey: ['report-alcs'], queryFn: () => api.get<Page<AlcRow>>('/portal/alcs?page=1&page_size=100'), enabled: isSbu })
  const params = new URLSearchParams(Object.entries(filters).filter(([, v]) => v))
  return <><PageHeader title="Reports" description={isSbu ? 'Export verified and operational data for your assigned ALCs.' : 'Export your centre’s activity history.'} />
    <section className="panel p-6"><h2 className="font-bold text-navy">Activity report</h2><p className="mt-1 text-sm text-slate-500">Scoped automatically to the data you are authorized to see.</p>
      <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <div><label>From</label><input className="mt-1" type="date" value={filters.date_from} onChange={e => setFilters({ ...filters, date_from: e.target.value })} /></div>
        <div><label>To</label><input className="mt-1" type="date" value={filters.date_to} onChange={e => setFilters({ ...filters, date_to: e.target.value })} /></div>
        <div><label>Status</label><select className="mt-1" value={filters.status} onChange={e => setFilters({ ...filters, status: e.target.value })}><option value="">All</option>{['SUBMITTED', 'CORRECTION_REQUIRED', 'RESUBMITTED', 'VERIFIED', 'REJECTED'].map(x => <option key={x}>{x}</option>)}</select></div>
        <div><label>Activity type</label><input className="mt-1" value={filters.activity_type} onChange={e => setFilters({ ...filters, activity_type: e.target.value })} /></div>
        {isSbu && <div><label>ALC</label><select className="mt-1" value={filters.alc_id} onChange={e => setFilters({ ...filters, alc_id: e.target.value })}><option value="">All assigned</option>{alcs.data?.items.map(a => <option key={a.id} value={a.id}>{a.alc_code} · {a.alc_name}</option>)}</select></div>}
      </div>
      <a className="btn-primary mt-5" href={api.downloadUrl(`/portal/reports/activities.csv?${params}`)}><Download className="h-4 w-4" />Download activity CSV</a>
    </section>
    <section className="panel mt-6 p-6"><h2 className="font-bold text-navy">Verification status report</h2><p className="mt-1 text-sm text-slate-500">Per-ALC counts by status with verified learner reach, leads and admissions.</p>
      <a className="btn-secondary mt-4" href={api.downloadUrl('/portal/reports/verification-status.csv')}><Download className="h-4 w-4" />Download verification status CSV</a>
    </section>
    <section className="panel mt-6 p-6"><h2 className="font-bold text-navy">Partner report</h2><p className="mt-1 text-sm text-slate-500">Partners across your ALCs with activity count and last activity.</p>
      <a className="btn-secondary mt-4" href={api.downloadUrl(`/portal/reports/partners.csv${filters.alc_id ? `?alc_id=${filters.alc_id}` : ''}`)}><Download className="h-4 w-4" />Download partner CSV</a>
    </section>
  </>
}