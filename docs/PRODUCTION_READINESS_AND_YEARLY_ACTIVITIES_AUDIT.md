# Production readiness and yearly activities audit

> **Current status — 2026-11-04: NOT READY for production.** Evidence-based Yearly Activities
> replaces manual marking. Auth checkpoint `53731d5` is committed locally. Final gates: 930 backend
> passed / 68 skipped; 139 frontend passed; 17 PostgreSQL and 1 real Redis test passed. Runtime npm/pip
> audits are clear; 12 dev npm entries and 11 baseline Ruff findings remain. Actual 25/50/100/200-VU
> stages missed targets; 400/800 were not run. Bounded uploads and real PG/private-S3 restore passed.
> Source DB remains 0009; production infrastructure/browser UAT remain unverified. See the
> [complete current report](MASTER_IMPLEMENTATION_PRODUCTION_REPORT_20261104.md),
> [workflow](YEARLY_ACTIVITY_EVIDENCE_WORKFLOW.md) and
> [redacted measured results](audit-evidence/MASTER_RUNTIME_RESULTS.json).
>
> The historical sections below retain earlier evidence. Current results above supersede the old
> manual YES/NO workflow, uncommitted-auth status and earlier dependency/load/recovery findings.

## Historical evidence — 2026-11-03

Audit date: **2026-11-03 (Asia/Kolkata)**. Overall release decision: **NOT READY** pending the security/operational decisions and verification below. This report distinguishes implemented code, executed isolated tests, source review, and unavailable infrastructure evidence. No production service or data was changed; no commit, push, or deployment was performed.

## 1. Repository state and baseline

- Original branch: `dev1/phase5b-production-runbook`.
- Integrated base: `3aaec473db8c873779a4bd710d000d7095853772`.
- Yearly implementation branch at the original audit: `codex/feature-dynamic-yearly-activities`.
- P1-A current branch: `dev1/production-blockers-remediation`, phase base/unchanged HEAD
  `85250dfcd5c936182bdce9fdf18d685ddbc38b71`, which already commits the Yearly Activities work.
  The pasted P1-A brief's older uncommitted/base description was verified against Git and
  not assumed. No patch was reapplied and no branch was changed.
- `git fetch origin` succeeded after the initial sandbox Git-directory permission failure; origin's runbook branch contains exactly this base.
- Existing untracked `alc-growth-portal-ui.png` was preserved and excluded from the patch.
- Verified integrated milestones: `347076b` regional scores; `71e2026` production configuration; `6b1995a` authenticated evidence delivery; `906a0f7` backup/recovery tooling; `9e30d97` health/logging; `ff0066d` bounded S3 requests; `3aaec47` deployment runbook. No older milestone was substituted for HEAD.

## 2. Architecture and reusable services

The existing architecture is retained: React18/TypeScript/Vite/Tailwind/TanStack Query frontend; FastAPI and SQLAlchemy2 async services; PostgreSQL/Alembic; Redis login limiting; private S3/MinIO evidence. Role guards live in `app/dependencies.py`, live hierarchy clauses in `services/scope.py`, transactional audit attribution in `services/audit.py`. Yearly APIs reuse these guards, sessions, audit records, pagination, and React UI components. No separate authentication or scoring architecture was introduced.

## 3. Existing functionality inventory

ADMIN manages hierarchy, users, account provisioning/imports, review, partners, reports, Growth Challenge and audit history. DCU supervises its current SBUs/ALCs; SBU supervises its current ALCs; ALC creates draft activities, evidence, partners and tasks and views its performance. Activities follow submission/correction/resubmission/verification/rejection with evidence/review history. Verified activities feed official performance. Regional Top10 and All ALC Scores share aggregate metrics. Notifications are scoped to the signed-in user. Some Resources/Settings/Tasks screens are limited interfaces rather than complete workflow engines; README/REMAINING-WORK contain historical branch statements which should not be interpreted as the current Git baseline.

