# P1-A persistent session revocation

## Verified baseline and architecture before implementation

Actual branch: `dev1/production-blockers-remediation`; phase base HEAD:
`85250dfcd5c936182bdce9fdf18d685ddbc38b71`. The Yearly Activities implementation is
already committed in this baseline, despite the pasted brief's older working-tree description.
Only the existing untracked screenshot was present. No branch change or patch reapplication
is needed or authorized.

Before this phase, access JWTs contained `sub`, `type`, `iat`, and `exp`, signed with HS256.
`get_current_user` decoded the subject and loaded the current User and hierarchy on every
protected request. That checked current availability/role/scope but no persistent session.
RefreshToken stored SHA256 token digests, expiry and revocation; rotation atomically consumed
one row and created another unrelated row. Logout revoked only the presented refresh row.
Resets revoked all refresh rows; password change spared the presented current refresh row.
There was no stable device-session identity or refresh-family replay containment. Consequently,
revoked refresh state could not invalidate a copied unexpired access JWT, and clearing
`must_change_password` could restore its operational access.

## Selected design

Add one persistent `auth_sessions` row per login/device, referenced by a required opaque
UUID `sid` in access JWTs and nullable `RefreshToken.session_id` for legacy-row compatibility.
Join active/unexpired session validation into the existing user lookup: zero extra SQL
round trips per ordinary protected request. Roles and scope remain database-derived.

Logout revokes only the presented session/family, including rotated credentials. Password
change/reset revoke all old families; a successful self-change issues a new session/cookies
so the current browser stays signed in. A family has an absolute lifetime equal to the
configured refresh lifetime from login; refresh does not extend it. Consumed refresh hashes
remain until family expiry so reuse can revoke that entire family. Strict replay detection
can sign out a family after competing refreshes; the frontend's existing single-flight
refresh is preserved. Old access tokens lacking `sid` and legacy refresh rows lacking a
family fail closed after rollout; users must sign in again.

Serialize login issuance, refresh, logout and global revocation with a per-user row lock,
then session/token locks in that order. No global auth lock or Redis revocation cache.
Credential verification happens before login's short issuance lock; compare the verified
password hash with locked current state before issuing, closing login/reset races. An
already validated in-flight request may finish; requests whose persistent validation starts
after revocation commits must fail. Ordinary reads do not hold auth locks for their entire
business operation.

New additive migration follows actual head `20261103_0008`. Existing users and refresh rows
are preserved. Expiry indexes and a bounded batch cleanup command remove expired families
and legacy tokens; deployment must schedule cleanup. No row is created per access JWT.

Implementation, concurrency/migration evidence and exact final validation results are
recorded below. Overall portal readiness remains **NOT READY** for the
unrelated dependency, capacity, recovery, infrastructure and UAT blockers.

## Implemented lifecycle

- `AuthSession` holds UUID id, indexed user id, creation/absolute expiry, and revocation.
  `RefreshToken.session_id` is nullable only to preserve legacy rows; it is indexed and
  cascades on family deletion. Actor/business history is unaffected by auth cleanup.
- JWT validation requires signature/HS256, access type, expiry/issue time, UUID subject and
  UUID session id. User/session ownership, expiry and revocation are checked in the existing
  user query before current account/hierarchy guards. No role/scope claim is trusted.
- Login creates a family and first hashed refresh credential. A verified old password cannot
  issue after a concurrent reset because issuance locks/reloads User and compares the hash
  that was actually verified. Fresh hierarchy/availability is checked before commit.
- Refresh resolves the indexed digest, locks its user/family/token, conditionally consumes
  it and issues a hashed successor in the **same** family. Family expiry is not extended.
  Reusing a consumed token commits family revocation before returning401. A racing winning
  refresh response can therefore contain credentials which are already unusable, safely
  requiring sign-in again. No reuse grace period is added.
- Logout keeps CSRF protection and idempotent cookie clearing. It selects the signed access
  token's family, or falls back to the stored refresh family when access is absent/expired.
  Even a consumed old refresh token can identify that same family. It revokes that family
  and every active refresh row in it, leaving separate logins/devices intact. If presented
  access and refresh belong to different families, the signed access identity takes priority.
- Normal or forced self password change verifies the current and different new password,
  then locks/reloads User and revalidates the current family. Every old family/token is
  revoked, password/forced-change flag are updated, a replacement family is created and
  fresh access/refresh/CSRF cookies are returned in one committed transaction. Response JSON
  remains `{message:"Password changed"}`. No frontend source/API-client change is needed.
- ADMIN and scoped DCU/SBU password resets lock the target User, preserve existing reset/
  forced-change policy and revoke **all** its families and legacy refresh rows atomically.
  Completing forced change creates a different family; revoked originals remain revoked.
