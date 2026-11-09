import http from 'k6/http'
import { check, sleep } from 'k6'
import { Counter, Trend } from 'k6/metrics'

// Every run requires an explicit operator assertion of an isolated synthetic environment.
// No production hostname or credentials are embedded in this file.
const base = (__ENV.ALC_LOAD_BASE_URL || '').replace(/\/$/, '')
const hostname = /^https?:\/\/([^/:]+)/.exec(base)?.[1]
if (__ENV.ALC_LOAD_ISOLATED !== '1' || !hostname || /[?#@]/.test(base)
  || __ENV.ALC_LOAD_CONFIRM_HOST !== hostname) {
  throw new Error('Require isolated environment, exact confirmed hostname, and safe base URL')
}
let users
try {
  users = JSON.parse(open(__ENV.ALC_LOAD_ACCOUNTS_FILE || './synthetic-accounts.json'))
} catch {
  // JSON parser diagnostics can quote the input, which contains synthetic passwords.
  throw new Error('Unable to read a valid synthetic-account JSON file')
}
if (!Array.isArray(users) || !users.length || users.some(u =>
  !['ADMIN', 'DCU', 'SBU', 'ALC'].includes(u.role) || !u.identifier || !u.password)) {
  throw new Error('Supply synthetic accounts with role, identifier, and password')
}
const vus = Number(__ENV.ALC_LOAD_VUS || 25)
if (![25, 50, 100, 200, 400, 800].includes(vus)
  || (vus >= 400 && __ENV.ALC_LOAD_ALLOW_STRESS !== '1')) {
  throw new Error('Allowed stages: 25/50/100/200; 400/800 require ALC_LOAD_ALLOW_STRESS=1')
}
const writeEnabled = __ENV.ALC_LOAD_SYNTHETIC_WRITES === '1'
const steadyState = __ENV.ALC_LOAD_STEADY_STATE === '1'
const timeoutCount = new Counter('portal_timeouts')
const transportCount = new Counter('portal_transport_errors')
const throttled = new Counter('portal_429')
const serverErrors = new Counter('portal_5xx')
const endpointTrends = {
  admin_dashboard: new Trend('endpoint_admin_dashboard_ms', true),
  portal_dashboard: new Trend('endpoint_portal_dashboard_ms', true),
  yearly_definitions: new Trend('endpoint_yearly_definitions_ms', true),
  yearly_submissions: new Trend('endpoint_yearly_submissions_ms', true),
  regional_leaderboard: new Trend('endpoint_regional_leaderboard_ms', true),
}
export const options = {
  scenarios: { users: { executor: 'ramping-vus', startVUs: 0, stages: [
    { duration: __ENV.ALC_LOAD_RAMP || '1m', target: vus },
    { duration: __ENV.ALC_LOAD_DURATION || '5m', target: vus },
    { duration: __ENV.ALC_LOAD_DOWN || '30s', target: 0 },
  ], gracefulRampDown: '30s' } },
  summaryTrendStats: ['avg', 'min', 'med', 'p(50)', 'p(90)', 'p(95)', 'p(99)', 'max'],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{kind:ordinary}': ['p(95)<500'],
    'http_req_duration{kind:dashboard}': ['p(95)<1000'],
    checks: ['rate>0.99'],
    endpoint_admin_dashboard_ms: ['p(95)<1000'],
    endpoint_portal_dashboard_ms: ['p(95)<1000'],
    endpoint_yearly_definitions_ms: ['p(95)<500'],
    endpoint_yearly_submissions_ms: ['p(95)<500'],
    endpoint_regional_leaderboard_ms: ['p(95)<500'],
  },
}