## 4. Authentication review

Source and existing tests cover Argon2id hashing; bounded asynchronous hash concurrency; role resolution from the database; HS256/type/expiry/UUID JWT validation; rotating hashed refresh tokens; atomic refresh consumption; inactive-account/hierarchy rejection; forced password changes; password reset/change; cookie configuration; CSRF and Redis fallback. Auth cookies are HttpOnly/SameSite=Lax and production configuration requires Secure. No auth token persistence in frontend localStorage/sessionStorage was found. Login/refresh rely on JSON/CORS/SameSite rather than the unsafe-operation CSRF dependency.

**P1-A resolved in implementation:** required JWT `sid` references a persistent active,
unexpired `auth_sessions` row joined into the existing user lookup. Current-family logout
invalidates copied access/refresh credentials; reset/change revoke all old families;
self-change issues replacement cookies. Completing forced change cannot revive a revoked
family. Consumed-refresh replay revokes its whole family, including successors.29 acceptance
tests,51 targeted auth tests in total, and10 migration/PostgreSQL race tests passed. See the
[phase report](SESSION_REVOCATION_REMEDIATION.md), [security evidence](SECURITY_AUDIT_EVIDENCE.md)
and final validation record for limits and full-suite results. No deployed fix is claimed;
0009/code rollout and cleanup scheduling remain deployment prerequisites.

## 5. Authorization matrix

[The individual matrix](AUTHORIZATION_ROUTE_MATRIX.md) lists **89 endpoints: 83 existing and six new** with role, scope and CSRF rules. Identity validation and resource authorization are reviewed separately.

| Yearly operation | ADMIN | DCU | SBU | ALC |
| --- | --- | --- | --- | --- |
| Registry/annual summary | Oversight | Own RCU | Own RCU + authorized history | Own RCU + own history |
| Run/ALC decisions read | All | Own DCU + currently scoped ALCs | Currently assigned ALCs | Self only |
| Register common identity | Denied | Allowed, own active RCU | Denied | Denied |
| Schedule/activate/mark/finalize | Denied | Own participant run only | Denied | Denied |
| Reopen/delete shared identity/results | No endpoint | No endpoint | No endpoint | No endpoint |

Out-of-scope runs/participants return404. Role-denied writes return403. Authenticated unsafe writes require CSRF. Regional leaderboard identity/aggregates intentionally remain shared across operational roles, preserving the existing contract; detailed ALC/evidence data does not inherit that broader visibility. Direct API tests exercise all four roles, cross-DCU access, scoped SBU/ALC reads, CSRF, locked results and existing hierarchy/role isolation regressions.

## 6. Security findings and remediation

| Finding / module | Severity | Evidence / impact | Remediation and validation | Deployment impact |
| --- | --- | --- | --- | --- |
| Credential input echoed in422, `app/main.py` | P2, fixed | Old Pydantic error handler echoed password/model inputs | Selective recursive credential redaction; malformed JSON raw input omitted; normal feedback retained; direct regression tests | Response hardening, no data migration |
| Copied JWT revocation, auth/dependencies/sessions | P1 resolved in P1-A | Prior direct API reproduction replaced by29 HTTP401/claims/retention/query acceptance cases | Persistent families, joined validation, scoped logout/global change/reset, replay containment; PostgreSQL races pass | Requires0009/coordinated new-code rollout and fresh sign-in; not deployed |
| Frontend upload Promise unresolved on HTML/malformed proxy response, `lib/api.ts` | P2, fixed | Uncaught JSON.parse in XHR completion | Controlled rejection fallback and regression test | User receives an error instead of hanging upload |
| React render recovery absent, `main.tsx` | P2, fixed | No root ErrorBoundary in baseline | Root ErrorBoundary with recovery test | Rendering failure can show recovery UI |
| Membership transfer/import bypass, hierarchy/import services | P1 integration risk, fixed | Direct FK import paths could bypass new snapshot policy | Shared membership serialization and pending-participation guard before reassignment; regression tests | Pending participation must finalize before move/import |
| Dependency advisories | P1/P2 unresolved | npm/pip actual scans, section21 | Review reachable advisories and upgrade in a separately tested dependency change | Do not treat current lock/environment as advisory-clean |
| Public service binding if dev Compose reused | P1 deployment misuse risk | `docker-compose.yml` publishes data-service ports without loopback | Follow production runbook/private network and verify real listeners/firewall | Existing dev setup retained; no evidence of deployed exposure |

