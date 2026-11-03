import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { api } from '../lib/api'
import { toQuery } from '../lib/constants'
import { useDebouncedSearch, usePageFor } from '../lib/hooks'
import type { User } from '../types'
import { Badge, ConfirmDialog, Empty, ErrorState, formatDate, Loading, MetricCard, PageHeader, Pager } from '../components/ui'

type RunStatus = 'AVAILABLE' | 'SCHEDULED' | 'ACTIVE' | 'MARKING' | 'FINALIZED'
interface Run {
  id: string; dcu_id: string; dcu_name: string; status: RunStatus
  scheduled_start_date: string | null; scheduled_end_date: string | null; instructions: string | null; finalized_at: string | null
}
interface Definition {
  id: string; rcu_id: string; calendar_year: number; activity_code: string; name: string; description: string | null
  introduced_by_dcu: { id: string; code: string; name: string } | null; created_at: string
  regional_status: 'PENDING' | 'ELIGIBLE'; participant_count: number; finalized_count: number; runs: Run[]
}
interface PageData<T> { items: T[]; total: number; page: number; page_size: number; pages?: number }
interface AnnualSummary { completed_count: number; total_applicable: number; completion_percentage: number; official_completed_count: number }
interface DefinitionPage extends PageData<Definition> { summary: AnnualSummary }
interface Completion {
  alc_id: string; alc_code: string; alc_name: string; sbu_id: string | null; sbu_name: string | null
  completed: boolean | null; marked_at: string | null; updated_at: string | null; can_edit?: boolean
  yearly_completed_count: number; yearly_official_completed_count: number; yearly_total_applicable: number
}
interface CompletionPage extends PageData<Completion> { summary: { yes: number; no: number; not_marked: number }; sbus: { id: string; name: string }[] }
interface Notice { message: string; error?: boolean }
const BASE = '/yearly-activities'
const currentYear = () => Number(new Intl.DateTimeFormat('en', { timeZone: 'Asia/Kolkata', year: 'numeric' }).format(new Date()))
const pages = (data: PageData<unknown>) => data.pages ?? Math.ceil(data.total / data.page_size)
const decision = (value: boolean | null) => value === null ? 'NOT MARKED' : value ? 'YES' : 'NO'
const timestamp = (value: string | null) => value ? new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Kolkata' }).format(new Date(value)) : '—'

