# ALC Growth Portal — Remaining Work

> Based on the current repository state and the latest project decisions. Update this
> checklist as work is completed. Work is organized developer-wise: **Pranav**,
> **Dhruvank**, and **Shared / Final Testing**.

## Role / Portal Structure (current decision)

| Role | Portal | Login identifier |
| --- | --- | --- |
| `ADMIN` (shown as "Super Admin") | `/admin` | username / email + password |
| `SBU` | `/portal` | username / email + password |
| `ALC` | `/portal` | ALC Code + password |

- One common login page — **no role selector**. The **backend** authenticates and
  determines the role; the frontend never sends a trusted role.
- One SBU → many ALCs (`ALC.sbu_id`). One ALC → one SBU. All reads/writes are scoped
  server-side.

**Branch status:** the common login + SBU + unified `/portal` architecture lives on the
feature branches `feature/common-login` and `feature/sbu-operational-portal` (pushed, **not
merged into `main`**). `main` still carries the older role-selector login and `/alc`
routing. See **Git / Branch Status** at the end of this file.

---

## 1. Current Completed Functionality

Verified against the code on the relevant branch. Items marked *(feature branch)* are
implemented and tested but not yet merged into `main`.

- [x] Admin login (username/email + password)
- [x] ALC login (ALC Code + password)
- [x] Role-based access control (ADMIN / ALC)
- [x] ALC data isolation (ownership enforced server-side)
- [x] Activity creation with non-negative metric and date validation
- [x] Draft / submit workflow with edit locking
- [x] Evidence upload (extension + declared MIME + file-signature checks, server-side keys)
- [x] Private evidence access via short-lived signed URLs
- [x] Admin verification queue
- [x] Verify / request correction (reason required) / reject (reason required)
- [x] ALC correction + resubmission with retained decision/submission snapshots
- [x] Partner management (create / edit)
- [x] Tasks / follow-ups
- [x] 30-Day Challenge base implementation
- [x] Notifications backend (`Notification` model + read/mark-read endpoints)
- [x] Audit logging (`AuditLog` model + audit service)
- [x] Basic CSV activity reports
- [x] Admin user management (list / create / patch users)
- [x] Password reset workflow with forced password change after reset
- [x] Local evidence storage adapter (development)
- [x] S3 / MinIO evidence storage adapter (`storage/s3.py`)
- [x] Initial Alembic migration (`20260916_0001_initial`)
- [x] Common unified login (backend determines role, no role selector) *(feature branch)*
- [x] Unified `/portal` experience shared by SBU and ALC *(feature branch)*

---

## 2. Current SBU Module Status

Implemented on `feature/sbu-operational-portal` (pushed, **not merged to `main`**). The SBU
**backend** is substantially complete and covered by tests; the SBU **frontend** pages are
functional but minimal and still need a refinement pass (see **Remaining work — Dhruvank**).

- [x] SBU role (`Role.SBU`)
- [x] SBU database model (`sbus`: id, code unique, name, is_active, timestamps)
- [x] `sbu_id` relation on ALC (nullable FK + relationship)
- [x] `sbu_id` relation on User (nullable FK + relationship)
- [x] SBU login (common login matches SBU by username/email; role derived server-side)
- [x] SBU portal routing (`SBU → /portal`, role-guarded; ADMIN redirected off `/portal`)
- [x] SBU dashboard (`sbu_dashboard` backend + `SbuDashboard` page, scoped metrics)
- [x] Assigned ALC directory (`GET /portal/alcs` + `SbuAlcs`)
- [x] SBU ALC detail (`GET /portal/alcs/{id}` + `SbuAlcDetail`)
- [x] Scoped activity list (`GET /portal/activities` + `SbuActivities`)
- [x] Verification queue (`GET /portal/verification`)
- [x] Verify / request correction / reject (SBU recorded as reviewer, reuses lifecycle)
- [x] Partner visibility for assigned ALCs (`GET /portal/partners` + `SbuPartners`)
- [x] Reports — basic scoped CSV only (`GET /portal/reports/activities.csv` + `PortalReports`)
- [x] Password reset for assigned ALC users (`POST /portal/alcs/{id}/reset-password`, forces change)
- [x] SBU-specific authorization (`require_sbu` dependency)
- [x] Strict cross-SBU isolation (`scoped_alc_ids`; out-of-scope → 404; covered by tests)
- [x] Admin SBU management backend + minimal UI (`/admin/sbus`, ALC assignment; `AdminSbus`/`AdminSbuDetail`)
- [x] Additive, idempotent SBU Alembic migration (`20260918_0002_sbu`)
- [x] SBU test suite (`tests/test_sbu.py`, 19 tests; passing on SQLite)

Not yet done (owned by Dhruvank — refinement, not rebuild):

