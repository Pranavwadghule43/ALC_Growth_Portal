// Where the browser sends API requests. Production is same-origin: the reverse proxy serves
// the built app and forwards /api to FastAPI, so the API base is the relative path /api.
// Used at runtime by lib/api.ts and at build time by vite.config.ts.
export const DEV_API_URL = 'http://localhost:8000/api'
export const PRODUCTION_API_URL = '/api'

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '0.0.0.0', '[::1]'])

// VITE_API_URL when set (and not blank); otherwise the local backend in development and the
// same-origin /api in a production build, which therefore never falls back to localhost.
export function resolveApiUrl(configured: string | undefined, dev: boolean): string {
  const value = configured?.trim()
  return value ? value : dev ? DEV_API_URL : PRODUCTION_API_URL
}

// Why a production build must not use this VITE_API_URL, or null when it is acceptable:
// a same-origin path such as /api, or an absolute https:// URL that is not localhost.
export function productionApiUrlProblem(configured: string | undefined): string | null {
  const value = configured?.trim()
  if (!value) return null  // falls back to /api
  if (value.startsWith('//')) return 'VITE_API_URL must not be a protocol-relative URL'
  if (value.startsWith('/')) return null
  let url: URL
  try { url = new URL(value) } catch { return 'VITE_API_URL must be a same-origin path such as /api or an absolute https:// URL' }
  if (url.protocol !== 'https:') return 'VITE_API_URL must use https:// in a production build'
  if (LOCAL_HOSTS.has(url.hostname) || url.hostname.endsWith('.localhost')) return 'VITE_API_URL must not point to localhost / 127.0.0.1 in a production build'
  return null
}

// The browser URL for an application path the API returns, such as the evidence content route
// /api/portal/evidence/<id>/content: resolved on the API's origin (the portal itself in
// production, the local backend in development). Only root-relative paths are accepted, so an
// absolute or protocol-relative URL (for example an object-storage URL) is refused (null) and
// the browser only ever loads evidence through the authenticated application API.
export function applicationUrl(path: string, apiUrl: string, pageUrl: string): string | null {
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\')) return null
  try {
    const apiOrigin = new URL(apiUrl, pageUrl).origin
    const url = new URL(path, apiOrigin)
    return url.origin === apiOrigin ? url.href : null
  } catch { return null }
}
