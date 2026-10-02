import { useState } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { challengePeriodSummary, challengeTimePercent, inclusiveDays, TARGET_KEYS, type ChallengePeriod, type TargetKey } from '../lib/challenge'
import { toQuery } from '../lib/constants'
import { useDebouncedSearch, usePageFor } from '../lib/hooks'
import type { Page } from '../types'
import { Badge, Empty, ErrorState, formatDate, Loading, PageHeader, Pager } from '../components/ui'

interface Row { id: string; alc_code: string; alc_name: string; prospects: number; meetings: number; pilots: number; partnerships: number }
// Every ALC is measured over the global Growth Challenge period (`default_period`, any
// duration). `periods` holds only the ALCs that have their own override, by ALC id.
type RowPeriod = ChallengePeriod & { targets?: Record<string, number> }
// A configured global challenge (`challenges`, newest period first).
interface ChallengeConfig { id: string; name: string; start_date: string; end_date: string; targets: Record<string, number>; status: ChallengePeriod['status']; total_days: number }
interface ChallengePage extends Page<Row> {
  targets: Record<string, number>
  default_period?: RowPeriod
  periods?: Record<string, RowPeriod>
  challenges?: ChallengeConfig[]
  today?: string
}
type FormState = { id: string | null; name: string; start_date: string; end_date: string } & Record<TargetKey, string>

// Server-side paginated and searchable: every ALC is reachable, one page is fetched at a time.
const CHALLENGE_PAGE_SIZE = 100
const DEFAULT_TARGETS: Record<TargetKey, number> = { prospects: 40, meetings: 20, pilots: 10, partnerships: 5 }

function formFor(challenge: ChallengeConfig | null, today: string, targets: Record<string, number>): FormState {
  const source = challenge?.targets ?? targets
  const target = (key: TargetKey) => String(source[key] ?? DEFAULT_TARGETS[key])
  return {
    id: challenge?.id ?? null, name: challenge?.name ?? 'Growth Challenge',
    start_date: challenge?.start_date ?? today, end_date: challenge?.end_date ?? '',
    prospects: target('prospects'), meetings: target('meetings'), pilots: target('pilots'), partnerships: target('partnerships'),
  }
}

// Longer than this is allowed (there is no maximum); the form only asks the Admin to double-check.
const LONG_CHALLENGE_DAYS = 366

