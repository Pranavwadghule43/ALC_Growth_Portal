# Yearly Activity evidence workflow

Updated 2026-11-07. This replaces manual YES/NO completion in the current implementation.
Deployment and production browser acceptance remain pending. See the current master execution
report for measured validation and infrastructure limits.

## Identity, membership and migration

Each definition is owned by its immutable `introduced_by_dcu_id`, derived from the authenticated
DCU rather than request data, and is unique by owning DCU, calendar year and normalized activity
code. Creation makes one run for that DCU. Activation snapshots eligible ALC membership and SBU
attribution only below that DCU. Existing transfer/import guards protect unfinished snapshots.
ADMIN retains oversight; DCU, SBU and ALC visibility follows the current owner hierarchy. Older
fan-out run rows remain preserved but cannot bypass definition ownership through a direct UUID.

Migration `20261104_0010` follows `20261103_0009` (persistent sessions), which follows
`20261103_0008` (yearly registry/runs/participation). It adds submissions, evidence and reviews.
One submission references one stable participation row through a unique foreign key. Existing
`yearly_activity_completions.completed` values are retained as legacy history and never promoted
to VERIFIED. Existing finalized runs are not reopened automatically. Legacy YES values alone
award no points. Migration rollback removes the three new tables; it must never be used on a
populated deployment without a separately reviewed data-retention plan.

## State machine and decisions

| State | ALC permissions | DCU permissions | Counts |
| --- | --- | --- | --- |
| DRAFT | Save date/details, add/remove evidence, submit during the initial schedule | See participation state; draft details/proof stay private | No |
| SUBMITTED | Read; ordinary editing locked | Verify, request correction or reject | No |
| CORRECTION_REQUIRED | Edit/add/remove proof and resubmit before finalization | Read submitted history; await resubmission | No |
| RESUBMITTED | Read; ordinary editing locked | Review the new revision | No |
| VERIFIED | Read locked history | Read locked history | Only after regional eligibility |
| REJECTED | Read locked history | Read locked history | No |

SUBMITTED includes the pending-review meaning; there is no redundant UNDER_REVIEW state.
Initial creation/edit/submission requires an ACTIVE or MARKING run and an inclusive open schedule.
Completion date must be within that schedule and no later than the current Kolkata date.
Submission requires at least ten characters of details and one active validated evidence file.
Correction and rejection require DCU remarks; verification remarks are optional. Rejection is
terminal. Correction is the explicit resubmission path, including after the initial deadline
while the run remains open. ADMIN cannot override review decisions.

Each submission/resubmission increments a revision. Review requires the revision seen by the
reviewer; a repeated/stale/concurrent decision returns 409. There is one immutable review per
revision, including decision, reviewer, previous status, remarks and a snapshot of completion
details and submitted evidence metadata. Removed files are inactive in the current revision but
remain available as historical proof. New correction evidence remains private until resubmission.

Finalization requires MARKING, an ended schedule, and no pending SUBMITTED, RESUBMITTED or
CORRECTION_REQUIRED cases. DRAFT and never-submitted participants can close without points after
the deadline. This conservative policy prevents an unresolved correction from being silently
closed. Finalized history is locked. Run share locks and submission row locks serialize writes;
finalization takes the run's exclusive lock. Unrelated submissions can proceed independently.

## Authorization and private delivery

ALC writes only its own applicable snapshot submission. Responsible DCU reviews only its current
authorized hierarchy and its own run. SBU reads only currently authorized ALCs. ADMIN has regional
oversight and read access. Server-side scope applies to every detail, evidence access and byte
request; possessing an object key or UUID grants no permission. Cross-scope resources return 404;
disallowed mutation roles return 403. All unsafe routes retain the existing CSRF requirement.

The retired manual marking API returns 410 and cannot award points. Compatibility participation
responses expose legacy history distinctly and derive current completion from submission state.

Evidence uses the existing local/S3 storage service, under
`yearly-evidence/<alc-id>/<submission-id>/<evidence-id>.<extension>` in the configured private
bucket. No public or presigned storage URL is returned. The authenticated API streams bytes with
private/no-store, nosniff and safe disposition headers. JPEG, PNG, WebP, PDF, MP4 and WebM are
allowed only with matching extension, declared MIME and supported signature. These checks detect
containers, not malware or every corrupt/unsupported codec; production content scanning and
browser codec acceptance remain operational requirements.

Limits remain 10 MiB per file, ten active files, fifty historical files per submission, and one
file per request. The yearly multipart body cap is the configured file cap plus 128 KiB, enforced
for declared and chunked bodies before unbounded spooling. Incoming body receipt has a total
60-second deadline; bounded file read/storage operations have a 45-second deadline. Read timeout
returns 408, oversize 413, type mismatch 415 and storage failure 503. Temporary multipart files
use the server temp directory; payload validation reads at most the file limit plus one byte.
Production must bound concurrent requests and provide a private, capacity-monitored temp volume.

Single byte ranges support video seeking with 206/416. The shared storage interface currently
opens the object and skips bounded chunks to the range start; it does not issue native S3 ranged
GETs. File size remains capped. Old activity evidence routes retain their established behavior.
Retained historical objects need a documented retention policy and orphan reconciliation;
cleanup failures log only internal evidence ID and exception type.

## Ranking and audit

Verified Leads, Verified Admissions, Verified Activities, Active Partners and Yearly Activity
Completion each retain exactly 20% through the shared Fraction-based scoring service. The fifth
metric counts distinct DCU-owned definitions with VERIFIED submissions for the selected year,
only after the owning DCU run is finalized. Local verification can precede scoring.
Draft/upload/submission/correction/rejection and extra evidence/reviews never
increase this count. The full regional population, exact ordering, deterministic ALC Code ties
and display-only DCU/SBU filters remain shared by Top 10 and All ALC Scores.

Creation, editing, evidence addition/removal, submission, resubmission and each review decision
write existing audit records without media, credentials or JWTs. The UI has paginated scoped
queues, ALC/SBU/status search, annual counts, correction remarks, evidence previews and explicit
confirmation before decisions. SBU and ADMIN have no mutation controls.

## Deployment conditions

Apply 0009 and 0010 with the matching code in a coordinated rollout; old tokens without a session
ID fail closed and require fresh login. Back up both the database and private objects first.
Configure the proxy to permit a 10 MiB file plus multipart overhead (for example an 11 MiB body
cap), a 60-second client body deadline, and an upstream timeout above the application/storage
budget (for example 90 seconds). These are local engineering settings, not production throughput
guarantees. Retest on the actual worker count, TLS proxy, database, Redis, object store and network.
