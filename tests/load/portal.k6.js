import http from 'k6/http'
import { check, sleep } from 'k6'
import { Counter } from 'k6/metrics'

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
const timeoutCount = new Counter('portal_timeouts')
export const options = {
  scenarios: { users: { executor: 'ramping-vus', startVUs: 0, stages: [
    { duration: '1m', target: vus },
    { duration: __ENV.ALC_LOAD_DURATION || '5m', target: vus },
    { duration: '30s', target: 0 },
  ], gracefulRampDown: '30s' } },
  summaryTrendStats: ['avg', 'min', 'med', 'p(50)', 'p(95)', 'p(99)', 'max'],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{kind:ordinary}': ['p(95)<500'],
    'http_req_duration{kind:dashboard}': ['p(95)<1000'],
    checks: ['rate>0.99'],
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
  if (response.status === 0) timeoutCount.add(1)
  check(response, { 'API returned success': r => r.status >= 200 && r.status < 300 })
  return response
}

export default function () {
  const user = users[(__VU - 1) % users.length]
  const login = call('POST', user.role === 'ADMIN' ? '/auth/admin-login' : '/auth/login', {
    identifier: user.identifier, password: user.password,
  }, 'login')
  if (login.status !== 200) { sleep(5); return }
  sleep(1 + Math.random() * 2)
  const prefix = user.role === 'ADMIN' ? '/admin' : '/portal'
  const year = __ENV.ALC_LOAD_YEAR || '2026'
  // Each iteration is a session with realistic think time and shared read-heavy paths.
  for (let screen = 0; screen < 5; screen++) {
    call('GET', prefix + '/dashboard', undefined, 'dashboard')
    sleep(1 + Math.random() * 2)
    call('GET', '/yearly-activities/definitions?year=' + year + '&page=1&page_size=25')
    if (user.run_id) call('GET', '/yearly-activities/runs/' + user.run_id + '/completions?page=1&page_size=25')
    call('GET', '/leaderboard/region-top10?year=' + year)
    if (screen % 2 === 0) call('GET', '/leaderboard/region-scores?year=' + year)
    if (user.role === 'SBU' || user.role === 'DCU') {
      call('GET', '/portal/verification?page=1&page_size=25')
      call('GET', '/portal/alcs?page=1&page_size=25')
    }
    if (user.role === 'ADMIN') call('GET', '/admin/activities?page=1&page_size=25')
    // Optional write fixtures MUST belong to disposable synthetic data. Never finalize here.
    if (writeEnabled && user.role === 'DCU' && user.run_id && user.alc_id) {
      call('PATCH', '/yearly-activities/runs/' + user.run_id + '/completions/' + user.alc_id, {
        completed: screen % 2 === 0,
      })
    }
    if (writeEnabled && ['DCU', 'SBU'].includes(user.role) && user.verification_activity_id
      && screen === 0) {
      call('POST', '/portal/activities/' + user.verification_activity_id + '/verify', {})
    }
    if (screen === 4) call('GET', prefix + '/reports/activities.csv', undefined, 'report')
    sleep(2 + Math.random() * 3)
  }
  call('POST', '/auth/logout')
  sleep(3 + Math.random() * 3)
}
