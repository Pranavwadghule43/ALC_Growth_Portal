# ALC Growth Portal — Remaining Work

> Based on the current uploaded project snapshot and the changes discussed after it. Update this checklist as work is completed.

## Current Core Functionality

- [x] Admin login
- [x] ALC login
- [x] Role-based access
- [x] ALC data isolation
- [x] Activity creation
- [x] Draft / submit workflow
- [x] Evidence upload
- [x] Admin verification queue
- [x] Verify / request correction / reject
- [x] ALC resubmission
- [x] Partner management
- [x] Tasks / follow-ups
- [x] 30-Day Challenge base implementation
- [x] Basic notifications backend
- [x] Audit logs
- [x] Basic CSV reports
- [x] Admin user management
- [x] Password reset workflow
- [x] Forced password change after Admin reset
- [x] Local PostgreSQL demo setup
- [x] Local evidence storage for development

---

# Remaining Features

## Priority 1 — Must Finish Before Wider Demo

### ALC Master Data
- [ ] Replace sample ALC data with the real master list
- [ ] Import all approximately 380 ALCs
- [ ] Validate duplicate ALC codes
- [ ] Verify correct ALC code → ALC name mapping
- [ ] Test login with multiple real ALC accounts

### Password Improvements
- [ ] Add Show / Hide Password eye icon on:
  - [ ] ALC Login
  - [ ] Admin Login
  - [ ] Change Password
  - [ ] Reset Password
  - [ ] Temporary Password forms
- [ ] Add Admin-only "View Current ALC Password"
- [ ] Keep Argon2 password hash for login verification
- [ ] Store an additional encrypted password value only if Admin password viewing remains a requirement
- [ ] Keep the encryption key server-side only
- [ ] Add Admin-only password reveal endpoint
- [ ] Audit every password-view action
- [ ] Never log the actual password

### Notifications UI
- [ ] Add notification bell in ALC header
- [ ] Add unread counter
- [ ] Add dedicated Notifications page
- [ ] Add Mark as Read / Mark All as Read
- [ ] Add deep-link to the related activity
- [ ] Improve notification content for:
  - [ ] Activity Verified
  - [ ] Correction Required
  - [ ] Activity Rejected

### Evidence Experience
- [ ] Add image thumbnail gallery
- [ ] Add image preview / lightbox
- [ ] Add PDF preview / open action
- [ ] Show file name, type and size
- [ ] Improve upload progress and per-file errors
- [ ] Improve mobile evidence upload

---

## Priority 2 — Must Finish Before Production

### Admin ALC Import
- [ ] Add Admin CSV Upload page
- [ ] Validate uploaded CSV
- [ ] Show valid / invalid / duplicate row counts
- [ ] Add safe upsert behavior
- [ ] Show import summary
- [ ] Add import audit logs

### Partner Module
- [ ] Add Partner Detail page
- [ ] Add Edit Partner
- [ ] Show partner activity history
- [ ] Show partner follow-ups/tasks
- [ ] Show partner verification/performance summary

### Performance
- [ ] Build dedicated ALC Performance page
- [ ] Use verified metrics only
- [ ] Add date filters
- [ ] Add useful charts
- [ ] Improve Admin performance analytics
- [ ] Add verified-data ALC comparison/filtering

### Reports
- [ ] Improve report filters
- [ ] Add XLSX export
- [ ] Add Activities by ALC report
- [ ] Add Verification Status report
- [ ] Add Learner Reach report
- [ ] Add Leads report
- [ ] Add Admissions report
- [ ] Add Partner report
- [ ] Add Challenge Progress report
- [ ] Allow ALC to export its own activity history

### Settings
- [ ] Make Admin Settings editable
- [ ] Persist settings in the database
- [ ] Validate settings
- [ ] Audit important settings changes

### 30-Day Challenge
- [ ] Review partnership-count logic
- [ ] Fix mismatch between current activity types and partnership calculation
- [ ] Confirm rules for prospects / meetings / pilots / partnerships
- [ ] Ensure official progress uses verified data
- [ ] Add backend tests for challenge calculations

### Database Migrations
- [ ] Improve Alembic migration structure
- [ ] Add explicit migrations for future schema changes
- [ ] Test upgrade / downgrade
- [ ] Document migration process

---

## Priority 3 — Production Readiness

### PostgreSQL
- [ ] Run integration tests against PostgreSQL
- [ ] Verify indexes and connection pooling
- [ ] Verify transaction handling
- [ ] Verify concurrent submissions
- [ ] Verify unique activity-number generation