- Inactive accounts/hierarchy are still checked each request. Deactivation immediately
  blocks access; refresh against an unavailable account now also revokes that family.
  Account deletion serializes with issuance and deletes auth state while preserving
  existing business history. General role/hierarchy authorization logic is unchanged.

## Race analysis

| Race | Serialization / result |
| --- | --- |
| Refresh vs logout | Both acquire User then family; either logout wins and refresh401, or refresh commits then logout revokes its stable family/successor. |
| Refresh vs reset | Target User lock serializes reset with refresh; no old-family successor survives reset commit. |
| Two refreshes | Exactly one conditional consumption succeeds; replay detection revokes the family and the winning successor too. |
| Password change vs refresh | Same user serialization; only the password-change replacement family survives. |
| Login vs reset | Hash verified before reset is compared with locked current hash; stale proof cannot issue a new family. |
| Request vs logout/revocation | A new validation query after revocation commit sees revoked/missing family and401; an already validated in-flight operation may finish. No long auth locks are held on ordinary API requests. |
| Cleanup vs refresh | Expired families lock first using SKIP LOCKED, then delete child credentials; no token-first/family-first lock inversion. |

Actual PostgreSQL tests use independent request transactions/connections and unique newly
created schemas. An extra independent-engine test proves committed revocation is visible
without shared Python state. This is persistent-backend correctness evidence, not a measured
multi-host deployment or simultaneous-user capacity claim.

## Performance and retention

Ordinary protected requests add **zero SQL round trips**: the existing user select gains an
`auth_sessions` primary-key/owner/active/expiry join. The measured statement-count acceptance
test compares the original user/hierarchy query with `/auth/me` and asserts equal counts,
with exactly one statement touching the session table. Existing eager hierarchy loading is
retained. Indexes: session PK, `auth_sessions.user_id`, `auth_sessions.expires_at`, existing
unique refresh digest/user/expiry indexes, and new `refresh_tokens.session_id`. Revocation
updates only indexed user/family matches; no access JWT row or permanent denylist is added.

Storage is one family per login plus one digest row per refresh, retained until absolute
family expiry. With cleanup running, retention is at most configured refresh lifetime
(default14 days, production maximum30) plus cleanup delay, rather than lifetime traffic.
Family revocation tombstones/consumed hashes must remain until expiry for stale-logout and
replay detection. Row volume is proportional to login/refresh traffic within that window;
this phase does not establish a throughput or byte-size capacity bound.

Schedule `python -m scripts.cleanup_auth_sessions --batch-size 1000 --max-batches 100` hourly
using the protected API environment. It commits bounded family/legacy batches, removes only
expired credentials, prints counts only, and closes its engine. Large families can contain
many refresh rows, so measure cleanup duration/backlog and tune batch size operationally.
No scheduler or production cleanup was installed/run in this task. Without scheduling,
expired rows are still rejected for authentication but can accumulate in storage.

## Migration and rollout requirements

Additive `20261103_0009` follows0008; it creates the session table and nullable indexed FK.
Existing users, password hashes, forced-change flags and old refresh rows are preserved.
Legacy refresh rows remain NULL-linked and unusable, not silently adopted into a new family.
Pre-upgrade access JWTs without sid are rejected. All existing browsers must sign in again.

For a separately authorized rollout, back up and rehearse0009 on a disposable restored
database; coordinate/drain old workers, apply the migration once, start all workers with
the new code, verify fresh sign-in and revocation, and schedule cleanup. Mixed old/new auth
workers are unsupported: old workers would still mint/accept credentials without the new
persistent checks. Do not describe migration application alone as the security fix.

Downgrade was exercised only in a disposable schema; it removes the added auth state/link
while retaining users and refresh rows. Prefer retaining the schema and forward repair.
Rolling back to the old auth code restores its revocation weakness; any emergency rollback
needs a reviewed forced-reauthentication/signing-key plan, not an automatic destructive
downgrade. Nothing was deployed or migrated on an existing database here.

A future backup restore can roll persistent revocation state back in time. The recovery
drill must require invalidating restored auth families/forcing fresh sign-in before traffic
resumes, as part of its separately reviewed procedure; no restore or global key change ran here.

## Final executed validation

- Targeted authentication: **51 passed in78.38s**, consisting of29 revocation/claim/retention/
  query-path tests,15 auth-stability tests and7 password-security tests.
- New migration + PostgreSQL concurrency: **10 passed in12.34s**:3 SQLite migration/graph,
  1 actual PostgreSQL full-chain legacy upgrade/downgrade/re-upgrade and6 real concurrency/
  persistence tests. Existing user/hash/refresh data and Yearly tables were retained.