- [ ] SBU portal UI refinement (layout, density, consistency across SBU pages)
- [ ] Evidence viewing (gallery / preview) for assigned ALCs
- [ ] SBU reports beyond basic CSV (filters, XLSX)
- [ ] SBU responsive / mobile UI
- [ ] Consistent SBU loading / empty / error states across every page
- [ ] Additional SBU backend/frontend tests (real PostgreSQL, direct-URL access attempts)

---

## 3. Remaining Work — Pranav

Owns remaining Admin, ALC, data, backend, reporting, security, and deployment work.

### Real ALC master import
The current importer (`scripts/import_alcs.py`) reads only `ALC Code` and `ALC Name`; it
does not read `SBU`. Only these source fields are required — do **not** import Taluka,
Mobile, Email, Area Type, RCU, or DCU.

- [ ] Extend the importer to read `ALC Code`, `ALC Name`, `SBU` (only these fields)
- [ ] Treat `ALC Code` as the authoritative unique identifier
- [ ] Update existing ALCs by code (name and SBU when changed)
- [ ] Create new ALCs not present in the database
- [ ] Upsert / resolve the SBU by name/code and set `alc_sbu_id`
- [ ] Detect duplicate codes within the file
- [ ] Detect blank / invalid codes
- [ ] Do **not** delete existing ALCs missing from a later import
- [ ] Do **not** create or reset passwords as part of the import
- [ ] Replace the sample `data/ALC-MASTER.csv` with the real master and verify counts

### Admin CSV import (UI)
- [ ] Add an Admin CSV upload page
- [ ] Validate the uploaded CSV and show valid / invalid / duplicate row counts
- [ ] Safe upsert behavior (never delete, never touch passwords)
- [ ] Show an import summary and write import audit logs

### ALC account & user management
- [ ] ALC account management improvements
- [ ] Remaining Admin user management
- [ ] Delete ALC / SBU login-account feature

### Notifications UI
- [ ] Notification bell + unread counter in the header
- [ ] Dedicated Notifications page
- [ ] Mark as Read / Mark All as Read
- [ ] Deep-link to the related activity
- [ ] Improve notification content for Verified / Correction Required / Rejected

### Evidence gallery & preview
- [ ] Image thumbnail gallery
- [ ] Image preview / lightbox
- [ ] PDF preview / open action
- [ ] Show file name, type, and size
- [ ] Improve upload progress and per-file errors

### Partner module completion
- [ ] Partner detail page
- [ ] Edit partner
- [ ] Partner activity history
- [ ] Partner follow-ups / tasks
- [ ] Partner verification / performance summary

### ALC Performance page
- [ ] Dedicated ALC Performance page using verified metrics only
- [ ] Date filters and useful charts
- [ ] Verified-data ALC comparison / filtering

### Reports & analytics
- [ ] Improve report filters
- [ ] XLSX exports
- [ ] Activities by ALC / Verification Status / Learner Reach / Leads / Admissions reports
- [ ] Partner and Challenge Progress reports
- [ ] Allow ALC to export its own activity history
- [ ] Improve Admin performance analytics

### 30-Day Challenge calculation fixes
- [ ] Review partnership-count logic
- [ ] Fix mismatch between current activity types and partnership calculation
- [ ] Confirm rules for prospects / meetings / pilots / partnerships
- [ ] Ensure official progress uses verified data only
- [ ] Add backend tests for challenge calculations

### Settings persistence
- [ ] Make Admin Settings editable (currently `GET /admin/settings` only, no mutation)
- [ ] Persist settings in the database with validation
- [ ] Audit important settings changes

### Password viewing / encryption (only if the requirement remains)
- [ ] Keep Argon2 hash for login verification
- [ ] Store an additional encrypted password value only if Admin password viewing is required
- [ ] Keep the encryption key server-side only
- [ ] Admin-only password reveal endpoint, fully audited, never logging the actual password

### Backend, data & infrastructure
- [ ] Alembic migration cleanup (structure, explicit future migrations, upgrade/downgrade tests, docs)
- [ ] PostgreSQL integration testing (indexes, pooling, transactions, concurrent submissions, unique activity numbers)
- [ ] Redis configuration / testing (login rate limiting, failure behavior, production endpoint)
- [ ] MinIO / S3-compatible storage integration (private access, deletion, key security, large files, production bucket)
- [ ] Backend security review (authorization, ownership isolation, evidence auth, reset behavior, cookies/CSRF/CORS/headers, rate limiting, secret rotation)
- [ ] Load / concurrency testing (~380 ALCs active)
- [ ] Production deployment (backend host + release migration)

---

## 4. Remaining Work — Dhruvank

Owns SBU module refinement and all remaining SBU-related work. **Refine and complete the
existing SBU implementation on `feature/sbu-operational-portal` — do not rebuild it.**

