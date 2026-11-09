# Dependency remediation — 2026-11-04

Fresh scans were performed against the repository lockfile and the application virtual
environment. No `npm audit fix --force` was used.

| Scan | Before | After |
| --- | --- | --- |
| npm complete tree | 21 affected entries: 2 critical, 3 high, 16 moderate | 12 moderate, zero high/critical |
| npm runtime (`--omit=dev`) | 1 high (Zod) | Zero advisories |
| Python virtual environment | 16 advisory entries across five packages, including duplicate IDs | Zero known advisories across 59 installed distributions |

Frontend changes: Zod 4.6.7, resolvers 7.0.2, react-hook-form 7.91.0, Vite 8.3.5,
Vitest 5.1.0 and React plugin 6.2.1, plus compatible parent/tooling updates. The lockfile
records the exact closure. The ActivityEditor form now distinguishes Zod input from coerced
output with `useForm<z.input<typeof schema>, unknown, FormValues>`; existing validation and
all form regressions remain enabled. Tooling was tested with Node 22.13.1. Vitest 5 needs
Node 22.12 or later; repeat CI/install checks on the release image. References:
[Zod migration](https://zod.dev/v4/changelog), [Vitest migration](https://vitest.dev/guide/migration/).

The remaining twelve npm entries are the development ESLint/typescript-eslint parent tree
and minimatch `<11.0.1` (GHSA-g3hv-75wg-m249). They do not enter the production bundle;
lint glob patterns are repository-controlled. This reduces application reachability but
does not erase the scanner findings. A minimatch major override could change glob behavior;
the scanner's proposed typescript-eslint downgrade was not applied. Track a tested parent
release and avoid exposing lint tooling to untrusted patterns.

Python upgrades: PyJWT 2.15.1, certifi 2026.10.23, dnspython 2.9.1, pytest 9.2.0,
pytest-asyncio 1.4.1.post1 and setuptools 84.2.1. The pytest fixture loop scope is explicitly
function, matching the test isolation model. No token algorithms, expiry/type checks or
persistent-family validation were relaxed.

Individual advisory review:

- certifi PYSEC-2026-4320: compromised CA trust matters to outgoing TLS clients; updated the trust bundle.
- dnspython CVE-2026-114605: DNS wire-name decompression CPU risk; email-validator is present, while an externally controlled wire-parser path was not established. Updated rather than assuming it is unreachable.
- PyJWT PYSEC-2026-4141 / PYSEC-2026-4183: JWKS unsigned payload handling and mismatched OKP private JWK material. This application uses explicit HS256 secrets, without PyJWKClient/OKP imports; upgraded and reran real authentication/session tests anyway.
- pytest PYSEC-2026-1845: UNIX temporary-directory issue in development tooling; upgraded despite this Windows run.
- setuptools PYSEC-2022-43012, PYSEC-2025-49, PYSEC-2026-1918, PYSEC-2026-3447 and GHSA-xmj3-9gf7-h52w: package download/build/install paths, including Windows legacy-wheel handling. These are build-tool risks rather than portal endpoints; upgraded the build environment and project minimum.

`apps/api/constraints-runtime.txt` and `constraints-dev.txt` pin the resolved application
closures using existing pip tooling. Install from `apps/api` with
`python -m pip install -c constraints-runtime.txt -e .` (or dev constraints and `-e '.[dev]'`).
They were resolved on Windows/Python 3.11. They are not hashed cross-platform lockfiles;
regenerate and validate the Linux release closure before deployment. No new dependency
management ecosystem was introduced. pip-audit was invoked with `--path` targeting the
actual virtual environment, so separately installed audit-tool packages did not mask its
versions. Raw scans remain in ignored `.local/master-*-final.json`.

Pip build isolation is not a fully pinned build-tool lock: the setuptools minimum remains a range in the build environment. Freeze and validate the release build environment separately; these constraints do not establish complete cross-platform artifact reproducibility.
