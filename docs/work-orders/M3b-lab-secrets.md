# M3b: lab secrets implementation

Date: 2026-10-04. Product: pCloud. Source baseline:
`9805d6f3cdb355ae33d6d35b283cec447c9ed9b9`, detached registered worktree
`C:\Users\amaze\.codex\worktrees\c580\pCloud`. Existing staged M3a preserved.
Owner authorized Codex to implement under the Claude engineering role and
selected lab completion first. [Contract](../CONTRACT.md) governs deployment.

## Bounded scope

Implement [ADR-0002](../adr/0002-lab-secret-management.md): standalone OpenBao
lab and supplied API profiles, explicit inputs/capability, pinned source/images,
restricted TLS/Raft manifests, projected reviewer, guarded bootstrap/conformance,
sensitive snapshot export and isolated restore, operations/recovery runbook,
offline tests, render/schema checks and root CI/dispatcher registration.
No application secret/role, IOT-EE dependency, production topology or automatic
cluster/host operation. Initialization/unseal remain external operator actions.

## Implementation acceptance

- Package copied to an unrelated directory renders/tests without sibling files.
- Schemas reject credentials and unsafe/mixed profiles; rendered server is
  persistent, non-dev, restricted, TLS-enabled and uses retained storage.
- Read-only Live checks never mutate infrastructure; mutations require reviewed
  revision/digest and distinct consent. Snapshots never enter product paths.
- Conformance checks scoped identity/access, versioned CAS rotation, revocation,
  audit and owner/UID cleanup. Failed checks/cleanup never count as PASS.
- Local TLS exercises trusted CA success, hostname/untrusted-CA rejection and
  blocked token-bearing redirects. Real Kustomize and pinned Kubernetes schemas pass.
- Package entry is tracked, documented and directly invoked by CI; root layout
  and both PowerShell dispatcher suites pass. Static CI remains separate deployment.

Implementation results are in the package's dated static evidence. Actual binary,
remote CI and lab behaviours are not proved by local tests. Deployment requires
actual context/storage/capacity/TLS/network inputs, operators/key custody and
reviewed revision. M3b lab exit requires live authorization/rotation/audit evidence,
observed restart/manual reunseal, off-host snapshot custody and isolated known-data
restore. That work remains pending; no installation was performed.