function DefinitionForm({ year, onSaved, onCancel }: { year: number; onSaved: () => void; onCancel: () => void }) {
  const [form, setForm] = useState({ name: '', activity_code: '', description: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const duplicateCode = useDebouncedSearch(form.activity_code.normalize('NFKC').trim().toUpperCase().replace(/[\s_]+/g, '-'))
  const duplicateQuery = toQuery({ year, q: duplicateCode, page_size: 5 })
  const candidates = useQuery({ queryKey: ['yearly-duplicate', duplicateQuery], queryFn: () => api.get<PageData<Definition>>(`${BASE}/definitions?${duplicateQuery}`), enabled: duplicateCode.length >= 2 })
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { await api.post(`${BASE}/definitions`, { ...form, name: form.name.trim(), calendar_year: year }); onSaved() }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to register activity') }
    finally { setBusy(false) }
  }
  return <form aria-label="Register regional activity" onSubmit={save} className="panel mb-5 grid gap-4 p-5 sm:grid-cols-2">
    <div className="sm:col-span-2"><h2 className="font-bold text-navy">Register a regional activity for {year}</h2><p className="mt-1 text-sm text-slate-600">Search the registry first and reuse an existing activity. Use a distinct regional code for a separate activity with a similar name. The shared identity is locked after registration.</p></div>
    <label>Activity name<input required maxLength={180} minLength={2} value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></label>
    <label>Regional activity code<input required maxLength={64} minLength={2} value={form.activity_code} onChange={event => setForm({ ...form, activity_code: event.target.value })} placeholder="e.g. CAREER-EXPO" /></label>
    {candidates.data && candidates.data.items.length > 0 && <div role="note" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 sm:col-span-2">Possible existing activities: {candidates.data.items.map(item => `${item.activity_code} — ${item.name}`).join('; ')}. Cancel and select the matching registry entry to reuse it.</div>}
    {candidates.error && <div className="sm:col-span-2"><ErrorState error={candidates.error} /></div>}
    <label className="sm:col-span-2">Description<textarea maxLength={4000} value={form.description} onChange={event => setForm({ ...form, description: event.target.value })} /></label>
    {error && <p role="alert" className="text-sm text-red-700 sm:col-span-2">{error}</p>}
    <div className="flex gap-2 sm:col-span-2"><button className="btn-primary" disabled={busy}>Register activity</button><button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>Cancel</button></div>
  </form>
}

function RunSchedule({ run, year, onSaved }: { run: Run; year: number; onSaved: () => void }) {
  const [start, setStart] = useState(run.scheduled_start_date ?? '')
  const [end, setEnd] = useState(run.scheduled_end_date ?? '')
  const [instructions, setInstructions] = useState(run.instructions ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { await api.patch(`${BASE}/runs/${run.id}`, { scheduled_start_date: start, scheduled_end_date: end, instructions, status: 'SCHEDULED' }); onSaved() }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to save schedule') }
    finally { setBusy(false) }
  }
  return <form aria-label="DCU activity schedule" onSubmit={save} className="mt-4 grid gap-3 border-t pt-4 sm:grid-cols-2">
    <label>DCU start date<input type="date" required min={`${year}-01-01`} max={`${year}-12-31`} value={start} onChange={event => setStart(event.target.value)} /></label>
    <label>DCU end date<input type="date" required min={start || `${year}-01-01`} max={`${year}-12-31`} value={end} onChange={event => setEnd(event.target.value)} /></label>
    <label className="sm:col-span-2">Instructions (optional)<textarea maxLength={4000} value={instructions} onChange={event => setInstructions(event.target.value)} /></label>
    {error && <p role="alert" className="text-sm text-red-700 sm:col-span-2">{error}</p>}
    <div><button className="btn-primary" disabled={busy}>Save DCU schedule</button></div>
  </form>
}

function CompletionGrid({ run, writable, onSaved }: { run: Run; writable: boolean; onSaved: () => void }) {
  const [searchText, setSearchText] = useState('')
  const search = useDebouncedSearch(searchText)
  const [sbu, setSbu] = useState('')
  const [page, setPage] = usePageFor(`${run.id}:${search}:${sbu}`)
  const [saving, setSaving] = useState<string | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const client = useQueryClient()
  const query = toQuery({ q: search, sbu_id: sbu, page, page_size: 25 })
  const q = useQuery({ queryKey: ['yearly-completions', run.id, query], queryFn: () => api.get<CompletionPage>(`${BASE}/runs/${run.id}/completions?${query}`) })
  async function mark(row: Completion, value: string) {
    setSaving(row.alc_id); setNotice(null)
    try {
      await api.patch(`${BASE}/runs/${run.id}/completions/${row.alc_id}`, { completed: value === 'pending' ? null : value === 'yes' })
      await client.invalidateQueries({ queryKey: ['yearly-completions', run.id] }); onSaved()
      setNotice({ message: `${row.alc_code}: ${value === 'pending' ? 'NOT MARKED' : value.toUpperCase()} saved.` })
    } catch (caught) { setNotice({ message: caught instanceof Error ? caught.message : 'Unable to save completion', error: true }) }
    finally { setSaving(null) }
  }
  return <section className="mt-4 border-t pt-4" aria-label={`${run.dcu_name} ALC completion decisions`}>
    <div className="mb-3 flex flex-wrap gap-3"><label className="flex-1">Search ALCs<input placeholder="Search ALC code or name" maxLength={180} value={searchText} onChange={event => setSearchText(event.target.value)} /></label><label className="min-w-48">SBU<select aria-label="Completion SBU" value={sbu} onChange={event => setSbu(event.target.value)}><option value="">All authorized SBUs</option>{q.data?.sbus?.map(unit => <option key={unit.id} value={unit.id}>{unit.name}</option>)}</select></label></div>
    {notice && <p role={notice.error ? 'alert' : 'status'} className={`mb-3 text-sm ${notice.error ? 'text-red-700' : 'text-emerald-800'}`}>{notice.message}</p>}
    {q.isLoading ? <Loading label="Loading ALC decisions" /> : q.error || !q.data ? <ErrorState error={q.error} /> : <>
      <p className="mb-3 text-sm text-slate-600" aria-live="polite">YES: {q.data.summary.yes} · NO: {q.data.summary.no} · NOT MARKED: {q.data.summary.not_marked}</p>
      {!q.data.items.length ? <Empty title="No ALCs match" message="Try another search or SBU filter." /> : <div className="table-wrap"><table className="table-dense"><thead><tr><th>ALC Code</th><th>ALC Name</th><th>SBU</th><th>Status</th><th>Year completed / total</th><th>Year official completions</th><th>Completion date (IST)</th><th>Last updated (IST)</th></tr></thead><tbody>{q.data.items.map(row => <tr key={row.alc_id}>
        <td className="font-semibold text-navy">{row.alc_code}</td><td>{row.alc_name}</td><td>{row.sbu_name ?? '—'}</td><td>{writable && row.can_edit !== false ? <select className="min-w-36" aria-label={`Completion for ${row.alc_code}`} value={row.completed === null ? 'pending' : row.completed ? 'yes' : 'no'} disabled={saving !== null || q.isFetching} onChange={event => void mark(row, event.target.value)}><option value="pending">NOT MARKED</option><option value="yes">YES</option><option value="no">NO</option></select> : <span>{decision(row.completed)}{row.can_edit === false && writable && <span className="block text-xs text-slate-500">Historical assignment · locked</span>}</span>}</td><td>{row.yearly_completed_count} / {row.yearly_total_applicable}</td><td>{row.yearly_official_completed_count}</td><td>{row.completed ? timestamp(row.marked_at) : '—'}</td><td>{timestamp(row.updated_at ?? row.marked_at)}</td>
      </tr>)}</tbody></table></div>}
      <Pager page={q.data.page} pages={pages(q.data)} total={q.data.total} noun="ALCs" onPage={setPage} />
    </>}
  </section>
}

function RunPanel({ run, year, canManage, onSaved }: { run: Run; year: number; canManage: boolean; onSaved: () => void }) {
  const [showRows, setShowRows] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [confirm, setConfirm] = useState(false)
  async function transition(next: 'ACTIVE' | 'MARKING' | 'FINALIZED') {
    setBusy(true); setError('')
    try {
      if (next === 'FINALIZED') await api.post(`${BASE}/runs/${run.id}/finalize`)
      else await api.patch(`${BASE}/runs/${run.id}`, { status: next })
      setConfirm(false); onSaved()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to change activity status'); setConfirm(false) }
    finally { setBusy(false) }
  }
  return <div className="mt-4 rounded-lg border border-line bg-white p-4">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold text-navy">{run.dcu_name}</h3><p className="mt-1 text-sm text-slate-600">{run.scheduled_start_date ? `${formatDate(run.scheduled_start_date)} – ${formatDate(run.scheduled_end_date ?? undefined)}` : 'DCU schedule not set'}</p></div><Badge status={run.status} /></div>
    {run.instructions && <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">{run.instructions}</p>}
    {canManage && ['AVAILABLE', 'SCHEDULED'].includes(run.status) && <RunSchedule key={`${run.id}:${run.status}:${run.scheduled_start_date}:${run.scheduled_end_date}`} run={run} year={year} onSaved={onSaved} />}
    {canManage && run.status !== 'FINALIZED' && <div className="mt-3 flex flex-wrap gap-2">
      {run.status === 'SCHEDULED' && <button className="btn-primary" disabled={busy} onClick={() => void transition('ACTIVE')}>Activate DCU activity</button>}
      {run.status === 'ACTIVE' && <button className="btn-primary" disabled={busy} onClick={() => void transition('MARKING')}>Start completion marking</button>}
      {run.status === 'MARKING' && <button className="btn-primary" disabled={busy} onClick={() => setConfirm(true)}>Finalize DCU activity</button>}
    </div>}
    {run.status === 'FINALIZED' && <p className="mt-3 text-sm text-emerald-800">DCU results finalized {timestamp(run.finalized_at)}. Completion editing is locked.</p>}
    {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
    <button className="btn-secondary mt-3" aria-expanded={showRows} onClick={() => setShowRows(!showRows)}>{showRows ? 'Hide ALC decisions' : 'View ALC decisions'}</button>
    {showRows && <CompletionGrid run={run} writable={canManage && run.status === 'MARKING'} onSaved={onSaved} />}
    {confirm && <ConfirmDialog title="Finalize this DCU activity?" body="Every participating ALC must be explicitly marked YES or NO and the scheduled period must have ended. Finalization locks these results. Regional points remain pending until every participating DCU finalizes." confirmLabel="Finalize and lock results" busy={busy} onConfirm={() => void transition('FINALIZED')} onCancel={() => setConfirm(false)} />}
  </div>
}

export default function YearlyActivities() {
  const user = useOutletContext<User>()
  const client = useQueryClient()
  const [year, setYear] = useState(currentYear)
  const [yearDraft, setYearDraft] = useState(String(year))
  const [searchText, setSearchText] = useState('')
  const search = useDebouncedSearch(searchText)
  const [page, setPage] = usePageFor(`${year}:${search}`)
  const [creating, setCreating] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const query = toQuery({ year, q: search, page, page_size: 25 })
  const q = useQuery({ queryKey: ['yearly-definitions', query], queryFn: () => api.get<DefinitionPage>(`${BASE}/definitions?${query}`) })
  const canCreate = user.role === 'DCU'
  function refresh() {
    void client.invalidateQueries({ queryKey: ['yearly-definitions'] })
    void client.invalidateQueries({ queryKey: ['region-top10'] })
    void client.invalidateQueries({ queryKey: ['region-scores'] })
  }
  return <>
    <PageHeader title="Yearly Activities" description={canCreate ? 'Reuse the common regional registry, schedule your DCU runs and record ALC completion. Official regional scores wait for every participating DCU.' : user.role === 'ADMIN' ? 'Regional registry, participating DCUs and finalization oversight. Completion decisions are managed by the responsible DCU.' : 'Read-only yearly activity progress for your authorized ALCs. Pending regional results are provisional.'} actions={canCreate && <button className="btn-primary" onClick={() => setCreating(true)} disabled={creating}>Register new regional activity</button>} />
    <div className="panel mb-4 flex flex-wrap items-end gap-4 p-4">
      <form className="flex items-end gap-2" onSubmit={event => { event.preventDefault(); setYear(Number(yearDraft)); setSelected(null); setCreating(false) }}><label>Calendar year<input aria-label="Calendar year" className="mt-1 max-w-32" type="number" min={2000} max={2100} required value={yearDraft} onChange={event => setYearDraft(event.target.value)} /></label><button className="btn-secondary">Apply year</button></form>
      <label className="min-w-48 flex-1">Search regional registry<input placeholder="Activity name or regional code" maxLength={180} value={searchText} onChange={event => setSearchText(event.target.value)} /></label>
    </div>
    {notice && <p role="status" className="mb-4 text-sm text-emerald-800">{notice}</p>}
    {creating && <DefinitionForm key={year} year={year} onSaved={() => { setCreating(false); setNotice('Regional activity registered. Every participating DCU can use the same activity identity.'); refresh() }} onCancel={() => setCreating(false)} />}
    {q.data?.summary && <AnnualProgress summary={q.data.summary} ownAlc={user.role === 'ALC'} />}
    {q.isLoading ? <Loading label="Loading regional activities" /> : q.error || !q.data ? <ErrorState error={q.error} /> : !q.data.items.length ? <Empty title="No yearly activities match" message={canCreate ? 'Search another name or code, or register a new regional activity.' : 'Activities will appear when your region registers them.'} /> : <>
      <div className="space-y-4">{q.data.items.map(activity => <article key={activity.id} className="panel p-5">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-bold text-navy">{activity.name}</h2><p className="mt-1 text-xs text-slate-500">{activity.activity_code} · {activity.calendar_year}{activity.introduced_by_dcu && ` · Introduced by ${activity.introduced_by_dcu.name}`}</p></div><Badge status={activity.regional_status === 'ELIGIBLE' ? 'REGIONALLY_ELIGIBLE' : 'REGIONAL_FINALIZATION_PENDING'} /></div>
        {activity.description && <p className="mt-3 whitespace-pre-wrap text-sm text-slate-700">{activity.description}</p>}
        <p className="mt-3 text-sm text-slate-600">{activity.finalized_count} / {activity.participant_count} participating DCUs finalized. {activity.regional_status === 'ELIGIBLE' ? 'YES results count toward official regional scoring.' : 'Results are provisional until every required DCU finalizes.'}</p>
        <button className="btn-secondary mt-3" aria-expanded={selected === activity.id} onClick={() => setSelected(selected === activity.id ? null : activity.id)}>{selected === activity.id ? 'Close activity' : canCreate ? 'Manage my DCU run' : 'View activity details'}</button>
        {selected === activity.id && <>{activity.runs.length ? activity.runs.map(run => <RunPanel key={run.id} run={run} year={activity.calendar_year} canManage={canCreate && user.dcu_id === run.dcu_id} onSaved={refresh} />) : <p className="mt-4 text-sm text-slate-500">No participating run in your authorized scope.</p>}</>}
      </article>)}</div>
      <Pager page={q.data.page} pages={pages(q.data)} total={q.data.total} noun="regional activities" onPage={setPage} />
    </>}
  </>
}

// Annual summaries are scoped and aggregated on the server, without per-ALC requests.
function AnnualProgress({ summary, ownAlc }: { summary: AnnualSummary; ownAlc: boolean }) {
  return <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><MetricCard label={ownAlc ? 'Completed activities' : 'Completed ALC decisions'} value={summary.completed_count} hint="Includes provisional YES results" /><MetricCard label={ownAlc ? 'Applicable activities' : 'Applicable ALC decisions'} value={summary.total_applicable} hint={ownAlc ? undefined : 'Across authorized ALCs and yearly activities'} /><MetricCard label="Completion" value={`${summary.completion_percentage.toFixed(1)}%`} /><MetricCard label="Official completions" value={summary.official_completed_count} hint="All participating DCUs finalized" /></div>
}
