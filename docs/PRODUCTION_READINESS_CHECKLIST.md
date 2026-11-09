# Production readiness checklist

> **Current status — 2026-11-07: READY WITH CONDITIONS.** Fresh-install migration compatibility
> blockers in 0011/0012 are repaired; isolated PostgreSQL 13 migration/concurrency tests, frontend
> tests/build/lint, Ruff and compilation pass. The complete Python 3.11 backend suite and production
> Python vulnerability audit now pass. Production deployment is still blocked on restored-production
> staging rehearsal, infrastructure/capacity verification and explicit human approval. See the
> [current stabilization report](PRE_DEPLOYMENT_STABILIZATION_20261107.md),
> [workflow](YEARLY_ACTIVITY_EVIDENCE_WORKFLOW.md) and historical measured evidence below.
>
> The historical sections below retain earlier evidence. Current results above supersede the old
> manual YES/NO workflow, uncommitted-auth status and earlier dependency/load/recovery findings.

## Current backend and Python dependency gates — 2026-11-07

| Gate | Status | Evidence |
| --- | --- | --- |
| Complete backend suite on Python 3.11 | **PASS** | Managed CPython 3.11.15, pytest 9.2.0: 963 passed, 69 skipped, 0 failed in 793.79s |
| PostgreSQL-specific backend coverage | **PASS** | 57 passed, 0 failed across 9 modules; only `alc_yearly_automated_test_20261107` used |
| Complete backend Ruff gate | **PASS** | `.venv311-clean/Scripts/ruff.exe check app tests`: all checks passed |
| Production Python dependency resolution | **PASS** | 45 pinned runtime packages from `constraints-runtime.txt`; `uv pip check` compatible |
| Python dependency vulnerability audit | **PASS** | `pip-audit 2.10.1`: no known vulnerabilities; no ignored findings |

The audit used the separately resolved runtime environment rather than the development environment.
No critical or high Python vulnerability remains. Constraints are exact-pinned but not hash-pinned;
adding hashes remains recommended supply-chain hardening and does not change this vulnerability gate.

## Current release gates — 2026-11-04

| Area | Classification | Limit |
| --- | --- | --- |
| Authentication | READY WITH CONDITIONS | Tested; coordinated0009/code rollout, HTTPS and cleanup schedule |
| Authorization | READY WITH CONDITIONS | Functional/isolation/restore tests; production IAM and external test pending |
| Yearly workflow | READY WITH CONDITIONS | Functional/race gates pass; controlled0010 rollout and browser UAT pending |
| Database | READY WITH CONDITIONS | Disposable migrations/recovery pass; source remains0009; production sizing/TLS not inspected |
| Dependencies | READY WITH CONDITIONS | Runtime scans clear;12 dev findings and Linux constraints remain |
| Load capacity | NOT READY | All25/50/100/200 stages fail; pool/timeout/refusal issue unresolved |
| Redis | READY WITH CONDITIONS | Atomic/outage/fallback/recovery pass on disposable Redis; production HA/ACL/TLS unverified |
| Evidence storage | READY WITH CONDITIONS | Private S3/upload/recovery checks pass; production MinIO/IAM/TLS/scanning unverified |
| Backup/restore | READY WITH CONDITIONS | Actual synthetic PG/S3 drill passes; real production retention/RPO/RTO unverified |
| Monitoring | NOT VERIFIED | Local probes/logs only; deployment alerting and pool wait telemetry absent |
| Production infrastructure | NOT VERIFIED | No deployment, proxy/TLS/worker budget/HA inspection |
| Browser UAT | NOT VERIFIED | No full-stack browser/mobile/accessibility/codec acceptance |
| CI enforcement | NOT VERIFIED | Disposable PostgreSQL/Redis quality workflow added locally; no hosted execution yet |

## Historical evidence — 2026-11-03

Date:2026-11-03. Overall status: **NOT READY**. READY applies only to stated repository/isolated-test evidence, never to uninspected production infrastructure. See [full audit](PRODUCTION_READINESS_AND_YEARLY_ACTIVITIES_AUDIT.md), [route matrix](AUTHORIZATION_ROUTE_MATRIX.md), and [validation results](audit-evidence/VALIDATION_RESULTS.md).