function csrf() {
  return http.cookieJar().cookiesForURL(base).csrf_token?.[0]
}
function call(method, path, body, kind = 'ordinary') {
  const headers = { 'Content-Type': 'application/json' }
  if (csrf()) headers['X-CSRF-Token'] = csrf()
  const response = http.request(method, base + '/api' + path,
    body === undefined ? null : JSON.stringify(body), {
      headers, timeout: '10s', tags: { name: method + ' ' + path.replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/g, ':id'), kind },
      responseType: 'text',
    })
  if (response.status === 0) transportCount.add(1)
  if (response.error_code === 1050) timeoutCount.add(1)
  if (response.status === 429) throttled.add(1)
  if (response.status >= 500) serverErrors.add(1)
  const endpoint = {
    'GET /admin/dashboard': 'admin_dashboard',
    'GET /portal/dashboard': 'portal_dashboard',
    'GET /yearly-activities/definitions': 'yearly_definitions',
    'GET /yearly-activities/submissions': 'yearly_submissions',
    'GET /leaderboard/region-scores': 'regional_leaderboard',
  }[method + ' ' + path.split('?')[0]]
  if (endpoint) endpointTrends[endpoint].add(response.timings.duration)
  check(response, { 'API returned success': r => r.status >= 200 && r.status < 300 })
  return response
}

function installSyntheticSession(user) {
  if (!user.access_token || !user.csrf_token) {
    throw new Error('Steady-state mode requires synthetic access_token and csrf_token')
  }
  const jar = http.cookieJar()
  jar.set(base, 'access_token', user.access_token, { path: '/' })
  jar.set(base, 'csrf_token', user.csrf_token, { path: '/' })
}

export default function () {
  const user = users[(__VU - 1) % users.length]
  if (steadyState) {
    installSyntheticSession(user)
  } else {
    const login = call('POST', user.role === 'ADMIN' ? '/auth/admin-login' : '/auth/login', {
      identifier: user.identifier, password: user.password, role: user.role,
    }, 'login')
    if (login.status !== 200) { sleep(5); return }
  }
  sleep(1 + Math.random() * 2)
  const prefix = user.role === 'ADMIN' ? '/admin' : '/portal'
  const year = __ENV.ALC_LOAD_YEAR || '2026'
  // Each iteration is a session with realistic think time and shared read-heavy paths.
  for (let screen = 0; screen < 5; screen++) {
    call('GET', prefix + '/dashboard', undefined, 'dashboard')
    sleep(1 + Math.random() * 2)
    call('GET', '/yearly-activities/definitions?year=' + year + '&page=1&page_size=25')
    if (user.run_id) call('GET', '/yearly-activities/submissions?year=' + year + '&run_id=' + user.run_id + '&page=1&page_size=25')
    let submission
    if (user.submission_id) {
      const response = call('GET', '/yearly-activities/submissions/' + user.submission_id)
      if (response.status === 200) submission = response.json()
    }
    // The real dashboard derives Top 10 from the cached complete regional ranking.
    call('GET', '/leaderboard/region-scores?year=' + year)
    if (user.role === 'SBU' || user.role === 'DCU') {
      call('GET', '/portal/verification?page=1&page_size=25')
      call('GET', '/portal/alcs?page=1&page_size=25')
    }
    if (user.role === 'ADMIN') call('GET', '/admin/activities?page=1&page_size=25')
    // Optional write fixtures MUST belong to disposable synthetic data. Never finalize here.
    if (writeEnabled && user.role === 'ALC' && submission?.status === 'DRAFT' && screen === 0) {
      call('PATCH', '/yearly-activities/submissions/' + user.submission_id, {
        completion_date: submission.completion_date, remarks: 'Synthetic isolated load draft details',
      })
    }
    if (writeEnabled && user.role === 'DCU' && user.review_submission_id && screen === 0) {
      const detail = call('GET', '/yearly-activities/submissions/' + user.review_submission_id)
      if (detail.status === 200 && ['SUBMITTED', 'RESUBMITTED'].includes(detail.json('status'))) {
        call('POST', '/yearly-activities/submissions/' + user.review_submission_id + '/review', {
          revision: detail.json('revision'), decision: 'VERIFY', remarks: 'Synthetic load review',
        })
      }
    }
    if (screen === 4) call('GET', prefix + '/reports/activities.csv', undefined, 'report')
    sleep(2 + Math.random() * 3)
  }
  if (!steadyState) call('POST', '/auth/logout')
  sleep(3 + Math.random() * 3)
}
