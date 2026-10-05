import { resolveApiUrl } from './apiUrl'

const API_URL = resolveApiUrl(import.meta.env.VITE_API_URL, import.meta.env.DEV)

function getCookie(name: string) {
  return document.cookie.split('; ').find((row) => row.startsWith(`${name}=`))?.split('=')[1]
}

export class ApiError extends Error { constructor(message: string, public status: number) { super(message) } }

// The sign-in flow itself never triggers a refresh (this also stops /auth/refresh recursing).
const NO_REFRESH_PATHS = ['/auth/login', '/auth/admin-login', '/auth/refresh', '/auth/logout']
// /auth/me may refresh, but if the session is gone the route guards choose the sign-in page.
const NO_REDIRECT_PATHS = ['/auth/me']
const inAdmin = () => window.location.pathname.startsWith('/admin')
function goTo(path: string) { if (window.location.pathname !== path) window.location.assign(path) }
const signIn = () => goTo(inAdmin() ? '/admin/login' : '/login')

// Single-flight refresh. When the access token expires, several requests can get 401 at once;
// they all share ONE POST /auth/refresh instead of each rotating the refresh token (the server
// accepts a refresh token only once, so parallel refreshes would sign the user out).
let refreshInFlight: Promise<boolean> | null = null
// Counts successful refreshes, so a request that was sent before a refresh finished but got
// its 401 afterwards retries with the new cookie instead of refreshing again.
let refreshGeneration = 0

export function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = fetch(`${API_URL}/auth/refresh`, { method: 'POST', credentials: 'include' })
      .then(response => { if (response.ok) refreshGeneration += 1; return response.ok }, () => false)
      .finally(() => { refreshInFlight = null })
  }
  return refreshInFlight
}

async function request<T>(path: string, options: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(options.headers)
  if (!(options.body instanceof FormData) && options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const csrf = getCookie('csrf_token')
  if (csrf && options.method && !['GET', 'HEAD'].includes(options.method)) headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  const generation = refreshGeneration
  const response = await fetch(`${API_URL}${path}`, { ...options, headers, credentials: 'include' })
  // Retry at most once (``retry`` is false on the second attempt), so there is no refresh loop.
  if (response.status === 401 && retry && !NO_REFRESH_PATHS.includes(path)) {
    const refreshed = generation !== refreshGeneration || await refreshSession()
    if (refreshed) return request<T>(path, options, false)
    // The session has ended (signed out elsewhere or password reset): send the user to sign in again.
    if (!NO_REDIRECT_PATHS.includes(path)) signIn()
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail = body.detail ?? body.error?.message ?? 'Request failed'
    // The account now requires a password change (e.g. reset by an SBU/admin): open the profile page.
    if (response.status === 403 && detail === 'Password change required') goTo(inAdmin() ? '/admin/profile' : '/portal/profile')
    throw new ApiError(detail, response.status)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) => request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  downloadUrl: (path: string) => `${API_URL}${path}`,
  uploadEvidence: (path: string, file: File, onProgress: (percent: number) => void, retry = true): Promise<unknown> => new Promise((resolve, reject) => {
    const body = new FormData()
    body.append('files', file)
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_URL}${path}`)
    xhr.withCredentials = true
    const csrf = getCookie('csrf_token')
    if (csrf) xhr.setRequestHeader('X-CSRF-Token', decodeURIComponent(csrf))
    xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100)) }
    xhr.onload = () => {
      const result = JSON.parse(xhr.responseText || '{}')
      if (xhr.status >= 200 && xhr.status < 300) resolve(result)
      // Expired access token: share the single refresh, then upload once more.
      else if (xhr.status === 401 && retry) refreshSession().then(ok => {
        if (ok) api.uploadEvidence(path, file, onProgress, false).then(resolve, reject)
        else { signIn(); reject(new ApiError('Session expired', 401)) }
      })
      else reject(new ApiError(result.detail ?? result.error?.message ?? 'Unable to upload evidence', xhr.status))
    }
    xhr.onerror = () => reject(new ApiError('Unable to upload evidence', 0))
    xhr.send(body)
  }),
}