# SBU Role & Portal Separation — Change Summary

Adds a third account role (**SBU**) between ADMIN and ALC, and splits the app into
**two portal experiences**: a separate Super Admin portal (`/admin/*`) and one shared
operational portal (`/portal/*`) for SBU and ALC. One common login remains; the backend
determines role and enforces all data scope. **Not committed or pushed** — left in the
working tree for review.

## Architecture

```
              ONE COMMON LOGIN  (/login)
                     |
        backend authenticates + determines role
                     |
        +------------+-------------------------+
        |                                      |
      ADMIN                              SBU / ALC
        |                                      |
  SUPER ADMIN PORTAL                  OPERATIONAL PORTAL
     /admin/*                             /portal/*
   (global access)             SBU: assigned ALCs · ALC: own centre
```

- Roles (internal): `ADMIN | SBU | ALC`. ADMIN is shown as **"Super Admin"** in the UI.
- One SBU → many ALCs (`ALC.sbu_id`). One ALC → one SBU. ALC user → its ALC; SBU user → its SBU.
- The frontend never sends a trusted role; every read/write is scoped server-side.

---

## Backend

### Data model & migration
- `app/enums.py` — added `SBU` to the `Role` enum.
- `app/models.py` — new **`SBU`** model (`id, code(unique), name, is_active, timestamps`);
  added nullable `sbu_id` FK + relationship to **`ALC`** and **`User`**.
- `app/schemas.py` — `SbuBrief/SbuOut/SbuIn/SbuPatch`, `PasswordResetIn`; `UserOut` and
  `AlcBrief` now expose `sbu_id`/`sbu`; `UserCreate` accepts `sbu_id`; `AlcStatusPatch`
  now supports optional `status` + `sbu_id` (assignment).
- `alembic/versions/20260918_0002_sbu.py` — **additive, idempotent** migration: creates
  `sbus`, adds `alcs.sbu_id` and `users.sbu_id` (indexes + FKs) using explicit `op` calls
  (no `create_all`). Inspector-guarded so it is correct on both fresh and existing
  databases. No table recreated, no data deleted, no passwords touched.

### Auth & authorization
- `app/routes/auth.py` — login query now matches ADMIN/SBU by username/email and ALC by
  ALC code; role derived from the matched account. Argon2, JWT, refresh rotation, cookies,
  CSRF, active/inactive checks, `must_change_password`, generic invalid-credential message,
  and audit logging all preserved.
- `app/dependencies.py` — new `require_portal_user` (SBU **or** ALC), `require_sbu`;
  existing `require_admin`/`require_alc` unchanged; `sbu` eager-loaded on the current user.

### Routes
- **`app/routes/portal.py` (new, `/api/portal/*`)** — unified operational API for SBU + ALC:
  - Server-side scope helper `scoped_alc_ids(user)` → ALC = own id; SBU = ALCs where
    `sbu_id == user.sbu_id`. Applied to dashboards, activity list/detail, evidence,
    partners, reports.
  - **Shared reads**: dashboard (role-aware), activities, activity detail, partners,
    evidence access/content, notifications, reports CSV.
  - **ALC-only writes** (`require_alc`, `user.alc_id`): create/edit/submit activity,
    evidence upload/delete, partner create/edit, tasks, challenge.
  - **SBU-only** (`require_sbu`): assigned-ALC directory + detail, verification queue +
    review detail, verify / request-correction / reject (reuse the existing
    `review_activity` lifecycle; SBU recorded as reviewer), and ALC password reset.
  - `app/routes/alc.py` **removed** (its logic moved into `portal.py`).
- `app/routes/admin.py` — added **SBU management**: `GET/POST /admin/sbus`,
  `GET /admin/sbus/{id}`, `PATCH /admin/sbus/{id}`; user creation validates SBU role
  (requires `sbu_id`, forbids `alc_id`); `PATCH /admin/alcs/{id}` can assign `sbu_id`.
- `app/main.py` — mounts `portal` router in place of `alc`.

### Scoping / security (enforced in FastAPI + DB, never frontend)
- Every SBU operation checks `target.alc.sbu_id == current_user.sbu_id`; out-of-scope
  → 404. Injecting another SBU's `alc_id` into list/report filters returns 404 / no data.
- ALC ownership isolation unchanged.
- Audit: SBU verify/correction/reject/password-reset logged with actor, role, and target
  `alc_id`/`sbu_id`; passwords never logged. Password **reset** ≠ password **view**.

---

## Frontend

