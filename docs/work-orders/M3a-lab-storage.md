# M3a -- Claude implementation work order: lab filesystem storage

**Status:** Implemented locally under the owner's instruction for Codex to
take the Claude engineering role and complete the lab profile first. The owner accepted the
M3a lab profile and namespace security exception on 2026-10-04 in this
conversation; see ADR-0001. Installation, isolated smoke and separately
authorized serial worker reboot/cleanup passed on 2026-10-05; see
[the dated lab acceptance](../../deploy/02-cluster-addons/storage/local-path/evidence/2026-10-05-lab-reboot.md).
M3a's required node-local lab gates are complete. This implementation work
order and design acceptance do not themselves authorize host/cluster changes.

## Outcome and scope

Implement the [accepted storage ADR](../adr/0001-lab-persistent-storage.md)
as one standalone Layer 2 package. It must provision lab filesystem PVs,
describe its limits in a versioned capability file, and prove its guards
without any IOT-EE code or another package's inventory/state.

The profile and namespace exception have owner acceptance. Follow
[the working contract](../CONTRACT.md), report
preflight and confirm one writer. Baseline for this work order is
`9805d6f3cdb355ae33d6d35b283cec447c9ed9b9` plus the reviewed M3a documents;
record the actual starting revision and any unrelated changes.

Target product: pCloud. Canonical checkout: `D:\project\pCloud`; this
architectural preparation used
`C:\Users\amaze\.codex\worktrees\c580\pCloud`. Use the owner-assigned
existing checkout, confirm its root/origin, and use a `codex/` branch if
a new branch is needed. Do not create nested repositories or read IOT-EE.

The original layout guard rejected this managed worktree's `.git` pointer.
The separately documented [G1 governance scope](G1-registered-worktree-validation.md)
adds verified linked-worktree support with negative regression tests. No
Git metadata is rewritten and no layout check is bypassed.

Implementation includes source, installation/upgrade/rollback documentation,
static tests, independent read-only preflight and explicitly gated isolated
smoke checks. It excludes disk preparation/formatting/migration, live apply,
node power changes, secrets, object storage, observability and Crossplane.
Report a prerequisite gap rather than modifying Layers 0 or 1 to fill it.

## Allowed files

| Area | Intended changes |
| --- | --- |
| `deploy/02-cluster-addons/storage/local-path/` | README, local manifests/Kustomize, configuration schema/example, capability contract, source provenance, package test entry and fixtures, Live/Smoke scripts, ignored site inputs |
| `tests/layout-manifest.json` | Register the implemented package's single static entry point |
| `tests/run.ps1`, `tests/run.Tests.ps1` | Add storage static/render, Live and Smoke catalog entries and prove selection, missing prerequisites and mutation consent |
| `.github/workflows/infra.yml` | Directly invoke the identical package entry point with render/schema validation; static only |
| `tests/README.md`, `README.md`, `deploy/README.md` | Document the implemented package and accurate status; retain historical evidence |
| `deploy/02-cluster-addons/README.md` | Describe the separate storage package without adding it to kube-vip's root Kustomize render |
| M3a ADR, milestone tracker, Layer 3 planning README | Record reviewed decisions, capability references and actual status without claiming live readiness |

Preserve kube-vip package independence. A copy of the storage directory must
test/render from an unrelated working directory, with no parent files.

## Implementation requirements

1. Vendor the reviewed v0.0.37 source locally, record its URL, source digest
   and licence. Record patches separately. Pin controller, helper and smoke
   images; resolve real image digests, never invent them. No remote render
   resources, latest tags or implicit unpinned helper-image fallback.
2. Use namespace `pcloud-storage-system`, controller identity
   `pcloud.io/local-path`, and class `pcloud-local-retain`. Explicitly set
   local volume type, Retain, WaitForFirstConsumer, non-default and disabled
   expansion. Preserve existing StorageClasses and controllers.
