import { Fragment, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { Badge, Empty, ErrorState, formatDate, formatNumber, Loading, MetricCard, Pager } from './ui'

type Challenge = { id: string; name: string; description: string; start_date: string; end_date: string; status: string; targets: Record<string, number> }
type Summary = { eligible: number; participating: number; completed: number; in_progress: number; not_started: number; completion_percent: number }
type Sbu = { sbu_id: string; sbu_code: string; sbu_name: string; eligible: number; participating: number; completed: number; completion_percent: number }
type Row = { id: string; alc_code: string; alc_name: string; sbu_code: string; prospects: number; meetings: number; pilots: number; partnerships: number; activity_count: number; evidence_count: number; progress_status: string }
type Page = { challenge: Challenge; summary: Summary; sbu_breakdown: Sbu[]; items: Row[]; page: number; pages: number; total: number; targets: Record<string, number> }
type EvidenceActivity = { id: string; activity_number: string; activity_type: string; activity_date: string; outcome: string; evidence: { id: string; original_filename: string; mime_type: string; file_size: number; access_url: string }[] }

function Evidence({ challengeId, alcId }: { challengeId: string; alcId: string }) {
  const q = useQuery({ queryKey: ['challenge-evidence', challengeId, alcId], queryFn: () => api.get<{ items: EvidenceActivity[] }>(`/portal/growth-challenges/${challengeId}/alcs/${alcId}/evidence`) })
  async function open(accessUrl: string) {
    const { url } = await api.get<{ url: string }>(accessUrl.replace('/api', ''))
    window.open(url, '_blank', 'noopener,noreferrer')
  }
  if (q.isLoading) return <Loading label="Loading challenge evidence" />
  if (q.error) return <ErrorState error={q.error} />
  return <div className="bg-slate-50 p-4">{q.data?.items.length ? q.data.items.map(activity => <div key={activity.id} className="mb-3 rounded border bg-white p-3"><p className="font-semibold">{activity.activity_number} · {activity.activity_type}</p><p className="text-xs text-slate-500">{formatDate(activity.activity_date)}</p>{activity.outcome && <p className="mt-1 text-sm">{activity.outcome}</p>}<div className="mt-2 flex flex-wrap gap-2">{activity.evidence.map(item => <button key={item.id} type="button" className="btn-secondary" onClick={() => void open(item.access_url)}>{item.original_filename}</button>)}</div></div>) : <p className="text-sm text-slate-500">No verified challenge evidence is available.</p>}</div>
}

export default function ChallengeTracking({ role }: { role: 'DCU' | 'SBU' }) {
  const [selected, setSelected] = useState('')
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [sbu, setSbu] = useState('')
  const [evidenceAlc, setEvidenceAlc] = useState('')
  const list = useQuery({ queryKey: ['visible-growth-challenges'], queryFn: () => api.get<{ items: Challenge[] }>('/portal/growth-challenges'), staleTime: 60_000 })
  const challenges = Array.isArray(list.data?.items) ? list.data.items : []
  const challengeId = selected || challenges.find(item => item.status === 'ACTIVE')?.id || challenges[0]?.id || ''
  const params = new URLSearchParams({ page: String(page), page_size: '25' })
  if (search.trim()) params.set('search', search.trim())
  if (sbu) params.set('sbu_id', sbu)
  const progress = useQuery({ queryKey: ['growth-challenge-progress', challengeId, page, search, sbu], queryFn: () => api.get<Page>(`/portal/growth-challenges/${challengeId}/progress?${params}`), enabled: !!challengeId, placeholderData: previous => previous })
  if (list.isLoading) return <Loading label="Loading Growth Challenges" />
  if (list.error) return <ErrorState error={list.error} />
  if (!challenges.length) return <section className="mt-8"><Empty title="No published Growth Challenge" message="Published Admin challenges will appear here." /></section>
  if (progress.isLoading || !progress.data) return <Loading label="Calculating challenge progress" />
  if (progress.error) return <ErrorState error={progress.error} />
  const data = progress.data
  return <section className="mt-8" aria-labelledby="challenge-tracking-title">
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3"><div><h2 id="challenge-tracking-title" className="text-xl font-bold text-navy">Growth Challenge tracking</h2><p className="mt-1 text-sm text-slate-600"><b>{data.challenge.name}</b> · {formatDate(data.challenge.start_date)} – {formatDate(data.challenge.end_date)}</p>{data.challenge.description && <p className="mt-1 max-w-3xl text-sm text-slate-600">{data.challenge.description}</p>}</div>{challenges.length > 1 && <label>Challenge<select className="mt-1" value={challengeId} onChange={event => { setSelected(event.target.value); setPage(1); setSbu('') }}>{challenges.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}</div>
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-6"><MetricCard label="Eligible ALCs" value={formatNumber(data.summary.eligible)} /><MetricCard label="Participating" value={formatNumber(data.summary.participating)} /><MetricCard label="Completed" value={formatNumber(data.summary.completed)} /><MetricCard label="In progress" value={formatNumber(data.summary.in_progress)} /><MetricCard label="Not started" value={formatNumber(data.summary.not_started)} /><MetricCard label="Completion" value={`${data.summary.completion_percent}%`} /></div>
    {role === 'DCU' && data.sbu_breakdown.length > 0 && <div className="table-wrap mt-5"><table className="table-dense"><thead><tr><th>SBU</th><th>Eligible</th><th>Participating</th><th>Completed</th><th>Completion</th></tr></thead><tbody>{data.sbu_breakdown.map(row => <tr key={row.sbu_id}><td><button className="font-semibold text-teal" onClick={() => { setSbu(row.sbu_id); setPage(1) }}>{row.sbu_code}</button><p className="text-xs text-slate-500">{row.sbu_name}</p></td><td>{row.eligible}</td><td>{row.participating}</td><td>{row.completed}</td><td>{row.completion_percent}%</td></tr>)}</tbody></table></div>}
    <div className="panel mt-5 flex flex-wrap gap-3 p-4"><input aria-label="Search challenge ALCs" placeholder="Search ALC code or name" value={search} onChange={event => { setSearch(event.target.value); setPage(1) }} />{sbu && <button className="btn-secondary" onClick={() => { setSbu(''); setPage(1) }}>Clear SBU filter</button>}</div>
    <div className="table-wrap mt-4"><table className="table-dense"><thead><tr><th>ALC</th>{role === 'DCU' && <th>SBU</th>}<th>Status</th><th>Prospects</th><th>Meetings</th><th>Pilots</th><th>Partnerships</th><th>Evidence</th></tr></thead><tbody>{data.items.map(row => <Fragment key={row.id}><tr><td><b>{row.alc_code}</b><p className="text-xs text-slate-500">{row.alc_name}</p></td>{role === 'DCU' && <td>{row.sbu_code}</td>}<td><Badge status={row.progress_status} /></td><td>{row.prospects} / {data.targets.prospects}</td><td>{row.meetings} / {data.targets.meetings}</td><td>{row.pilots} / {data.targets.pilots}</td><td>{row.partnerships} / {data.targets.partnerships}</td><td><button className="btn-secondary" onClick={() => setEvidenceAlc(evidenceAlc === row.id ? '' : row.id)}>{row.evidence_count} files</button></td></tr>{evidenceAlc === row.id && <tr><td colSpan={role === 'DCU' ? 8 : 7}><Evidence challengeId={challengeId} alcId={row.id} /></td></tr>}</Fragment>)}</tbody></table></div>
    <Pager page={data.page} pages={data.pages} total={data.total} noun="ALCs" onPage={setPage} />
  </section>
}
