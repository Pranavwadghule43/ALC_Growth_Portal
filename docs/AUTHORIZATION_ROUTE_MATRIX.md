# API authorization route matrix

## Current yearly evidence routes — 2026-11-04

All routes below are under `/api/yearly-activities`; unsafe methods retain CSRF protection.
Existing registry, scheduling, hierarchy and authentication guards are preserved.

| Route | ALC | SBU | DCU | ADMIN |
| --- | --- | --- | --- | --- |
| GET `/submissions` | Own applicable participation | Current authorized ALCs | Own run + current hierarchy | Oversight |
| POST `/runs/{id}/submission` | Own snapshot, open initial window | 403 | 403 | 403 |
| GET `/submissions/{id}` | Own history/draft | Scoped submitted history | Responsible scoped submitted history | Submitted history |
| PATCH `/submissions/{id}` | Own editable draft/correction | 403 | 403 | 403 |
| POST `/submissions/{id}/submit` | Own validated evidence revision | 403 | 403 | 403 |
| POST `/submissions/{id}/review` | 403 | 403 | Responsible scope; pending exact revision only | 403 |
| POST `/submissions/{id}/evidence` | Own editable submission; bounded file | 403 | 403 | 403 |
| DELETE `/evidence/{id}` | Own editable submission; soft removal | 403 | 403 | 403 |
| GET `/evidence/{id}/access` and `/content` | Own proof | Authorized submitted proof | Responsible authorized submitted proof | Submitted proof |
| Retired PATCH `/runs/{id}/completions/{alc}` | 403 | 403 | 410 | 403 |

Cross-scope IDs return 404. Draft details and newly added correction evidence stay private until
submission/resubmission. VERIFIED, REJECTED and finalized history cannot be edited through normal
workflow. The historical matrix's manual completion mutation is superseded by this section.

## Historical route review — 2026-11-03

Read-only source review dated 2026-11-03. Every endpoint below is listed individually. Role guards are server-side; resource-scope helpers remain necessary after role authorization. This inventory is source evidence, not a claim that every possible request permutation was dynamically tested.

All authenticated dependencies call `get_current_user`, which reloads the current database account and checks active hierarchy. All role guards reject `must_change_password`. `/admin`, `/portal`, `/leaderboard` and `/yearly-activities` routers apply CSRF to unsafe methods. Authentication exceptions are detailed below. There are 83 existing routes and six new yearly-activity routes.

