# M3c: lab observability backend implementation

2026-10-04. pCloud registered worktree
`C:\Users\amaze\.codex\worktrees\c580\pCloud`, detached baseline
`9805d6f3cdb355ae33d6d35b283cec447c9ed9b9`. Owner delegated implementation
and selected lab completion before production; M3a/M3b staged work is preserved.
The [contract](../CONTRACT.md) still governs actual environment changes.

## Bounded scope

Implement [ADR-0003](../adr/0003-lab-observability-filesystem.md) under
`deploy/02-cluster-addons/storage/observability-filesystem/`: separate persistent
claims, supported pinned storage fragments and capability, explicit installed
and supplied-claim profiles, read-only checks, reviewed isolated POSIX/remount/
UID-denial probes, safe cleanup/retained-volume inventory, runbook, offline tests
and CI/dispatcher registration. No S3 service, LGTM server, provider installation,
business secret/configuration, shared-data mutation or production profile.

## Implementation acceptance

- Schemas reject unsupported profiles, secret fields, repeated claims,
  missing supplied UIDs and oversized requests; backend capabilities state limits.
- Exact pinned source/hash records; every durable fragment path is on its own
  declared full mount, and Mimir blocks/rules/Alertmanager paths do not collide.
- Copied package renders without sibling dependencies; real Kubernetes schemas
  validate claims and restricted probe Pods. Linux tests exercise actual POSIX
  operations, reserve failure and unknown-file/symlink preservation.
- Static/Live checks make no cluster changes. Smoke requires reviewed source
  digest/revision and consent, uses only run-owned claims/Pods/namespace and UID
  deletes, and preserves every PV. Failed/uncertain cleanup never reports PASS.
- Root tracked-entry/CI guards and both PowerShell dispatcher suites pass;
  Ubuntu CI calls the same entry, including real POSIX tests.

Implementation evidence is recorded in the package. No deployment/live
acceptance follows from static tests. Installation requires accepted storage,
measured capacity, actual site inputs and environment/revision approval.
M3c lab exit requires mounted filesystem/remount/UID checks, retained synthetic
PV disposition and M4's pinned server startup, ingest/query, retention and
known-data recovery. M4 implementation can proceed from this explicit handover;
its installation/acceptance still needs all prerequisite evidence.
