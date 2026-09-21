const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api'

function getCookie(name: string) {
  return document.cookie.split('; ').find((row) => row.startsWith(`${name}=`))?.split('=')[1]
}

export class ApiError extends Error { constructor(message: string, public status: number) { super(message) } }

async function request<T>(path: string, options: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(options.headers)
  if (!(options.body instanceof FormData) && options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const csrf = getCookie('csrf_token')
  if (csrf && options.method && !['GET', 'HEAD'].includes(options.method)) headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  const response = await fetch(`${API_URL}${path}`, { ...options, headers, credentials: 'include' })
  if (response.status === 401 && retry && path !== '/auth/login' && path !== '/auth/admin-login' && path !== '/auth/refresh') {
    const refreshed = await fetch(`${API_URL}/auth/refresh`, { method: 'POST', credentials: 'include' })
    if (refreshed.ok) return request<T>(path, options, false)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new ApiError(body.detail ?? body.error?.message ?? 'Request failed', response.status)
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
  uploadEvidence: (path: string, file: File, onProgress: (percent: number) => void): Promise<unknown> => new Promise((resolve, reject) => {
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
      else reject(new ApiError(result.detail ?? result.error?.message ?? 'Unable to upload evidence', xhr.status))
    }
    xhr.onerror = () => reject(new ApiError('Unable to upload evidence', 0))
    xhr.send(body)
  }),
}