No Critical/High BOLA exploit was reproduced in the reviewed existing route implementations. This is not an exhaustive penetration-test certification. SQL uses bound ORM expressions; React escaping is retained; evidence keys/paths are server-generated/contained, MIME/extension/signature are checked, and evidence is privately streamed after authorization. SSRF/user-directed fetch and unsafe HTML rendering entrypoints were not found in reviewed routes/components. Upload memory, dependency exploits and actual proxy/bucket policies still require the checks below. Review references: [OWASP API1](https://api-security.owasp.org/editions/2023/en/0xa1-broken-object-level-authorization/) and [ASVS](https://github.com/OWASP/ASVS/tree/v5.0.0/5.0); no ASVS certification is claimed.

## 7. Database structure and integrity

Three normalized additive tables:

1. `yearly_activity_definitions`: immutable canonical identity, RCU/year/code, name/description, introducing DCU and actor timestamps. Unique `(rcu_id, calendar_year, activity_code)`; year and normalized-code checks.
2. `yearly_activity_dcu_runs`: stable participation snapshot, one row per definition/DCU; independent schedule/instructions/lifecycle/activation/finalization attribution. Unique `(yearly_activity_id,dcu_id)`; schedule/state/finalized timestamp checks.
3. `yearly_activity_completions`: one historical ALC participation per definition; nullable Boolean decision, responsible run, snapshot SBU, marker/updater/timestamps. Unique `(definition,alc)` and `(run,alc)`. Composite `(run,definition)` foreign key prevents linking a completion to another activity's run.

History FKs use RESTRICT; actor FKs use SET NULL to preserve history on account deletion. Foreign-key lookup indexes and uniqueness support the application joins. No activity definitions or outcomes are seeded. Constraints supplement server-side scope/state validation; direct database writes by privileged operators remain outside API authorization.

P1-A separately adds `auth_sessions` (UUID primary key, indexed user/expiry, created/revoked
timestamps, expiry-order check) and nullable indexed `refresh_tokens.session_id` with CASCADE.
These are authentication state, not new Yearly business tables. Legacy credentials are
preserved but cannot authenticate without the new family binding.

## 8. Migration details and executed evidence

Revision `20261103_0008`, after `20261002_0007`, creates only the three tables. Existing tables/production data are untouched. Established current-metadata/checkfirst migration conventions were retained. `alembic/env.py` now accepts a supplied connection so tests can run inside a unique schema without altering public/application schemas.

Executed SQLite upgrade/downgrade/re-upgrade tests verify table/model checks/indexes, duplicate rejection, empty seeded state and unrelated-row preservation. Executed PostgreSQL17 test runs the actual full Alembic chain, downgrade to0007, re-upgrade, preserves a sentinel, inspects six completion FKs, and rejects mismatched run/definition identity. All PostgreSQL work used newly generated schemas in a newly created loopback-only disposable container; cleanup removed only those schemas. A proposed public-schema reset test was rejected by automatic approval review; the safe schema-isolated replacement was approved and passed. Legacy opt-in tests that reset public were not executed.

Downgrade drops the new tables and their history. It is tested only on disposable data; production rollback must prefer application rollback/forward repair with backups, and requires a reviewed retention decision. No production migration was applied. Because old migrations import live metadata, future changes must continue reviewing fresh-install versus incremental paths.

Current migration head is `20261103_0009`, after0008. New SQLite tests and a real PostgreSQL
full-chain downgrade/re-upgrade verify existing users/password hashes/legacy refresh rows
survive, new session linkage starts NULL for old rows, indexes/FK match, and Yearly tables
remain. No old sessions are silently adopted; legacy access/refresh credentials fail closed
after the new code starts. No migration on an existing development/production database was
run. See the P1-A report for coordinated rollout and security-sensitive rollback guidance.

## 9. Dynamic registry and deduplication

DCU searches by activity name/code for a selected year, reuses the common ID or registers a genuinely separate code. Codes normalize NFKC/whitespace/case/underscores into uppercase hyphen identifiers; the database uniqueness handles concurrent duplicate registration. Similar names are suggestions, not automatic equivalence. Shared identity is immutable from registration (stricter than locking only after reuse). ADMIN creation/approval is unnecessary and completion editing is denied. Activity count remains fully dynamic; tests use more than nine identities and multiple years.

## 10. DCU schedule, marking and finalization

Lifecycle: AVAILABLE → SCHEDULED → ACTIVE → MARKING → FINALIZED. Every participant's run exists from registry creation; dates/instructions belong to its DCU. Both dates must be ordered and within the activity year. Activation requires start reached; marking/finalization require the end reached. Activation snapshots active ALCs under active SBUs currently in that DCU. Default decisions are NULL/NOT MARKED, never NO. Marking accepts only strict true/false/null and audits every change. Finalization rejects any NULL participant and permanently locks ordinary editing. Empty-population runs still require schedule/lifecycle progression; no fictitious NO records are generated.

DCU interface has debounced ALC/code search, SBU filter,25-row pagination, YES/NO/NOT MARKED summary, individual immediate feedback, disabled in-flight writes and finalization confirmation. No large activity/evidence ORM graph is loaded for completion pages.

## 11. SBU/ALC visibility and historical membership policy

SBU rows use current authorized ALC scope; ALC gets only itself. Annual counts, applicable totals, percentages, schedule, marker time, local finalization and regional provisional/official state are available. ADMIN sees all participant runs, introducing DCU and pending finalizations, and can use audit history.

**Conservative organizational decision:** ALC and SBU reassignment, including CSV import reassignments, is rejected while any affected snapshotted run is unfinished. After finalization, moves preserve results and snapshot SBU/responsible DCU; current read access follows ALC identity and former DCU cannot edit locked history. Destination activation excludes an ALC already snapshotted for that identity. No automatic historical population rewrite, late-ALC enrollment, backfill, DCU waiver or unrestricted reopening is implemented. These need an explicit audited future policy if required.

## 12. Regional finalization barrier

Registration snapshots **every active DCU in the active RCU**, not only volunteers. Every persisted participant must finalize for eligibility; zero participants fail closed. AVAILABLE/SCHEDULED/ACTIVE/MARKING all block official scoring. Deactivation/reorganization never silently removes a requirement. A later-created/newly activated DCU joins future definitions, not historical snapshots; it receives no invented historical YES result. An inactive unfinished participant can block eligibility until legitimate reactivation/finalization or a future audited exception policy. ADMIN sees pending participant counts/runs. The same grouped `eligible_activity_ids` source drives official summaries and scoring.

## 13. Approved five-metric formula

Exactly five metrics each carry `Fraction(1,5)`: lifetime VERIFIED leads, lifetime VERIFIED admissions, lifetime VERIFIED activity count, lifetime ACTIVE partner rows, and selected-year distinct regionally eligible YES completions. NO/NULL/pending activities award zero. Eligibility is complete active hierarchy and any positive metric, including yearly-only achievement. All-zero ALCs remain excluded.

Existing percentile remains `100 × strictly-lower eligible values / (eligible_count−1)`, with constant nonzero population→100 and constant zero→0. Exact Fraction scores sort descending before ALC Code tie-breaking; display uses two-decimal half-up rounding. Selected year defaults to application date in Asia/Kolkata; first four definitions remain lifetime. No annual target or prior-year carryover exists.

## 14. Leaderboard compatibility

Both existing URLs retain `period=LIFETIME`, identity/metric/score keys, complete regional rank and generated time. Additive fields are `activity_year`, `eligible_yearly_activities`, and item `yearly_completed_count`; optional `?year=2000..2100`. Intentional score change: absent eligible yearly points, the old four components now sum to at most80, as required by five equal20% weights. Regression expectations were updated for this approved formula rather than removed/weakened.

One shared ranking drives both endpoints; filters remain display-only and preserve ranks/normalization. Frontend year selection synchronizes both views. Score reference is updated for ADMIN/DCU/SBU only; ALC Dashboard/Performance still have no score-reference component. Existing leaderboard population is preserved across the configured hierarchy. The deployment is intended for RCU Pune; if multiple independent RCUs are introduced, baseline leaderboard region selection/normalization must be explicitly designed before claiming independent region-specific rankings.

## 15. Query performance and measurements

Lifetime activity/partner pre-aggregation is retained. A grouped annual completion subquery joins the shared eligibility barrier and counts distinct definition IDs by ALC/RCU; history from a different current RCU cannot contribute annual leaderboard points. Scores load one aggregate row per ALC, not completion objects. A single SQL statement also preserves the eligible annual population when the score population is empty. Statement-count tests verify constant query count/no activity hydration. Completion lists group annual totals for only the paged ALC IDs; no per-row requests/N+1 counting.

[Raw synthetic experiment](audit-evidence/yearly-query-measurements.json): PostgreSQL17, local Docker,800 ALCs/4000 verified activities/1600 partners/8000 annual completions/10 identities/4 DCUs/20 SBUs;12 serial scoring samples. EXPLAIN ANALYZE execution **22.686ms**,312 shared-hit blocks; first scoring sample **107.02ms**, warm median **68.64ms**, warm maximum **128.95ms**. Timing includes Python exact scoring in the scoring samples. No speculative caching/index expansion was made from unrepresentative production assumptions. These are microbenchmarks, not API latency SLAs or concurrent-user evidence. Repeat with `python -m scripts.measure_yearly_queries` using the explicit isolated loopback environment described in its docstring.

## 16. Concurrency and transaction safety

Run-row FOR UPDATE serializes marking/finalization; snapshot uniqueness and canonical code uniqueness enforce duplicate protection. Snapshot activation/reassignment/import serialize membership changes with ordered regional-root locks. Finalization/audit and marking/audit commit together. Request session closure rolls back failed requests. Actual PostgreSQL concurrency tests verify a waiting mark returns409 after finalization commits, the barrier changes only after commit, and parallel YES/NO updates preserve one row/two attributable audits. Lock behavior follows [PostgreSQL explicit locking](https://www.postgresql.org/docs/17/explicit-locking.html). Contention throughput, concurrent registration/import/login/report workloads at realistic infrastructure scale remain unmeasured; root membership locks are deliberately conservative for rare hierarchy/schedule operations.

P1-A auth mutations use per-user→family→refresh row locks, not regional/global locks. Six
real PostgreSQL tests cover refresh/logout/reset/change races, simultaneous refresh replay,
an independent engine observing revocation and stale verified login versus reset. Ordinary
protected-request validation adds zero SQL round trips and holds no long auth row lock.
An already validated in-flight business request can finish; validation after revocation
commit rejects. These are correctness tests, not representative throughput evidence.

## 17. PostgreSQL configuration and connections

Existing per-process pool size10/overflow20, pre-ping and hidden SQL parameters are retained. Session context releases/rolls back connections; streaming report cursors close in finally. Default pool timeout is30s; no application query statement timeout/checkout-wait metric is configured. Deployment must budget `workers × 30` plus backup/migration/monitoring reserve against PostgreSQL max_connections, configure safe role/server timeouts, and measure checkout wait before tuning. Actual production server/worker resources, TLS, limits and plans are NOT VERIFIED.

## 18. Redis behavior

Existing atomic Lua counters and bounded HMAC-keyed process-local fallback remain. Mocked outage/recovery regression coverage is executed in the normal suite. Multi-worker fallback is per process, not a distributed global limit. Readiness reports Redis outage503 even when fallback logins work. Real Redis failure/recovery, eviction and failover under load remain NOT VERIFIED.

An additional actual Redis7 integration test passed in0.27s against a newly created loopback-only container:50 concurrent reservations allowed exactly8;50 hits allowed exactly20; TTL retention/repair, refund floor and clear were verified. Only unique synthetic test keys were used. The test container was verified and removed afterward. This does not replace a real outage/failover experiment.

## 19. Evidence storage

Phase5B authenticated private evidence streaming is integrated; no storage key/bucket/endpoint is exposed to browsers. Read/open/disconnect/timeouts have existing regression tests. S3 connect/read/retry bounds are retained. Production startup rejects local storage. Real bucket privacy, IAM, TLS, encryption, object restore and throughput are NOT VERIFIED. Upload validation can accumulate up to default100MiB/request; enforce reverse-proxy body/timeouts and measure simultaneous upload memory/temp disk. Write outage may return generic500; orphan uploaded objects after a later transaction failure remain a P2 reconciliation concern.

## 20. Frontend reliability/accessibility

Existing RCU Pune design and responsive top navigation are retained. Role-aware navigation, lazy routing, auth refresh single-flight, cookies/CSRF API client and Query staleTime30s/retry1 are retained. New forms label controls, expose loading/error/empty/status feedback, disable conflicting submissions and page/filter row data. A root ErrorBoundary handles render failures; malformed upload responses reject predictably. Executed tests are jsdom/mocked-API components/client tests, not live browser/full-stack E2E. Physical desktop/mobile layout, assistive technology and deployment CSP remain NOT VERIFIED. Recharts zero-size jsdom warnings remain visible, not suppressed.

## 21. Dependency vulnerabilities

Actual advisory scans were run on2026-11-03, not inferred from version names. [npm full](audit-evidence/npm-audit.json):21 affected packages (16 moderate,3 high,2 critical); most are dev tooling including Vitest/tinypool/Vite/source-map-js. [npm production-only](audit-evidence/npm-audit-production.json): one high Zod advisory; resolving reported version requires a major Zod update. Browser-form usage does not establish server exploitability; runtime exposure needs a targeted review. Never expose Vite/Vitest test/dev servers publicly.

[pip-audit](audit-evidence/pip-audit.json):16 reported advisory entries in5 installed packages: certifi2026.7.22, dnspython2.8.0, PyJWT2.14.0, pytest8.4.2 and setuptools65.5.0. Several entries duplicate advisory IDs; totals are scanner counts, not16 distinct proven portal exploits. PyJWT findings concern JWK/JWKS paths while this portal uses HS256; pytest/setuptools are tooling. No blind dependency upgrades were performed. Backend ranges are not a reproducible runtime lock, and installed setuptools is older than declared build-system minimum. Review/upgrade/re-audit in a tested dependency maintenance patch before launch.

## 22. Capacity and load environment/results

Registered-user target800 is a population requirement, not verified concurrency. Guarded k6 scenarios/instructions live in [tests/load](../tests/load/README.md):25/50/100/200 VUs, explicit400/800 stress opt-in, synthetic accounts, read-heavy workflows/think times, optional scoped marking/verification, reports/logout, timeouts/p50/p95/p99/error thresholds. No load was sent to any existing development/production database. k6 was not installed/executed here. **Maximum concurrent users tested: NOT VERIFIED**; two competing database transactions were tested only for lock correctness. RPS/API p50/p95/p99, error rate/timeout rate, CPU/RSS/network, pool waits/connections and800-concurrent-user capacity all remain NOT VERIFIED. Initial p95<500ms APIs/<1s dashboard and<1% server error are engineering targets, not SLAs.

## 23. Failure/recovery tests

Normal suite includes invalid/expired tokens, auth separation/forced change/inactive hierarchy, CSRF/invalid input, refresh races, Redis limiter fallback, database/readiness failure mocks, storage timeout/missing-object/stream cleanup, upload validation, generic error redaction and request logging. New tests cover locked/future/invalid transitions, NULL/NO distinctions, duplicate identities/decisions, scope, year rollover and pending barrier. Real dependency stop/start, network partitions, client disconnects at production proxy, concurrent report/login refresh under representative load and process restart behavior remain staging work. Mock successes must not be represented as real infrastructure recovery evidence.

## 24. Backup/restore and disaster recovery

Phase5B4 scripts/manifests/checksum verification/retention/restore documentation are present. Existing unit/script checks are part of backend regression. A previous documented drill is not a fresh restore proof for this release. Require a new isolated PostgreSQL+object-store restore including the three new tables, off-server encrypted copies, access review, RPO/RTO measurements and reconciliation. No backup or production object was deleted/restored here. Current production restore readiness is NOT VERIFIED.

## 25. Monitoring and logging

Liveness remains independent of dependencies; readiness performs bounded DB/Redis/storage checks and returns minimal503 details. Structured production request logs generate server request IDs, omit query strings/bodies/cookies/secrets, hide SQL parameters and quiet third-party debug logging. Audit metadata records participant creation, schedule/lifecycle, completion changes, finalization and membership moves. Existing Admin audit view remains available. Production alert routing, dashboard/metrics, disk/log retention and alert response are NOT VERIFIED; pool-wait measurement remains absent.

## 26. Production configuration

APP_ENV validation, secret/default rejection, Secure cookies, exact HTTPS CORS, trusted-proxy CIDRs, disabled production OpenAPI/docs, private storage requirement and VITE production URL checks are integrated/tested. Uvicorn must use `--no-proxy-headers` behind the documented private reverse proxy. The production frontend build used `VITE_API_URL=/api`; plain build correctly rejected an inherited development HTTP URL. No secrets were printed or packaged. No environment file, credentials or infrastructure configuration was modified.

## 27. Deployment prerequisites and rollback

Before any separately authorized deployment: resolve/accept P1 findings, review the patch and membership/late-participant policy, test dependencies, snapshot DB/storage, rehearse schema upgrade on representative restored data, perform isolated load/soak and failure recovery, set worker/DB budgets and proxy body/timeouts/HTTPS, verify private bucket/firewall, install monitoring, and rehearse restore/application rollback. Apply migration once as a release step, not from each worker. Keep new schema/history through application rollback where possible; do not casually downgrade0008. This task intentionally stops before deployment/commit/push.

For P1-A, coordinate/drain all old auth workers, apply0009 once and run only the new auth
code, require fresh sign-in, verify revocation and schedule bounded expiry cleanup. Mixed
auth versions are unsupported. Family lifetime is absolute configured refresh days; replay
can require sign-in after competing refreshes. Rolling back old auth code restores the
resolved vulnerability; retain schema/forward-fix or use a reviewed forced-reauthentication
plan. No production cleanup, signing-key change or deployment was performed here.

## 28. Outstanding blockers

1. Advisory triage and tested dependency remediation/re-audit.
2. Representative isolated capacity/load and live failure-recovery tests.
3. Fresh full DB/object-store restore drill including yearly history and new auth state.
4. Verified production HTTPS/proxy/firewall/IAM/secrets/worker/connection/monitoring configuration.
5. Organizational acceptance of blocked unfinished transfers, activation population cutoff, and inactive/late DCU exception handling; no silent waiver tool exists.
6. Live browser/full-stack/mobile/accessibility acceptance and existing baseline lint debt.

The copied-JWT finding is resolved by P1-A's tested implementation, not an active unresolved
blocker. Deployment prerequisites for that fix are separate from the remaining blockers.

## 29. Risk register

| Priority | Risk | Current disposition |
| --- | --- | --- |
| P0 | Verified critical exploitable source defect | None established by this review; no blanket security guarantee |
| P1 | Advisories including critical dev tooling/high browser library | Recorded, no reachability certification or breaking upgrade |
| P1 | Capacity/connection/upload memory limits and real recovery unverified | Isolated load/staging prerequisites |
| P1 | Restore/private infrastructure/monitoring not verified | Operational release blockers |
| P1 | Dev Compose reused publicly | Prevent through deployment verification; no deployed exposure observed |
| P2 | Inactive participant blocks barrier / blocked unfinished transfer | Explicit safe policy; future audited exception/correction needed if required |
| P2 | Independent multi-RCU ranking not designed | Existing single-regional population preserved; revisit before multi-region expansion |
| P2 | Storage orphan reconciliation, unpaginated legacy small lists, report cursor occupancy | Documented, unchanged to bound scope |
| P2 | No live UI/assistive-tech/full-stack E2E | Component tests/build pass, acceptance unverified |
| P3 | Baseline Ruff debt and historical README status text | Unrelated existing files preserved; follow-up |

## 30. Actual test results

P1-A current validation:29 revocation acceptance +22 auth/password regression cases passed
(51 combined);4 new migration +6 real PostgreSQL race/persistence cases passed (10 combined).
No extra authenticated-request round trip was added. Changed app/tests/maintenance/migration
Ruff passes; full Ruff still has11 unchanged script findings. **P1-A complete backend:
902 passed,67 skipped,1 warning in612.32s**; all source changes preceded the run. Raw results
and skip reasons are in the P1-A validation record. Frontend is
unchanged and was not rerun. The paragraph below records historical Yearly-phase evidence,
including the old vulnerability reproductions which P1-A has replaced.

Final consolidated results are recorded in [validation evidence](audit-evidence/VALIDATION_RESULTS.md). **Complete backend run:869 passed,60 skipped in572.48s.** Existing skip conditions cover Windows/POSIX backup/permission/timezone behavior, absent optional PostgreSQL/Redis environment variables and unavailable real-data fixtures; skips were not added to bypass failures. This run began before the final import/per-RCU corrections; the final affected60-test regression verified those source changes and additional cases. Final combined migration/concurrency check:5 passed (including actual PostgreSQL); actual Redis check:1 passed. Frontend:136 passed, lint/typecheck/build passed. All app/tests/new performance-script Ruff checks pass; full repository Ruff retains11 findings in unchanged provisioning/demo scripts. Targeted groups overlap the full suite and must not be summed. Tests reproducing JWT weakness do not count as security acceptance. Raw backend/PostgreSQL/lint output is included in `audit-evidence`.

## 31. Files and patch

`audit-evidence/CHANGED_FILES.txt` records the historical Yearly phase manifest; its recovery
patch was not reapplied. Current P1-A changes are listed separately in
`audit-evidence/P1A_CHANGED_FILES.txt` and the phase report. Existing Yearly business/scoring/
frontend files are preserved; only migration-head/auth-test integration is mechanical.

## 32. Final Git state

P1-A made no commit/push/deployment/branch change/reset/stash. HEAD remains phase base
`85250dfcd5c936182bdce9fdf18d685ddbc38b71` on `dev1/production-blockers-remediation`.
The already-committed Yearly implementation and untracked screenshot are preserved.
P1-A changes remain unstaged. Final status/checks and phase-only manifest are captured
separately under `audit-evidence/P1A_*`; historical Yearly status/patch evidence is retained.
