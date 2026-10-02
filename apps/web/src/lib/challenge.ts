// Growth Challenge period, as calculated by the API. The duration is variable (15, 30, 45,
// 60, 90 ... days): every day count comes from the server, which works in Asia/Kolkata, so
// nothing in the UI assumes 30 days and no "today" is taken from the browser's time zone.
export type ChallengeStatus = 'NOT_CONFIGURED' | 'UPCOMING' | 'ACTIVE' | 'COMPLETED'

export interface ChallengePeriod {
  period_start: string | null
  period_end: string | null
  configured: boolean
  status: ChallengeStatus
  total_days: number
  elapsed_days: number
  remaining_days: number
  days_until_start: number
  time_progress_percent: number
  name?: string | null
  period_source?: 'GLOBAL' | 'ALC_OVERRIDE' | null
  challenge_id?: string | null
}

export const TARGET_KEYS = ['prospects', 'meetings', 'pilots', 'partnerships'] as const
export type TargetKey = typeof TARGET_KEYS[number]

const days = (n: number) => `${n} ${n === 1 ? 'day' : 'days'}`

/** One-line description of where the challenge stands; '' when the API sent no period. */
export function challengePeriodSummary(period?: Partial<ChallengePeriod> | null): string {
  if (!period?.status) return ''
  if (period.status === 'NOT_CONFIGURED') return 'Not configured'
  const total = period.total_days
  if (!total) return ''
  switch (period.status) {
    case 'UPCOMING':
      return `${total}-day challenge · starts in ${days(period.days_until_start ?? 0)}`
    case 'ACTIVE': {
      const remaining = period.remaining_days ?? 0
      const left = remaining === 0 ? 'final day' : `${days(remaining)} remaining`
      return `Day ${period.elapsed_days ?? 0} of ${total} · ${left}`
    }
    case 'COMPLETED':
      return `${total}-day challenge · completed`
    default:
      return ''
  }
}

/** Share of the challenge period that has passed, 0–100, for a progress bar. */
export function challengeTimePercent(period?: Partial<ChallengePeriod> | null): number {
  const value = period?.time_progress_percent
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0
}

/** Inclusive length in days of two YYYY-MM-DD dates (pure calendar arithmetic); 0 if invalid. */
export function inclusiveDays(start: string, end: string): number {
  const iso = /^\d{4}-\d{2}-\d{2}$/
  if (!iso.test(start) || !iso.test(end)) return 0
  const from = Date.parse(`${start}T00:00:00Z`), to = Date.parse(`${end}T00:00:00Z`)
  if (Number.isNaN(from) || Number.isNaN(to) || to < from) return 0
  return Math.round((to - from) / 86_400_000) + 1
}
