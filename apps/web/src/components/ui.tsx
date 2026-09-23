import { ReactNode, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { AlertCircle, CheckCircle2, ClipboardCheck, Eye, Inbox, X } from 'lucide-react'
import type { Status } from '../types'

// Statuses for which a reviewer (SBU or Admin) may still act on an activity.
export const REVIEWABLE_STATUSES = ['SUBMITTED', 'RESUBMITTED', 'UNDER_REVIEW']

const statusClasses: Record<string, string> = {
  DRAFT: 'bg-slate-100 text-slate-700', SUBMITTED: 'bg-blue-100 text-blue-800', UNDER_REVIEW: 'bg-indigo-100 text-indigo-800',
  CORRECTION_REQUIRED: 'bg-amber-100 text-amber-900', RESUBMITTED: 'bg-violet-100 text-violet-800', VERIFIED: 'bg-emerald-100 text-emerald-800', REJECTED: 'bg-red-100 text-red-800',
  ACTIVE: 'bg-emerald-100 text-emerald-800', INACTIVE: 'bg-slate-100 text-slate-600', OPEN: 'bg-blue-100 text-blue-800', COMPLETED: 'bg-emerald-100 text-emerald-800'
}
export function Badge({ status }: { status: Status | string }) { return <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${statusClasses[status] ?? 'bg-slate-100 text-slate-700'}`}>{status.replaceAll('_', ' ')}</span> }
export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) { return <div className="mb-6 flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-2xl font-bold tracking-tight text-navy">{title}</h1>{description && <p className="mt-1 text-sm text-slate-600">{description}</p>}</div>{actions}</div> }
export function MetricCard({ label, value, hint }: { label: string; value: string | number; hint?: string }) { return <div className="panel p-4"><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p><p className="mt-2 text-2xl font-bold text-navy">{value}</p>{hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}</div> }
export function Loading({ label = 'Loading' }: { label?: string }) { return <div className="panel flex min-h-40 items-center justify-center p-8 text-sm text-slate-500"><span className="mr-3 h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-teal"/>{label}…</div> }
export function Empty({ title = 'Nothing to show', message = 'Records will appear here when available.' }: { title?: string; message?: string }) { return <div className="panel flex min-h-44 flex-col items-center justify-center p-8 text-center"><Inbox className="mb-3 text-slate-400"/><h3 className="font-semibold">{title}</h3><p className="mt-1 max-w-md text-sm text-slate-500">{message}</p></div> }
export function ErrorState({ error }: { error: unknown }) { return <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"><AlertCircle className="mr-2 inline h-4 w-4"/>{error instanceof Error ? error.message : 'Unable to load this section'}</div> }
export function FieldError({ message }: { message?: string }) { return message ? <p className="mt-1 text-xs text-red-700">{message}</p> : null }
export function Toast({ message, onClose, tone = 'success' }: { message: string; onClose: () => void; tone?: 'success' | 'error' }) {
  useEffect(() => { const t = setTimeout(onClose, 4000); return () => clearTimeout(t) }, [message, onClose])
  const styles = tone === 'error' ? 'border-red-200 bg-red-50 text-red-800' : 'border-emerald-200 bg-emerald-50 text-emerald-800'
  return <div role="status" className={`fixed bottom-5 right-5 z-50 flex max-w-sm items-start gap-3 rounded-lg border px-4 py-3 text-sm shadow-lg ${styles}`}>{tone === 'error' ? <AlertCircle className="mt-0.5 h-4 w-4 shrink-0"/> : <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0"/>}<span className="flex-1">{message}</span><button onClick={onClose} aria-label="Dismiss"><X className="h-4 w-4"/></button></div>
}
export function formatDate(value?: string) { return value ? new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium' }).format(new Date(value)) : '—' }
export function formatNumber(value?: number) { return new Intl.NumberFormat('en-IN').format(value ?? 0) }
// Row-level entry points into the activity review page. "View" (eye) is always available;
// "Review" (check) appears only when the status permits a decision. Both open the same
// review page at `${base}/${id}`, which itself gates the Verify/Correction/Reject actions
// server-side and by status — the buttons never bypass authorization.
export function RowActions({ base, id, status }: { base: string; id: string; status: string }) {
  const reviewable = REVIEWABLE_STATUSES.includes(status)
  return <div className="flex items-center gap-2">
    <Link to={`${base}/${id}`} title="View activity" aria-label="View activity" className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-semibold text-navy hover:border-teal hover:text-teal"><Eye className="h-3.5 w-3.5" />View</Link>
    {reviewable && <Link to={`${base}/${id}`} title="Review activity" aria-label="Review activity" className="inline-flex items-center gap-1 rounded-md bg-teal px-2 py-1 text-xs font-semibold text-white hover:bg-teal/90"><ClipboardCheck className="h-3.5 w-3.5" />Review</Link>}
  </div>
}
// Server-side pagination footer shared by directory and queue tables.
export function Pager({ page, pages, total, noun = 'records', onPage }: { page: number; pages: number; total: number; noun?: string; onPage: (page: number) => void }) {
  return <div className="mt-4 flex items-center justify-between text-sm"><span className="text-slate-600">Page {page} of {Math.max(pages, 1)} · {formatNumber(total)} {noun}</span><div className="flex gap-2"><button className="btn-secondary" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button><button className="btn-secondary" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button></div></div>
}
// Modal confirmation used before any recorded decision.
export function ConfirmDialog({ title, body, confirmLabel, tone = 'primary', busy, onConfirm, onCancel }: { title: string; body: ReactNode; confirmLabel: string; tone?: 'primary' | 'danger' | 'warning'; busy?: boolean; onConfirm: () => void; onCancel: () => void }) {
  useEffect(() => { const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onCancel() }; window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey) }, [busy, onCancel])
  const button = tone === 'danger' ? 'btn-danger' : tone === 'warning' ? 'btn bg-amber-600 text-white hover:bg-amber-700' : 'btn-primary'
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
    <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl"><h2 id="confirm-title" className="text-lg font-bold text-navy">{title}</h2><div className="mt-3 text-sm text-slate-700">{body}</div>
      <div className="mt-6 flex justify-end gap-2"><button type="button" className="btn-secondary" disabled={busy} onClick={onCancel}>Cancel</button><button type="button" className={button} disabled={busy} onClick={onConfirm}>{busy ? 'Saving…' : confirmLabel}</button></div></div>
  </div>
}
// Labelled filter control wrapper for desktop filter bars.
export function Filter({ label, children }: { label: string; children: ReactNode }) { return <div><label className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</label><div className="mt-1">{children}</div></div> }