3. Render from explicit site configuration, with schema validation. Require
   profile, context, eligible worker names/label, mount root/filesystem
   identity, capacity budget/free-space floors and reviewed resource limits.
   Real node names, kubeconfig, capacity observations and credentials stay
   ignored. An example must not select the historical lab by default.
4. Allow paths only on explicitly eligible workers; default/non-listed path
   list is empty. Assert matching consumer scheduling and class topology.
   Refuse control-plane eligibility, duplicate nodes/paths, root/system/RKE2
   paths, traversal, symlink escapes and conflicting existing ownership.
5. A read-only node preflight checks the supplied filesystem with mount
   identity, free bytes/inodes, permissions and marker identity. It also
   verifies Ready workers, no DiskPressure, controller/class collisions,
   expected Kubernetes version, admission/RBAC boundaries and host capacity
   evidence. No writes in Live mode, including server-side dry-run calls.
6. Setup must refuse an absent/wrong backing-filesystem marker before data
   writes; test behaviour after a mount is missing. Do not rely on a
   container's bind mount to prove the underlying node filesystem is mounted.
   Path guards apply to teardown too. Preserve upstream safe-template and
   safe-path behaviour; do not enable unsafe overrides.
7. Harden the controller and bound resources for controller/generated helper
   pods. Document the host-access exception from the ADR. Inspect actual
   generated helper security settings rather than claiming the template
   meets restricted Pod Security. Consumers/smoke workloads must meet
   restricted Pod Security with non-root write permissions proven.
8. Publish storage capability v1 as validated non-secret JSON (with a schema):
   profile, class/provisioner, filesystem/access mode, binding/reclaim,
   node selection, requested-vs-enforced capacity, limits, lack of
   replication/snapshots/backup, recovery effect and component version.
   Provide the JSON as explicit consumer configuration; no sibling reads.
9. `supplied-storage` installs nothing. Validate supplied declared
   capabilities with the shared conformance checks; it may have stronger
   features, which must be tested separately before advertising them.
   No installer/admin rights are assumed for this profile.
10. Document reviewed render, diff, server-side dry run and application as
    separately authorized mutation operations. Bind installation review to
    the chosen context and actual artifact digest. No script default applies
    resources; Static and Live remain free of cluster writes.

## Validation and failure cases

Use a package entry point named `verify-storage.py` in its `tests` directory.
Default mode is offline; `--render` requires kubectl and kubeconform v0.7.0
and validates local renders, generated helper templates and smoke resources
against Kubernetes 1.35.0 schemas. Pin PyYAML 6.0.3. Any other schema/tool
pin must be reviewed and documented. All subprocess failures propagate.

At minimum, meaningful negative cases must prove rejection of an unpinned
image/helper fallback, default StorageClass, Delete reclaim on the consumer
class, Immediate binding, unsafe path/template options, root/RKE2 paths,
symlink escapes, control-plane/non-listed nodes, wrong/missing mount marker,
missing capacity input, unsupported profile, permissive consumer security,
and conflicting ownership. Fake tool/node outputs test error handling;
they must never be reported as live acceptance evidence.

Prove a failed render stops validation, a failed schema check fails the
entry point, absence of kubectl cannot become a pass, a missing site file
cannot target a cluster, and denied Smoke consent makes a required run
incomplete. Register all checks through the existing runner conventions.
Do not mark this planned package implemented in the manifest before its
tracked entry point and enabled CI invocation actually exist.

Run both repository guards after implementation, from the root:

```powershell
python tests/verify-layout.py
python tests/test_verify_layout.py
```

Run the new package entry point, its render/schema mode, dispatcher tests
on available PowerShell runtimes, and the unchanged kube-vip check because
its parent documentation is edited. Run each changed package's documented
entry point before a push. Missing tools make verification unavailable,
not passed. A push needs the owner's applicable authorization.

## Live and Smoke acceptance for the later installation work order

Live is read-only API queries plus explicitly permitted read-only node
checks. Smoke changes state and requires an owner-authorized cluster,
reviewed revision, designated test namespace and explicit runner consent.
Supply bounded timeouts and run-labelled resources. Do not borrow an
application PVC, namespace or production dataset.

