// Chart colours (SVG attributes need literal values; these mirror the tokens in index.css).

// Decorative series order for non-semantic charts: navy → blue → bright blue → brand red.
export const CHART_SERIES = ['#0E3557', '#1D5F91', '#2D8FC5', '#D94B45'] as const
export const CHART_PRIMARY = '#1D5F91'
export const CHART_AXIS = '#5F6B76'
export const CHART_GRID = '#D9E3EB'

// Activity status → semantic colour, matching the status badges: RCU info blues for in-workflow
// states, amber for correction, green for verified, semantic red for rejected (not brand red).
// Validated as a set (all pairs) for colour-vision-deficiency and normal-vision separation.
export const STATUS_CHART_COLORS: Record<string, string> = {
  SUBMITTED: '#2D8FC5', UNDER_REVIEW: '#1D5F91', RESUBMITTED: '#7CB9F5',
  CORRECTION_REQUIRED: '#CA8A04', VERIFIED: '#059669', REJECTED: '#DC2626', DRAFT: '#7B8794',
}
export const statusChartColor = (status: string) => STATUS_CHART_COLORS[status] ?? '#7B8794'
