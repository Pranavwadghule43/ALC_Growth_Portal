import { describe, expect, it } from 'vitest'
import { DEV_API_URL, PRODUCTION_API_URL, productionApiUrlProblem, resolveApiUrl } from './apiUrl'

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