- [ ] SBU portal UI refinement
- [ ] SBU dashboard refinement
- [ ] Assigned ALC directory refinement
- [ ] SBU ALC detail pages refinement
- [ ] ALC ↔ SBU assignment (verify and refine the Admin/SBU flows)
- [ ] ALC reassignment between SBUs
- [ ] Admin SBU management UI refinement
- [ ] SBU activity monitoring
- [ ] Activity verification (verify) refinement
- [ ] Request correction refinement
- [ ] Reject activity refinement
- [ ] Evidence viewing for assigned ALCs
- [ ] Partners for assigned ALCs (detail / refinement)
- [ ] SBU reports (filters, XLSX, beyond basic CSV)
- [ ] SBU password reset for assigned ALC users (refine UX / confirmation)
- [ ] Strict cross-SBU backend isolation (harden and re-verify)
- [ ] SBU responsive / mobile UI
- [ ] SBU loading / empty / error states (consistent across all pages)
- [ ] SBU backend / frontend tests (expand coverage, test on real PostgreSQL)

---

## 5. Shared / Final Testing

End-to-end verification, owned jointly.

- [ ] ALC login
- [ ] SBU login
- [ ] Admin login
- [ ] Create activity
- [ ] Save draft
- [ ] Upload evidence
- [ ] Submit activity
- [ ] Admin / SBU receives the submitted activity
- [ ] Admin / SBU requests correction
- [ ] ALC receives the notification
- [ ] ALC edits and resubmits
- [ ] Admin / SBU verifies the activity
- [ ] ALC receives the verification notification
- [ ] Verified metrics update
- [ ] Reports update
- [ ] Cross-ALC access is blocked
- [ ] Cross-SBU access is blocked (one SBU cannot reach another SBU's ALCs/activities/evidence)
- [ ] Evidence from another ALC / SBU cannot be accessed via direct URL or ID
- [ ] Password reset forces the ALC password change (Admin and SBU paths)
- [ ] `must_change_password` lockout works for ALC and SBU in the browser
- [ ] SBU / common-login branches merge cleanly and pass CI
- [ ] Alembic `upgrade head` runs correctly against a copy of production data

---

## 6. Production Readiness

- [ ] PostgreSQL integration tests pass
- [ ] Redis-backed login rate limiting configured and tested
- [ ] Object-storage (MinIO / S3) integration tested end to end
- [ ] Private evidence access, deletion, and key security verified
- [ ] Load / concurrency testing completed (~380 ALCs)
- [ ] Slow queries identified and optimized
- [ ] Security review completed
- [ ] All development secrets rotated before production
- [ ] Demo credentials removed
- [ ] Database and evidence backups configured
- [ ] Monitoring / logging / error reporting configured
- [ ] Frontend production build passes
- [ ] Backend production deployment tested

---

## 7. Production Acceptance Checklist

The project should not be considered production-ready until:

- [ ] All real ALCs are imported (with correct SBU assignment)
- [ ] Admin, SBU, and ALC login work with real accounts
- [ ] Full activity workflow passes end to end
- [ ] Evidence is stored securely
- [ ] ALC data isolation is verified
- [ ] SBU data isolation is verified
- [ ] Admin / SBU verification workflow is stable
- [ ] Notifications are visible and actionable
- [ ] Reports work correctly (including XLSX exports)
- [ ] Password handling is secure
- [ ] PostgreSQL integration tests pass
- [ ] Object-storage integration tests pass
- [ ] Load testing is completed
- [ ] Security review is completed
- [ ] Production secrets are configured
- [ ] Database and evidence backups are configured
- [ ] Frontend production build passes
- [ ] Backend production deployment is tested
- [ ] Monitoring / logging is configured
- [ ] Final Admin + SBU + ALC acceptance test passes

---

## Git / Branch Status

Factual state at the time of writing:

- `main` — base application (FastAPI backend, React frontend, ALC master data). Carries the
  older **role-selector** login and `/alc` routing. ADMIN and ALC roles only.
- `feature/common-login` — adds the unified common login (no role selector). Pushed, **not
  merged** into `main`.
- `feature/sbu-operational-portal` — builds on the common login; adds the SBU role, SBU
  model, `sbu_id` relations, unified `/portal`, the full SBU backend, minimal SBU frontend,
  the SBU Alembic migration, and the SBU tests. Pushed, **not merged** into `main`.
- `feature/password-ui` — currently at the same commit as `main` (no unique work yet).

The SBU work has been pushed to its feature branch but is **not** merged into `main`.

---

## Notes

- Do not use Google Sheets or Apps Script for the production version.
- Local development may use local evidence storage; production must use private object storage.
- Do not count unverified activity metrics as official performance.
- ALC and SBU authorization must always be enforced on the backend.
- One ALC must never access another ALC's data; one SBU must never access another SBU's ALCs.
- Keep audit history for important Admin, SBU, and ALC actions.
- Update this file whenever an item is completed.