| Method | API route | Required role | Data scope | CSRF for write |
| --- | --- | --- | --- | --- |
| GET | `/api/health` | Public | Liveness, no dependency access | N/A |
| GET | `/api/health/live` | Public | Liveness, no dependency access | N/A |
| GET | `/api/health/ready` | Public | Read-only dependency checks | N/A |
| POST | `/api/auth/login` | Public | See authentication rules below | No; see below |
| POST | `/api/auth/admin-login` | Public | See authentication rules below | No; see below |
| POST | `/api/auth/refresh` | Public | See authentication rules below | No; see below |
| GET | `/api/auth/me` | Signed in | Own account; no must-change-password guard | N/A |
| POST | `/api/auth/logout` | Public | See authentication rules below | Yes |
| POST | `/api/auth/change-password` | Signed in | Own account; no must-change-password guard | Yes |
| GET | `/api/portal/dashboard` | DCU / SBU / ALC | Current hierarchy; own ALC for ALC | N/A |
| GET | `/api/portal/activities` | DCU / SBU / ALC | Current hierarchy; own ALC for ALC | N/A |
| POST | `/api/portal/activities` | ALC | Own ALC only | Yes |
| GET | `/api/portal/activities/{activity_id}` | DCU / SBU / ALC | Current hierarchy; own ALC for ALC | N/A |
| PATCH | `/api/portal/activities/{activity_id}` | ALC | Own ALC only | Yes |
| POST | `/api/portal/activities/{activity_id}/submit` | ALC | Own ALC only | Yes |
| POST | `/api/portal/activities/{activity_id}/evidence` | ALC | Own ALC only | Yes |
| DELETE | `/api/portal/evidence/{evidence_id}` | ALC | Own ALC only | Yes |
| GET | `/api/portal/evidence/{evidence_id}/access` | DCU / SBU / ALC | Current hierarchy; own ALC for ALC | N/A |
| GET | `/api/portal/evidence/{evidence_id}/content` | DCU / SBU / ALC | Current hierarchy; own ALC for ALC | N/A |
| GET | `/api/portal/partners` | DCU / SBU / ALC | Current hierarchy; own ALC for ALC | N/A |
| POST | `/api/portal/partners` | ALC | Own ALC only | Yes |
| PATCH | `/api/portal/partners/{partner_id}` | ALC | Own ALC only | Yes |
| GET | `/api/portal/tasks` | ALC | Own ALC only | N/A |
| POST | `/api/portal/tasks` | ALC | Own ALC only | Yes |
| PATCH | `/api/portal/tasks/{task_id}/complete` | ALC | Own ALC only | Yes |
| GET | `/api/portal/challenge` | ALC | Own ALC only | N/A |
| GET | `/api/portal/notifications` | DCU / SBU / ALC | Current hierarchy; own ALC for ALC | N/A |
| POST | `/api/portal/notifications/{notification_id}/read` | DCU / SBU / ALC | Current hierarchy; own ALC for ALC | Yes |
| GET | `/api/portal/sbus` | DCU / SBU | Current DCU/SBU hierarchy only | N/A |
| GET | `/api/portal/sbus/{sbu_id}` | DCU / SBU | Current DCU/SBU hierarchy only | N/A |
| GET | `/api/portal/alcs` | DCU / SBU | Current DCU/SBU hierarchy only | N/A |
| GET | `/api/portal/lookups/alcs` | DCU / SBU | Current DCU/SBU hierarchy only | N/A |
| GET | `/api/portal/alcs/{alc_id}` | DCU / SBU | Current DCU/SBU hierarchy only | N/A |
| POST | `/api/portal/alcs/{alc_id}/reset-password` | DCU / SBU | Current DCU/SBU hierarchy only | Yes |
| GET | `/api/portal/sbu/partners` | DCU / SBU | Current DCU/SBU hierarchy only | N/A |
| GET | `/api/portal/partner-directory` | DCU / SBU | Current DCU/SBU hierarchy only | N/A |
| GET | `/api/portal/verification` | DCU / SBU | Current DCU/SBU hierarchy only | N/A |
| GET | `/api/portal/verification/{activity_id}` | DCU / SBU | Current DCU/SBU hierarchy only | N/A |
| POST | `/api/portal/activities/{activity_id}/verify` | DCU / SBU | Current DCU/SBU hierarchy only | Yes |
| POST | `/api/portal/activities/{activity_id}/request-correction` | DCU / SBU | Current DCU/SBU hierarchy only | Yes |
| POST | `/api/portal/activities/{activity_id}/reject` | DCU / SBU | Current DCU/SBU hierarchy only | Yes |
| POST | `/api/portal/activities/{activity_id}/change-decision` | DCU | Own DCU only | Yes |
| GET | `/api/portal/reports/sbu-summary` | DCU / SBU | Current DCU/SBU hierarchy only | N/A |
| GET | `/api/portal/reports/sbu-performance.csv` | DCU / SBU | Current DCU/SBU hierarchy only | N/A |
| GET | `/api/portal/reports/activities.csv` | DCU / SBU / ALC | Current hierarchy; own ALC for ALC | N/A |
| GET | `/api/portal/reports/partners.csv` | DCU / SBU / ALC | Current hierarchy; own ALC for ALC | N/A |
| GET | `/api/portal/reports/verification-status.csv` | DCU / SBU / ALC | Current hierarchy; own ALC for ALC | N/A |
| GET | `/api/admin/dashboard` | ADMIN | Regional administrative scope | N/A |
| GET | `/api/admin/verification-queue` | ADMIN | Regional administrative scope | N/A |
| GET | `/api/admin/activities` | ADMIN | Regional administrative scope | N/A |
| GET | `/api/admin/activities/{activity_id}` | ADMIN | Regional administrative scope | N/A |
| POST | `/api/admin/activities/{activity_id}/verify` | ADMIN | Regional administrative scope | Yes |
| POST | `/api/admin/activities/{activity_id}/request-correction` | ADMIN | Regional administrative scope | Yes |
| POST | `/api/admin/activities/{activity_id}/reject` | ADMIN | Regional administrative scope | Yes |
| POST | `/api/admin/activities/{activity_id}/change-decision` | ADMIN | Regional administrative scope | Yes |
| GET | `/api/admin/evidence/{evidence_id}/access` | ADMIN | Regional administrative scope | N/A |
| GET | `/api/admin/evidence/{evidence_id}/content` | ADMIN | Regional administrative scope | N/A |
| GET | `/api/admin/alcs` | ADMIN | Regional administrative scope | N/A |
| GET | `/api/admin/alcs/{alc_id}` | ADMIN | Regional administrative scope | N/A |
| PATCH | `/api/admin/alcs/{alc_id}` | ADMIN | Regional administrative scope | Yes |
| PATCH | `/api/admin/alcs/{alc_id}/sbu` | ADMIN | Regional administrative scope | Yes |
| POST | `/api/admin/alcs/import/validate` | ADMIN | Regional administrative scope | Yes |
| POST | `/api/admin/alcs/import` | ADMIN | Regional administrative scope | Yes |
| GET | `/api/admin/partners` | ADMIN | Regional administrative scope | N/A |
| GET | `/api/admin/challenge` | ADMIN | Regional administrative scope | N/A |
| POST | `/api/admin/growth-challenges` | ADMIN | Regional administrative scope | Yes |
| PATCH | `/api/admin/growth-challenges/{challenge_id}` | ADMIN | Regional administrative scope | Yes |
| GET | `/api/admin/users` | ADMIN | Regional administrative scope | N/A |
| POST | `/api/admin/users` | ADMIN | Regional administrative scope | Yes |
| PATCH | `/api/admin/users/{user_id}` | ADMIN | Regional administrative scope | Yes |
| DELETE | `/api/admin/users/{user_id}` | ADMIN | Regional administrative scope | Yes |
| GET | `/api/admin/sbus` | ADMIN | Regional administrative scope | N/A |
| POST | `/api/admin/sbus` | ADMIN | Regional administrative scope | Yes |
| GET | `/api/admin/sbus/{sbu_id}` | ADMIN | Regional administrative scope | N/A |
| PATCH | `/api/admin/sbus/{sbu_id}` | ADMIN | Regional administrative scope | Yes |
| PATCH | `/api/admin/sbus/{sbu_id}/dcu` | ADMIN | Regional administrative scope | Yes |
| GET | `/api/admin/dcus` | ADMIN | Regional administrative scope | N/A |
| GET | `/api/admin/dcus/{dcu_id}` | ADMIN | Regional administrative scope | N/A |
| GET | `/api/admin/audit-logs` | ADMIN | Regional administrative scope | N/A |
| GET | `/api/admin/reports/activities.csv` | ADMIN | Regional administrative scope | N/A |
| GET | `/api/admin/settings` | ADMIN | Regional administrative scope | N/A |
| GET | `/api/leaderboard/region-top10` | ADMIN / DCU / SBU / ALC | Regional aggregate scores intentionally shared | N/A |
| GET | `/api/leaderboard/region-scores` | ADMIN / DCU / SBU / ALC | Regional aggregate scores intentionally shared | N/A |
| GET | `/api/yearly-activities/definitions` | ADMIN / DCU / SBU / ALC | ADMIN overview; operational roles only definitions owned by their current parent DCU; direct legacy run IDs cannot bypass owner scope | N/A |
| POST | `/api/yearly-activities/definitions` | DCU | Owner derived from authenticated DCU; creates only that DCU's run; ownership is not patchable | Yes |
| PATCH | `/api/yearly-activities/runs/{run_id}` | DCU | Own DCU run only; lifecycle and schedule validation | Yes |
| GET | `/api/yearly-activities/runs/{run_id}/completions` | ADMIN / DCU / SBU / ALC | ADMIN all; DCU current hierarchy; SBU current assigned ALCs; ALC self only | N/A |
| PATCH | `/api/yearly-activities/runs/{run_id}/completions/{alc_id}` | DCU | Retired manual marking API; always 410 after role/CSRF checks | Yes |
| POST | `/api/yearly-activities/runs/{run_id}/finalize` | DCU | Own DCU-owned run only; ended MARKING and no unresolved reviews/corrections | Yes |

