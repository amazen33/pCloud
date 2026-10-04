# M4: lab observability implementation work order

Date: 2026-10-04. Source baseline: detached
`9805d6f3cdb355ae33d6d35b283cec447c9ed9b9`; registered worktree
`C:\Users\amaze\.codex\worktrees\c580\pCloud`.
Owner authorized remaining lab implementation in the Claude engineering role
and approved progression from M3c to M4. This is implementation scope, not
approval to apply manifests, touch lab disks or change live resources.
The [contract](../CONTRACT.md) and [ADR-0004](../adr/0004-lab-observability-runtime.md)
govern it. Preserve all earlier staged M3a/M3b/M3c work.

## Files and scope

- Standalone `deploy/03-observability`: lab inputs/schemas, explicit M3c
  interface snapshot/examples, image/source pins, complete runtime configs,
  renderer/capability/digest, read-only preflight/live, guarded API Smoke,
  package tests, optional local executable exercise, docs and evidence.
- Shared registration: `tests/layout-manifest.json`, `tests/run.ps1`, dispatcher
  regressions, direct `infra.yml` invocation, root/deployment/test READMEs,
  milestone tracker, this work order and the decision record.
- One writer, no sibling/product source/state imports, no sub-repositories.

## Implementation acceptance

1. Explicit lab purpose/namespace, retention/resource/TLS/network/Secret inputs;
   reject unknown profiles, unsafe identifiers, duplicate claims and mismatched
   M3c versions/fragment hash.
2. Digest-pinned six workloads, restricted UID/GID 10001, separate existing
   backend claims and own retained Grafana claim; bounded gateways/queues.
3. Separated TLS ingest/query/admin routes, private backend policies, provisioned
   data sources/platform view/service graph and authenticated HTTPS alert route.
4. Versioned capability with explicit no-tenancy/no-HA/no-backup/data-loss limits.
5. Copied package tests without siblings, negative fixtures, real Kustomize and
   strict Kubernetes 1.35.0 schemas, actual pinned Linux runtime validation where
   available; exact local evidence and limits.
6. Root layout guards and dispatcher regressions on PS7/PS5.1; tracked package
   entry directly invoked unchanged by enabled CI. Local declaration checks do
   not claim remote CI ran.

## Live exit gates (pending)

All [package acceptance](../../deploy/03-observability/ACCEPTANCE.md) rows,
including actual predecessor readiness, mounted-data permissions, real policies,
secret custody, telemetry/query/access/graph, receiver receipt, retention expiry,
restart/failure isolation, owner-approved soak and scoped rollback/removal.
Automated Live/Smoke remain INCOMPLETE after their subchecks.

## Non-goals

No automatic deployment, infrastructure apply, disk/VM changes, application
instrumentation, Kafka/APISIX, Crossplane, SSO/IOT tenancy, production, HA/DR,
backup implementation or automatic uninstall. No commit, push or merge in this
work order. Missing deployment inputs do not stop independent implementation.