- `types/index.ts` — `Role` adds `SBU`; new `Sbu`; `User`/`Alc` gain `sbu`/`sbu_id`.
- `layouts/AdminShell.tsx` (new) — Super Admin nav (adds **SBUs**), ADMIN-only.
- `layouts/PortalShell.tsx` (new) — role-aware nav shared by SBU and ALC.
  - SBU: Dashboard, ALCs, Activities, Verification, Partners, Reports, Profile.
  - ALC: Dashboard, Add Activity, My Activities, Partners, Tasks, 30-Day Challenge,
    Performance, Growth Resources, Profile.
- `App.tsx` — new routing + guards: ADMIN→`/admin`, SBU/ALC→`/portal`; ADMIN redirected
  off `/portal`, non-admins off `/admin`; role-specific portal pages guarded;
  `must_change_password` locks to the profile flow.
- New SBU pages: `SbuDashboard`, `SbuActivities` (list + verification queue),
  `SbuReviewActivity`, `SbuAlcs`, `SbuAlcDetail` (incl. password reset), `SbuPartners`,
  `PortalReports` (role-aware).
- New admin pages: `AdminSbus`, `AdminSbuDetail`; `AdminUsers` supports SBU role +
  SBU assignment and shows "Super Admin"; `AlcDetail` can assign an ALC to an SBU.
- ALC pages repointed from `/alc/*` → `/portal/*` (Login redirect, AlcDashboard,
  Activities, ActivityEditor, Partners, Tasks, Challenge). Old `components/Shell.tsx` removed.
- Login page unchanged in design (Username/ALC Code, password, show/hide eye, Sign In).

---

## Tests & validation

- `tests/conftest.py` — seeds SBU 4 (Centres A + C), SBU 6 (Centre B), SBU users, admin, ALCs.
- `tests/test_workflow.py` — ported to `/portal` paths.
- `tests/test_sbu.py` (new, 19 tests) — auth for all three roles, generic error,
  must-change-password, admin separation, SBU ALC scoping, activity/evidence/partner
  scoping, verify/correction/reject (+reason, reviewer recorded), cross-SBU denial,
  password reset (allowed/denied/forces change), scoped reports + cross-SBU injection,
  admin SBU/user creation + ALC assignment.

**Results**
- `pytest`: **25 passed** (6 original regression + 19 new).
- `ruff` (changed files): **pass** (only pre-existing, untouched long-line warnings remain
  in `admin.py` evidence endpoints and `scripts/demo_smoke.py`).
- `npm run lint`: **pass** · `npm run build`: **pass**.
- `alembic upgrade head`: verified (correct schema, chain runs).

---

## Files

**Created**
- `apps/api/app/routes/portal.py`
- `apps/api/alembic/versions/20260918_0002_sbu.py`
- `apps/api/tests/test_sbu.py`
- `apps/web/src/layouts/AdminShell.tsx`, `apps/web/src/layouts/PortalShell.tsx`
- `apps/web/src/pages/SbuDashboard.tsx`, `SbuActivities.tsx`, `SbuReviewActivity.tsx`,
  `SbuAlcs.tsx`, `SbuAlcDetail.tsx`, `SbuPartners.tsx`, `PortalReports.tsx`,
  `AdminSbus.tsx`, `AdminSbuDetail.tsx`

**Modified**
- Backend: `enums.py`, `models.py`, `schemas.py`, `dependencies.py`, `routes/auth.py`,
  `routes/admin.py`, `main.py`, `scripts/demo_smoke.py`, `tests/conftest.py`,
  `tests/test_workflow.py`
- Frontend: `App.tsx`, `types/index.ts`, `Login.tsx`, `AdminUsers.tsx`, `AlcDetail.tsx`,
  `AlcDashboard.tsx`, `Activities.tsx`, `ActivityEditor.tsx`, `Partners.tsx`, `Tasks.tsx`,
  `Challenge.tsx`

**Removed**
- `apps/api/app/routes/alc.py` (logic moved to `portal.py`)
- `apps/web/src/components/Shell.tsx` (replaced by the two shells)

---

## Manual testing to do
- Run the real stack (Postgres + Redis + storage); log in as admin, SBU, and ALC; confirm
  redirects and role-correct navigation.
- Run `alembic upgrade head` against a **copy of production data** (only verified on SQLite
  here) and confirm existing users/passwords/activities/evidence survive.
- Exercise SBU verify/correction/reject and ALC password reset end-to-end; confirm one SBU
  cannot open another SBU's ALC/activity/evidence via direct URL/ID.
- Confirm `must_change_password` lockout for SBU and ALC in the browser.

## Follow-up (out of scope, as specified)
- Full ALC master import reading **only** ALC Code, ALC Name, SBU (upsert SBU by code,
  update on existing, create new, reject blank, flag duplicates, never delete missing,
  never touch passwords). Schema is prepared; the importer still reads only Code + Name and
  leaves `sbu_id` null.