## Authentication rules

- `POST /auth/login`: public credential exchange, operational roles only, account role resolved in the database. JSON body plus SameSite cookies; no separate login CSRF token. IP and IP+identifier throttles apply before Argon2.
- `POST /auth/admin-login`: same credential exchange restricted to ADMIN identities.
- `POST /auth/refresh`: opaque refresh cookie bound to a persistent family, user/family/token locks and conditional consumption; expiry/revocation/account checks. Replay revokes that family and successors. No separate CSRF token; SameSite=Lax remains the browser cross-site defense.
- `POST /auth/logout`: double-submit CSRF; revokes the signed access family or stored refresh-family fallback and clears cookies. Copied access JWTs from that family fail immediately after commit; independent sessions remain valid.
- `GET /auth/me`: valid access cookie plus current active account/hierarchy; permits must-change-password accounts to reach their profile.
- `POST /auth/change-password`: current password proof plus CSRF and locked current-session validation. Revokes all previous access/refresh families; returns replacement auth cookies for the current browser. See SESSION_REVOCATION_REMEDIATION.md.

## Resource checks and evidence

| Route family | Object authorization / evidence |
| --- | --- |
| Portal activities/detail/evidence | `activity_scope`, `scoped_activity`, `scoped_evidence`; supervisors cannot see drafts; evidence is streamed only after scope check. |
| Portal partners/tasks | `alc_scope` and owning ALC predicates on writes; related partner references checked against own ALC. |
| Portal directory/review/reports | `alc_scope`, `sbu_scope`, explicit `require_alc_in_scope`/`require_sbu_in_scope`; caller filters narrow scope. |
| Portal reset-password | `require_supervisor`, `scoped_alc`, target user restricted to `Role.ALC`; audit excludes password. |
| DCU decision override | `require_dcu`, same scoped activity, mandatory reason, audit and notifications. |
| Portal notifications | `Notification.user_id == user.id` for list and mark-read. |
| Admin routes | Every endpoint has `require_admin`; user mutation schemas omit assignable role/scope on patch. |
| Leaderboard | Intentionally regional read-only aggregate contract. This is not a permission to read detailed activities/evidence. |

## Existing dynamic coverage

`test_sbu_isolation.py`, `test_isolation_writes.py`, `test_auth_separation.py`, `test_dcu_hierarchy.py`, `test_dcu_portal.py`, `test_inactive_hierarchy.py`, `test_hierarchy_reassignment.py`, `test_evidence_delivery.py`, `test_password_security.py`, `test_login_rate_limit.py`, `test_proxy_boundary.py` and `test_auth_stability.py` exercise direct API permissions. Refer to the master audit for actual commands/results; source inventory does not substitute for executed results.