### Redis
- [ ] Test Redis-backed login rate limiting
- [ ] Test Redis failure behavior
- [ ] Configure production Redis
- [ ] Verify concurrent login limits

### Evidence / Object Storage
- [ ] Test MinIO or S3-compatible storage end to end
- [ ] Confirm private evidence access
- [ ] Verify evidence deletion
- [ ] Verify storage-key security
- [ ] Test large file handling
- [ ] Configure production storage

### Load / Concurrency Testing
Target: approximately 380 ALCs potentially active at the same time.

- [ ] Test concurrent logins
- [ ] Test dashboard requests under load
- [ ] Test concurrent activity submissions
- [ ] Test concurrent evidence uploads
- [ ] Test Admin verification under load
- [ ] Monitor PostgreSQL connections
- [ ] Measure API response time
- [ ] Identify and optimize slow queries

### Security Review
- [ ] Review Admin authorization
- [ ] Review ALC ownership isolation
- [ ] Review evidence authorization
- [ ] Review password encryption design
- [ ] Review password reset behavior
- [ ] Review cookies / CSRF / CORS / secure headers
- [ ] Review rate limiting
- [ ] Rotate all development secrets before production
- [ ] Remove demo credentials
- [ ] Never expose database/storage secrets to frontend

---

## Priority 4 — Frontend / UX Polish

- [ ] Complete npm migration
- [ ] Keep `package-lock.json`
- [ ] Remove pnpm-specific files if no longer used
- [ ] Run `npm install`
- [ ] Run `npm run lint`
- [ ] Run `npm run build`
- [ ] Test desktop / tablet / mobile layouts
- [ ] Test mobile activity submission
- [ ] Test evidence upload from phone
- [ ] Improve empty, loading and error states
- [ ] Improve responsive tables
- [ ] Improve confirmation dialogs
- [ ] Add consistent password eye controls
- [ ] Improve accessibility

---

# Suggested Developer Split

Both developers may work on frontend and backend. Assign one owner per feature until it is merged.

## Developer 1
- [ ] Admin password viewing + encryption backend
- [ ] Full ALC master import
- [ ] Admin CSV import
- [ ] Reports / XLSX
- [ ] Admin analytics
- [ ] Challenge calculation fixes
- [ ] Settings persistence
- [ ] Alembic migrations
- [ ] PostgreSQL integration testing
- [ ] Backend load testing
- [ ] Backend deployment

## Developer 2
- [ ] Password eye controls
- [ ] Notification bell/page/deep-links
- [ ] Evidence gallery and previews
- [ ] Partner detail/edit
- [ ] ALC Performance page
- [ ] ALC Profile improvements
- [ ] Responsive/mobile polish
- [ ] npm migration cleanup
- [ ] Frontend load behavior
- [ ] Vercel frontend deployment

## Shared Final Testing
- [ ] ALC login
- [ ] Create activity
- [ ] Save draft
- [ ] Upload evidence
- [ ] Submit activity
- [ ] Admin receives activity
- [ ] Admin requests correction
- [ ] ALC receives notification
- [ ] ALC edits and resubmits
- [ ] Admin verifies activity
- [ ] ALC receives verification notification
- [ ] Verified metrics update
- [ ] Reports update
- [ ] Cross-ALC access is blocked
- [ ] Evidence from another ALC cannot be accessed
- [ ] Password reset forces ALC password change
- [ ] Admin password-view action is audited

---

# Production Acceptance Checklist

The project should not be considered production-ready until:

- [ ] All real ALCs are imported
- [ ] Admin and ALC login work with real accounts
- [ ] Full activity workflow passes end to end
- [ ] Evidence is stored securely
- [ ] ALC data isolation is verified
- [ ] Admin verification workflow is stable
- [ ] Notifications are visible and actionable
- [ ] Reports work correctly
- [ ] Password handling is secure
- [ ] PostgreSQL integration tests pass
- [ ] Object-storage integration tests pass
- [ ] Load testing is completed
- [ ] Security review is completed
- [ ] Production secrets are configured
- [ ] Database and evidence backups are configured
- [ ] Frontend production build passes
- [ ] Backend production deployment is tested
- [ ] Monitoring/logging is configured
- [ ] Final Admin + ALC acceptance test passes

---

# Notes

- Do not use Google Sheets or Apps Script for the production version.
- Local development may use local evidence storage.
- Production evidence should use private object storage.
- Do not count unverified activity metrics as official performance.
- ALC authorization must always be enforced on the backend.
- One ALC must never be able to access another ALC's activities, partners, tasks, evidence or reports.
- Keep audit history for important Admin and ALC actions.
- Update this file whenever an item is completed.