| Area | Status | Evidence / required condition |
| --- | --- | --- |
| Integrated Git baseline and preservation | READY | Remote fetched, latest runbook base verified; existing screenshot retained; no commit/push/deploy |
| Dynamic shared yearly registry | READY | Canonical RCU/year/code, duplicate protection, no fixed activity count, scoped DCU creation |
| Independent DCU lifecycle | READY | Date/state validation, snapshot, tri-state marking, complete finalization lock, audit |
| Regional finalization fairness | READY WITH CONDITIONS | Every registered active participant required; organization must accept inactive/late participant and correction policy |
| Historical transfers/imports | READY WITH CONDITIONS | Unfinished snapshots block moves; finalized history retained; review operational policy before release |
| Five equal20% scoring components | READY | Shared exact Fraction ranking; yearly-only eligibility; selected year; preserved lifetime definitions/filter ranks |
| Role/object authorization | READY WITH CONDITIONS | Server-scoped APIs and direct role/isolation regressions; no external penetration test certification |
| Authentication primitives/availability | READY WITH CONDITIONS | Existing hashing/JWT/rotation/inactive/forced-change tests; deployment HTTPS required |
| Immediate logout/reset access revocation | READY WITH CONDITIONS | P1-A resolved:29 acceptance cases plus PostgreSQL races; apply0009 with coordinated code rollout and fresh sign-in; no production deployment performed |
| Persistent session expiry/replay containment | READY WITH CONDITIONS | Family replay revocation and absolute expiry implemented; schedule bounded expired-family/legacy cleanup; competing refreshes can require sign-in |
| Credential-safe validation errors | READY | Recursive redaction + malformed JSON protection + API regressions |
| Cookie/CSRF/CORS/proxy configuration | READY WITH CONDITIONS | Repository validation exists; actual domain/HTTPS/startup/proxy trust must be verified |
| Dependency security | NOT READY | npm21 affected packages; production-only Zod high; pip16 entries/5 packages; triage and tested upgrades outstanding |
| Database integrity/migration correctness | READY WITH CONDITIONS | Yearly0008 and auth0009 SQLite/disposable PostgreSQL tests pass, legacy data retained; restored-data rehearsal required |
| PostgreSQL production settings/pool budget | NOT VERIFIED | Worker×30 limit, max_connections reserve, timeouts, role/TLS and real query plans need environment checks |
| Concurrent marking/finalization correctness | READY | Real PostgreSQL waiting-mark/finalization and parallel-mark tests in generated private schemas |
| Concurrent login/import/report throughput | NOT VERIFIED | Functional/race unit coverage is not representative throughput evidence |
| Query aggregation efficiency | READY WITH CONDITIONS | One SQL ranking statement;800 synthetic ALC serial experiment; production workload must be measured |
| Redis fallback implementation | READY WITH CONDITIONS | Bounded process-local fallback/mock tests; multi-worker fallback differs from distributed budget |
| Live Redis failure/recovery | NOT VERIFIED | Require isolated dependency stop/start and monitoring |
| Private evidence delivery implementation | READY WITH CONDITIONS | Authenticated streaming, file/path checks and timeout tests; upload resource sizing remains required |
| Object store policy/IAM/TLS/restore | NOT VERIFIED | Inspect actual private bucket/service account/encryption/lifecycle and restore |
| Frontend tests/lint/typecheck/build | READY | Prior phase136 tests/lint/build passed; no frontend change or rerun in backend-only P1-A |
| Live browser/mobile/accessibility/E2E | NOT VERIFIED | No physical viewport/assistive-tech/full-stack browser validation performed |
| Backend regression | READY WITH CONDITIONS | P1-A902 passed/67 skipped; new PostgreSQL auth cases passed separately; optional/POSIX skips and existing lint debt explicit |
| Complete backend Ruff gate | NOT READY |11 pre-existing findings in unchanged provisioning/demo scripts; new/changed source checks clean |
| 800 registered-user operational capacity | NOT VERIFIED | Population microbenchmark alone cannot establish deployed session/request capacity |
| 25/50/100/200 VU normal-load targets | NOT VERIFIED | Guarded repeatable k6 scripts delivered; representative run not performed |
| 400/800 VU stress experiment | NOT VERIFIED | Requires provisioned disposable infrastructure and explicit stress opt-in |
| CPU/RSS/pool-wait/RPS/latency/error/timeout monitoring | NOT VERIFIED | Collect per-stage infrastructure and k6 metrics; no invented results |
| Controlled real dependency recovery | NOT VERIFIED | Mocked checks pass; real DB/Redis/storage outages/recovery must be exercised |
| Structured logging/request IDs/audit source | READY WITH CONDITIONS | Integrated code/regressions; production retention/access/alerting must be configured |
| Live readiness/health monitoring/alert response | NOT VERIFIED | Configure external probes, metrics, actionable alerts and owners |
| Backup tooling | READY WITH CONDITIONS | Existing scripts/checks retained; secure external copies/scheduling/permissions must be confirmed |
| Fresh full backup/restore/RPO/RTO drill | NOT VERIFIED | Include yearly tables and evidence; no production restore performed |
| Deployment startup/shutdown/restart | NOT VERIFIED | Review real process/proxy/service manager and dependency recovery |
| Deployment migration/rollback | READY WITH CONDITIONS | Additive migration and runbook exist; back up and rehearse; avoid destructive history downgrade |
| Production secret/firewall/private networking configuration | NOT VERIFIED | Do not reuse public dev Compose; verify actual environment without exposing credentials |
| CI/CD enforcement | NOT VERIFIED | No workflow found; organization must wire tests/security/integration gates |

Release requires closing NOT READY items and obtaining evidence for operational NOT VERIFIED items relevant to the chosen deployment. No infrastructure access, deployment, commit or push is implied by this checklist.