- Changed application/test/cleanup/migration Ruff checks pass. Full repository Ruff still
  reports the same11 unrelated pre-existing provisioning/demo-script findings.
- Complete backend regression: **902 passed,67 skipped,1 warning in612.32s (10m12s)**.
  This run started after all source/test changes were complete. Skips cover existing POSIX/
  optional dependency integration cases plus the new opt-in PostgreSQL tests. The new7
  PostgreSQL auth cases were separately executed and passed in the10-test run above.
  No failing test was bypassed. Yearly Activities/scoring and role/hierarchy regressions
  passed as part of this complete suite.
- Frontend is unchanged; tests, lint and production build were not rerun in this backend-only
  phase. Existing JSON contracts are retained; browser/full-stack UAT remains unverified.
- `git diff --check` and `git diff --cached --check` passed. No staged changes; actual branch
  and HEAD remain the verified phase base. The original screenshot and prior recovery patch
  are preserved. The disposable PostgreSQL container and generated schemas were removed.

The targeted run retains a warning from deliberately constructing a wrong-algorithm HS512
token with the HS256 test key. It is rejected as intended; no warning/error filter or test
suppression was added.

## Security acceptance result

| Requirement | Result |
| --- | --- |
| Copied access after its logout | PASS —401 |
| Old refresh after logout | PASS —401 |
| Copied access after normal password change | PASS —401; replacement current browser remains valid |
| All old refresh sessions after credential change/reset | PASS —401 across both pre-existing devices |
| Copied access immediately after ADMIN/DCU/SBU reset | PASS —401 |
| Original copied access after forced-change completion | PASS —still401 |
| Logout one device preserves independent login | PASS —other device access/refresh200 |
| Refresh/logout/reset/change races | PASS —no unexpected surviving old family |
| Account availability, role and hierarchy enforcement | PASS in complete existing regression suite |
| Cross-connection persistent revocation | PASS —independent engine observes committed revocation |

The original P1 copied-JWT finding is **resolved in repository implementation and automated
acceptance evidence**, with migration/rollout prerequisites documented. This is not an
assertion that production is deployed, dependency-clean, load-tested or otherwise READY.

## Files modified in this phase

The exhaustive phase-only [manifest](audit-evidence/P1A_CHANGED_FILES.txt) lists every
modified/new source, test, report and raw validation file. The earlier Yearly manifest/logs
remain separate historical evidence. Source groups:

- Auth implementation: `app/auth.py`, `app/dependencies.py`, `app/models.py`,
  `app/routes/auth.py`, `app/routes/admin.py`, `app/routes/portal.py`, `app/services/sessions.py`.
- Additive schema/maintenance: `alembic/versions/20261103_0009_auth_sessions.py`,
  `scripts/cleanup_auth_sessions.py` (paths above relative to `apps/api`).
- Tests: `test_auth_stability.py`, `test_password_security.py`,
  `test_session_revocation_evidence.py`, new `test_auth_session_migration.py` and
  `test_auth_session_concurrency.py`; `test_dcu_hierarchy.py` only supplies required sid in
  its forged/stale token; `test_growth_challenge_migration.py` and
  `test_yearly_activity_migration.py` only integrate the new graph head/explicit old revision.
- Documentation: this report, the master audit/checklist/security evidence/validation record
  requested by the brief, and auth lifecycle entries in `AUTHORIZATION_ROUTE_MATRIX.md`.
  New `P1A_*` evidence files preserve raw test/lint/status output and the exhaustive manifest.

No Yearly domain service, approved scoring implementation, hierarchy history rule or frontend
file changed. `git diff` verifies those business/scoring/frontend paths have no phase diff.

## Remaining production blockers and final Git status

Dependency remediation, representative load/stress testing, a fresh recovery drill,
production infrastructure/monitoring verification and browser/full-stack UAT remain open.
Existing unrelated11-findings Ruff debt remains visible. Migration rehearsal, all-worker
rollout/fresh sign-in and expiry-cleanup scheduling are explicit operational prerequisites
for the resolved auth fix. Overall readiness remains **NOT READY**.

Branch `dev1/production-blockers-remediation`; HEAD
`85250dfcd5c936182bdce9fdf18d685ddbc38b71`, unchanged by P1-A. No commit, push, deployment,
checkout, reset or stash. Phase changes are unstaged; existing Yearly work and the untracked
`alc-growth-portal-ui.png` are preserved. Complete final status is in
[P1A_FINAL_GIT_STATUS.txt](audit-evidence/P1A_FINAL_GIT_STATUS.txt).