function ChallengeForm({ initial, onSaved, onCancel }: { initial: FormState; onSaved: () => void; onCancel: () => void }) {
  const [form, setForm] = useState(initial)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const set = (key: keyof FormState, value: string) => setForm(current => ({ ...current, [key]: value }))
  const length = inclusiveDays(form.start_date, form.end_date)
  const backwards = Boolean(form.start_date && form.end_date) && form.end_date < form.start_date

  async function save(event: React.FormEvent) {
    event.preventDefault(); setError('')
    if (backwards) { setError('End date must be on or after the start date.'); return }
    const body = {
      name: form.name.trim(), start_date: form.start_date, end_date: form.end_date,
      prospects_target: Number(form.prospects), meetings_target: Number(form.meetings),
      pilots_target: Number(form.pilots), partnerships_target: Number(form.partnerships),
    }
    setBusy(true)
    try {
      if (form.id) await api.patch(`/admin/growth-challenges/${form.id}`, body)
      else await api.post('/admin/growth-challenges', body)
      onSaved()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to save the Growth Challenge') }
    finally { setBusy(false) }
  }

  return <form onSubmit={save} aria-label="Growth Challenge period" className="mt-4 grid gap-4 border-t pt-4 md:grid-cols-4">
    <div className="md:col-span-2"><label htmlFor="gc-name">Challenge name</label><input id="gc-name" className="mt-1" required minLength={2} maxLength={150} value={form.name} onChange={e => set('name', e.target.value)} /></div>
    <div><label htmlFor="gc-start">Start date</label><input id="gc-start" className="mt-1" type="date" required value={form.start_date} onChange={e => set('start_date', e.target.value)} /></div>
    <div><label htmlFor="gc-end">End date</label><input id="gc-end" className="mt-1" type="date" required min={form.start_date || undefined} value={form.end_date} onChange={e => set('end_date', e.target.value)} /></div>
    {TARGET_KEYS.map(key => <div key={key}><label htmlFor={`gc-${key}`} className="capitalize">{key} target</label><input id={`gc-${key}`} className="mt-1" type="number" required min={0} max={1000000} step={1} value={form[key]} onChange={e => set(key, e.target.value)} /></div>)}
    <p className="text-sm text-slate-600 md:col-span-4" aria-live="polite">{backwards ? 'End date is before the start date.' : length ? `Duration: ${length} ${length === 1 ? 'day' : 'days'} (both dates included).` : 'Choose the start and end dates; the challenge can run for any number of days.'}</p>
    {length > LONG_CHALLENGE_DAYS && <p role="note" className="text-sm text-amber-800 md:col-span-4">This is a long challenge ({length} days). Please check the dates; you can still save it.</p>}
    {error && <p role="alert" className="text-sm text-red-700 md:col-span-4">{error}</p>}
    <div className="flex gap-2 md:col-span-4"><button className="btn-primary" disabled={busy}>{busy ? 'Saving…' : form.id ? 'Save changes' : 'Save challenge'}</button><button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>Cancel</button></div>
  </form>
}

export default function AdminChallenge() {
  const client = useQueryClient()
  const [searchText, setSearchText] = useState('')
  const search = useDebouncedSearch(searchText)
  const [page, setPage] = usePageFor(search)
  const [editing, setEditing] = useState<FormState | null>(null)
  const query = toQuery({ page, page_size: CHALLENGE_PAGE_SIZE, search })
  const q = useQuery({ queryKey: ['admin-challenge', query], queryFn: () => api.get<ChallengePage>(`/admin/challenge?${query}`), placeholderData: keepPreviousData })
  const data = q.data
  const current = data?.default_period
  const challenges = data?.challenges ?? []
  const today = data?.today ?? ''
  const currentConfig = challenges.find(c => c.id === current?.challenge_id) ?? null
  const open = (challenge: ChallengeConfig | null) => setEditing(formFor(challenge, today, data?.targets ?? DEFAULT_TARGETS))
  function saved() {
    setEditing(null)
    client.invalidateQueries({ queryKey: ['admin-challenge'] })
    client.invalidateQueries({ queryKey: ['challenge'] })
  }

  return <>
    <PageHeader title="Growth Challenge" description="Progress calculated from verified activities and active partner records, over the configured Growth Challenge period." />
    {current && <section className="panel mb-4 p-5" aria-label="Challenge period">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-bold text-navy">Challenge period</h2>
          {current.configured
            ? <><p className="mt-1 text-sm text-slate-700"><b>{current.name ?? 'Growth Challenge'}</b> · {formatDate(current.period_start ?? undefined)} – {formatDate(current.period_end ?? undefined)}</p><p className="text-sm text-slate-500">{challengePeriodSummary(current)}</p></>
            : <p className="mt-1 text-sm text-slate-600">No Growth Challenge is configured. ALCs see no challenge until a period is set.</p>}
        </div>
        {!editing && <div className="flex gap-2">
          {currentConfig && <button className="btn-secondary" onClick={() => open(currentConfig)}>Edit period</button>}
          <button className={currentConfig ? 'btn-secondary' : 'btn-primary'} onClick={() => open(null)}>{challenges.length ? 'Add period' : 'Configure challenge'}</button>
        </div>}
      </div>
      {current.configured && <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-label="Challenge time elapsed" aria-valuemin={0} aria-valuemax={100} aria-valuenow={challengeTimePercent(current)}><div className="h-full bg-navy" style={{ width: `${challengeTimePercent(current)}%` }} /></div>}
      {editing && <ChallengeForm key={editing.id ?? 'new'} initial={editing} onSaved={saved} onCancel={() => setEditing(null)} />}
      {challenges.length > 1 && !editing && <div className="table-wrap mt-4"><table><thead><tr><th>Configured periods</th><th>Dates</th><th>Duration</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{challenges.map(c => <tr key={c.id}><td><b>{c.name}</b></td><td className="whitespace-nowrap">{formatDate(c.start_date)} – {formatDate(c.end_date)}</td><td>{c.total_days} {c.total_days === 1 ? 'day' : 'days'}</td><td><Badge status={c.status} /></td><td><button className="btn-secondary" onClick={() => open(c)}>Edit</button></td></tr>)}</tbody></table></div>}
    </section>}
    <div className="panel mb-4 p-4"><input aria-label="Search ALCs" placeholder="Search ALC code or centre name" value={searchText} onChange={e => setSearchText(e.target.value)} /></div>
    {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : !data?.items.length ? <Empty title="No ALCs match" message="Try a different ALC code or centre name." /> : <>
      <div className="table-wrap"><table><thead><tr><th>ALC</th><th>Period</th>{Object.keys(data.targets).map(k => <th key={k} className="capitalize">{k}</th>)}</tr></thead><tbody>{data.items.map(r => {
        const period: RowPeriod | undefined = data.periods?.[r.id] ?? data.default_period
        const targets = period?.targets ?? data.targets
        // An ALC with no challenge has nothing to measure: show a dash, not "0 / 40".
        const unconfigured = period !== undefined && !period.configured
        return <tr key={r.id}>
          <td><b>{r.alc_code}</b><p className="text-xs text-slate-500">{r.alc_name}</p></td>
          <td className="whitespace-nowrap">{!period ? '—' : unconfigured ? <span className="text-slate-500">Not configured</span> : <>{formatDate(period.period_start ?? undefined)} – {formatDate(period.period_end ?? undefined)}<p className="text-xs text-slate-500">{challengePeriodSummary(period)}{period.period_source === 'ALC_OVERRIDE' ? ' · own period' : ''}</p></>}</td>
          {Object.keys(data.targets).map(k => <td key={k}>{unconfigured ? <span className="text-slate-400">—</span> : <><b>{r[k as keyof Row] as number}</b><span className="text-slate-400"> / {targets[k] ?? data.targets[k]}</span></>}</td>)}
        </tr>
      })}</tbody></table></div>
      <Pager page={data.page} pages={data.pages} total={data.total} noun="ALCs" onPage={setPage} />
    </>}
  </>
}
