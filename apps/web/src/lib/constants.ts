// Shared option lists. Activity types mirror the ALC activity editor.
export const ACTIVITY_TYPES = ['Prospect outreach', 'Partner meeting', 'Pilot programme', 'Collaboration event', 'Career awareness session', 'Admission campaign', 'Community engagement', 'Training programme']
// Statuses a supervisor can filter on. DRAFT is private to the ALC and never listed.
export const WORKFLOW_STATUSES = ['SUBMITTED', 'RESUBMITTED', 'UNDER_REVIEW', 'CORRECTION_REQUIRED', 'VERIFIED', 'REJECTED']
// Build a query string from non-empty filter values.
export function toQuery(values: Record<string, string | number | boolean | undefined | null>) {
  return new URLSearchParams(Object.entries(values).filter(([, v]) => v !== '' && v !== undefined && v !== null && v !== false).map(([k, v]) => [k, String(v)])).toString()
}
