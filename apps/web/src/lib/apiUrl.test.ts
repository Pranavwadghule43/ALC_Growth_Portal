import { describe, expect, it } from 'vitest'
import { applicationUrl, DEV_API_URL, PRODUCTION_API_URL, productionApiUrlProblem, resolveApiUrl } from './apiUrl'

// The API base URL: development keeps the local backend, a production build is same-origin
// (/api) and never silently points at localhost.
describe('resolveApiUrl', () => {
  it('uses VITE_API_URL when it is set', () => {
    expect(resolveApiUrl('/api', false)).toBe('/api')
    expect(resolveApiUrl('http://127.0.0.1:8000/api', true)).toBe('http://127.0.0.1:8000/api')
  })

  it('falls back to the local backend in development only', () => {
    expect(resolveApiUrl(undefined, true)).toBe(DEV_API_URL)
    expect(resolveApiUrl('  ', true)).toBe(DEV_API_URL)
  })

  it('falls back to same-origin /api in a production build', () => {
    expect(resolveApiUrl(undefined, false)).toBe(PRODUCTION_API_URL)
    expect(resolveApiUrl('', false)).toBe('/api')
  })
})

describe('productionApiUrlProblem', () => {
  it.each([undefined, '', '/api', 'https://portal.example.org/api'])('accepts %s', value => {
    expect(productionApiUrlProblem(value)).toBeNull()
  })

  it.each([
    ['http://localhost:8000/api', 'https://'],
    ['https://localhost/api', 'localhost'],
    ['https://127.0.0.1/api', 'localhost'],
    ['https://[::1]/api', 'localhost'],
    ['https://app.localhost/api', 'localhost'],
    ['http://portal.example.org/api', 'https://'],
    ['//portal.example.org/api', 'protocol-relative'],
    ['api', 'same-origin path'],
  ])('rejects %s', (value, reason) => {
    expect(productionApiUrlProblem(value)).toContain(reason)
  })
})

// Evidence (and any other application URL the API returns) always loads from the API's own
// origin; storage or other absolute URLs are refused.
describe('applicationUrl', () => {
  const page = 'https://portal.example/portal/activities/1'
  const path = '/api/portal/evidence/e1/content'

  it('resolves on the portal origin in production (same-origin /api)', () => {
    expect(applicationUrl(path, '/api', page)).toBe('https://portal.example/api/portal/evidence/e1/content')
  })

  it('resolves on the local backend in development', () => {
    expect(applicationUrl(path, DEV_API_URL, 'http://localhost:5173/portal/activities/1')).toBe('http://localhost:8000/api/portal/evidence/e1/content')
  })

  it('refuses absolute, protocol-relative and storage URLs', () => {
    for (const bad of [
      'http://127.0.0.1:9000/alc-evidence/evidence/a.pdf',
      'https://s3.amazonaws.com/alc-evidence/a.pdf?X-Amz-Signature=x',
      '//evil.example/a.pdf',
      '/\\evil.example/a.pdf',
      'javascript:alert(1)',
      'api/portal/evidence/e1/content',
      '',
    ]) expect(applicationUrl(bad, '/api', page)).toBeNull()
  })
})