| Check | Required evidence |
| --- | --- |
| Delayed binding | New test PVC stays Pending without a consumer, then binds to an eligible worker after a correctly scheduled consumer exists |
| Data persistence | Non-root restricted pod writes a marker/checksum; replacing the pod while retaining the PVC reads the identical value on the same node |
| Provisioner restart | Restart only the authorized test installation's controller; existing test data remains readable and new test claims provision |
| Retain lifecycle | Deleting a test PVC leaves its PV/data retained; the documented operator rebind procedure restores access to the same marker |
| Missing backing filesystem | Disposable test root/fixture rejects provisioning without the correct marker; never unmount a shared live filesystem to manufacture this case |
| Node affinity | PV binds to the selected worker; incompatible scheduling cannot make data available on another worker. No worker/host outage is inferred from this check |
| Isolation and rollback | Read-only Layer 1/kube-vip checks remain healthy; controller rollback preserves classes/PVs/data; reject uninstall while owned PVs remain |
| Cleanup | Inventory every created object and any retained test data. Delete only run-owned objects/data through explicit scoped cleanup; unresolved retained data makes the run incomplete |

For supplied storage, test only consumer conformance in the designated
scope. Controller restart, host-directory operations and provider rollback
belong to that environment's operator and are not assumed available.

The storage profile provides no backup. Explicitly state permanent-loss
behaviour; a PVC retention test or pod restart does not prove backup/restore.
Keep data until its ownership and exact cleanup target are verified; never
use blanket namespace deletion or recursive host-path cleanup as rollback.

## Completion and handoff

Report separately: implementation completed, static checks passed,
authorized lab installation completed, and lab acceptance completed.
Record exact revision, rendered digest, versions, target cluster, real check
outputs, skipped checks, retained-resource disposition and known limits in
dated package evidence only after those operations actually occur.

For the first code handoff, include changed files, actual checks and CI
status, all implementation deviations and a concrete installation proposal
with prerequisites. M3a remains incomplete until implementation and lab
evidence. Do not fabricate approvals, checksums, filesystem capacity or
results. Next bounded work after accepted M3a is M3b secrets design, with
M3c's data-backend decision required before Layer 3 installation.

## Architecture-preparation verification -- 2026-10-04

- Layout rejection suite: 48 tests passed.
- Existing kube-vip offline entry point: passed (4 local files, 9 resources;
  3 render failure-handling cases using fake tools).
- Local Markdown links: 31 checked across the seven changed documents; all resolved.
- Tracked diff whitespace check: passed.
- Root layout guard: failed with `root .git must be a directory, not a worktree pointer`.
- Storage implementation, real rendering/schema validation, live checks,
  installation and remote CI: not performed by this architecture preparation.

These results assess documentation/repository preparation and the existing
kube-vip package. They provide no storage acceptance evidence.

## Implementation verification -- 2026-10-04

The owner authorized implementation in the Claude engineering role and
selected the lab profile first. The standalone package and its static,
read-only and consent-gated operations are now implemented; see
[actual local checks and limits](../../deploy/02-cluster-addons/storage/local-path/evidence/2026-10-04-static.md).
The [installation proposal](M3a-lab-install.md) identifies missing target,
mount/capacity and revision inputs. Installation and lab acceptance remain
unperformed. No production profile or Crossplane dependency was added.

## Subsequent lab acceptance -- 2026-10-05

The owner separately authorized Stage 1 installation, isolated storage smoke
and the serial worker reboot plan. Their dated records now establish
installation/strict Live, all required isolated smoke checks, automatic
mount and checksum persistence on both workers, actual helper capture and
complete scoped synthetic cleanup; see [lab reboot acceptance](../../deploy/02-cluster-addons/storage/local-path/evidence/2026-10-05-lab-reboot.md).
The earlier architecture/static sections retain their historical limits.
This accepts only the required node-local lab storage gates; other platform
milestones, production HA/DR and backup/restore remain incomplete.
