# Lab filesystem storage

Standalone Layer 2 package for Local Path Provisioner **v0.0.37** on Linux
Kubernetes **1.35.x**. The owner accepted this lab profile and its namespace
exception on 2026-10-04. Stage 1 installation and strict Live checks passed
on 2026-10-05; isolated smoke and cleanup passed, recorded separately in
[the dated smoke evidence](evidence/2026-10-05-lab-smoke.md).

## Profiles and capability

| Explicit profile | Installs | Requires |
| --- | --- | --- |
| `node-local-lab` | One controller, scoped RBAC, ConfigMap and non-default `pcloud-local-retain` class | A dedicated application filesystem on explicitly listed healthy workers, independently mounted from OS and RKE2 data; trusted SSH access; measured host/node headroom |
| `supplied-storage` | Nothing | Existing declared class/provisioner with Filesystem, RWO, Retain and WaitForFirstConsumer; compatible cluster and scoped conformance permissions |

The installed class uses identity `pcloud.io/local-path`, local volumes,
`Retain`, `WaitForFirstConsumer`, and disabled expansion. Unlisted workers
have no provisioner path. Applications explicitly select the class and
use scheduler affinity; setting `pod.spec.nodeName` bypasses the delayed
binding scheduler and is unsuitable for new claims.

`storage.py capability` emits the validated `pcloud.storage/v1` declaration
described by `capability.schema.json`. Pass that JSON to a consumer as an
explicit handover. No script reads another package's inventory/state or
IOT-EE files. Exporting the declaration is not evidence of conformance.

**Limits:** requested PVC size is not a filesystem quota. No replication,
snapshots, expansion, backup, S3 API, worker-failure availability or host
HA is provided. A retained PV survives PVC deletion, not permanent node or
disk loss. Recovering lost data requires an independently supplied backup.
The single-host lab remains one failure domain.

## Source and dependencies

`upstream/local-path-storage.yaml` and `upstream/LICENSE` are unmodified
Apache-2.0 release inputs; `UPSTREAM.json` records source URLs and exact
SHA-256 values. `storage.py render` applies the explicit patches: namespace,
unique RBAC names, controller identity/security/resources, immutable images,
class lifecycle/topology, eligible mounts and guarded setup/teardown.
No remote Kustomize resources are used.

`images.lock.json` records real OCI index digests for the controller and
BusyBox 1.37.0 helper/consumer. Release tags describe provenance; every
render uses `image@sha256`. Offline tests check locks and upstream bytes;
they do not establish a current vulnerability scan or registry signature.
The root artifact-lock utility is a maintenance tool; this copied package
does not need it to render or run tests.

Python 3.12+ with `pip install -r requirements.txt` (PyYAML 6.0.3,
jsonschema 4.25.1), Git Bash or Bash for helper regression execution.
Real schema validation requires kubectl **v1.35.0** and kubeconform **v0.7.0**
on PATH. Live commands also require a separately supplied kubeconfig and
trusted SSH host keys; they never disable host-key validation.

## Static validation

From this directory, or use the absolute script path from another directory:

```bash
python tests/verify-storage.py
python tests/verify-storage.py --render
```

The default uses local fixtures and temporary directories, with no managed
host/cluster contact. It checks configuration/capability schemas, source and
image locks, safety failures, consumer security, actual shell teardown
guards, simulated API lifecycle/failure cleanup, and a copied package from
an unrelated working directory. The simulated API is not live evidence.
If Bash is absent the shell test reports a skip; full static acceptance
requires that test to execute. `--render` additionally checks tool versions,
runs real local Kustomize, and validates all nine installation resources,
the helper template and both smoke objects against strict Kubernetes
1.35.0 schemas. Failed/missing commands propagate as failures.

## Site input and read-only preflight

Copy `site.example.json` to ignored `site.json`, then replace every example
with measured values. The example deliberately names no historical cluster,
uses a documentation address and has a stale host observation.
`site.schema.json` rejects other profiles and unknown inputs.

Specify context, worker names, SSH identities, filesystem UUIDs,
`storage_root`, capacity floors/reserve/projected bytes, host observation
and controller/helper requests and limits. At least 20% free byte/inode
reserve plus the absolute free-byte floor must remain after projected use.
Host thin-disk growth must fit the fresh (preceding 24-hour) host budget.
The script verifies node filesystem facts over SSH, but the host observation
is operator-supplied evidence; retain its measurement source with review.

The application root must already be an exact, canonical mount below
`/var/lib/pcloud/storage/`, on a filesystem distinct from `/` and
`/var/lib/rancher`. A separately reviewed disk-preparation procedure creates
the root-owned, non-group/world-writable marker `.pcloud-storage-ready`
containing exactly `pcloud-storage-v1:<filesystem UUID>`. The root must also
be root-owned and not group/world writable. This package does not create
or format disks, mount them, create the marker or migrate existing RKE2 data.

```bash
python storage.py preflight --site site.json
python storage.py render --site site.json --output rendered.yaml
python storage.py capability --site site.json --output capabilities.json
python storage.py digest --site site.json
```

Preflight is read-only: explicit-context API GET/version calls and a Python
SSH stdin probe using findmnt/stat/statvfs. It creates no node files and
does not perform server-side dry runs. It rejects mount/UUID/permissions,
Ready/DiskPressure/control-plane, capacity and ownership conflicts.
`live` adds installed availability, image/command/security, ConfigMap,
namespace admission, RBAC and class topology checks. Applications must not
be granted access to the storage namespace. Cluster-admin access remains
an operator responsibility; this package does not restrict existing admins.

For `supplied-storage`, use `profile`, `context`, `supplied_class`,
`supplied_provisioner` and `resources` only. Rendering is empty. Class and
server reads are required; no host SSH or installation privileges are
assumed. Extra backend features require their own evidence.

## Security exception

The controller is non-root UID/GID 1000, RuntimeDefault seccomp, drops all
capabilities, has no privilege escalation, and has a read-only root.
Generated helpers need hostPath access and root ownership operations; they
are **not restricted-compliant**. Namespace `pcloud-storage-system` enforces
`privileged` and audits/warns `restricted`, pinned to v1.35. This is the
owner-accepted exception, not a claim that helpers satisfy restricted PSA.
Helper resources and image are pinned, its service-account token automount
is disabled, and unsafe upstream template options remain disabled.

Setup/teardown require the reviewed marker and an exact canonical PVC child
path, reject symlinks/traversal and only operate on that directory. Setup
creates mode 0700 data owned by UID/GID 1000. Consumers must use that
identity (or obtain a separately reviewed permission policy). Upstream
hostPath `DirectoryOrCreate` can recreate the empty mount directory when a
mount disappears; the absent marker then blocks data writes. A marker is
not an in-container mount proof: read-only node preflight provides that
proof. Unexpected remount after preflight remains an operational risk.

## Review, installation and upgrade

Render/digest review covers site/context, rendered manifests and critical
package source hashes. A review records the **actual 40-character reviewed
Git revision**, digest, measured prerequisites and chosen environment.
The revision is an operator attestation for traceability; scripts do not
claim to verify that a supplied string corresponds to a committed clean
checkout. Digest matching binds the executable source and configuration.

After owner authorization for that concrete target/revision, review the
diff, dry run and apply as separate cluster operations:

```bash
kubectl --context <reviewed-context> diff -f rendered.yaml
kubectl --context <reviewed-context> apply --dry-run=server -f rendered.yaml
kubectl --context <reviewed-context> apply -f rendered.yaml
kubectl --context <reviewed-context> -n pcloud-storage-system rollout status deployment/local-path-provisioner --timeout=180s
python storage.py live --site site.json --output reports/live.json
```

Create the ignored reports directory before selecting an output file.
No script automatically applies installation resources. Static CI has no
deployment credentials. A supplied profile skips all installation commands.
Run Layer 1 health and kube-vip read-only checks before/after installation;
save their output outside this package or as reviewed dated evidence.
They are explicit predecessor evidence, never hidden sibling dependencies.

Upgrade is the same review with a newly locked source/image set, schema
checks and isolated smoke first. Do not mutate class binding/provisioner
identity in place, change existing PV paths or move data silently. Existing
data migration and permissions changes require a distinct reviewed plan.

## Gated live acceptance

For a **disposable, isolated installation with no existing consumer PVCs or
PVs of this class**, after separately approved install and preflight:

```bash
python storage.py smoke --site site.json --revision <reviewed-40-hex-revision> --approved-digest <reviewed-sha256> --allow-cluster-changes --allow-controller-restart --output reports/smoke.json
```

This creates a unique restricted test namespace with run labels and records
UIDs. It proves delayed binding, non-root writes, marker persistence across
pod recreation, selected-node affinity, scheduler rejection on a different
healthy worker, controller stop/restore with existing data readable and new
provisioning, Retain release/rebind, actual generated-helper API capture and
scoped cleanup. A second healthy schedulable worker is required for the
affinity rejection; this does not simulate worker or host failure.

Only the authorized storage controller is stopped and restored. Consumer
claims are checked again before stopping it. Concurrent installation/use of
the test class must be excluded by the operator. Cleanup verifies namespace,
object/claim/PV identities and exact local path. It changes **only synthetic
PV** reclaim to Delete, waits for the provider and reads each reviewed worker
to confirm test directories disappeared. It never deletes host paths itself
or changes a consumer StorageClass. Any mismatch or leftover data fails.
The watch is bounded to 900 seconds / 5 MiB. Missing actual helper evidence
fails; an offline template is not substituted.

For `supplied-storage`, omit `--allow-controller-restart`. No provider is
restarted or removed, and no host directories are touched. Without explicit
`--allow-volume-admin` the suite tests ordinary consumer operations and
retention, then reports required operator rebind/cleanup as incomplete.
Even with volume-admin consent, retained data is left to the provider's
operator and exit 3 records its exact PV name/UID. Attach verified operator
cleanup and rebind evidence before accepting supplied conformance. Supplied
storage never assumes authority to change a provider's reclaim policy.

The dispatcher reads ignored `review.json` with only `revision` and
`approved_digest`. Selecting `storage-smoke` with `-AllowClusterChanges`
explicitly selects the isolated controller acceptance described above.
Missing consent, inputs or tools makes the dispatcher incomplete. Package
CLI exits 0 pass, 1 failure, 2 usage error, 3 incomplete. Reports capture actual checks and
retained-resource disposition without credentials/helper logs.

## Rollback, uninstall and recovery

Controller withdrawal/recovery is covered by stop/restore smoke. It preserves
the class, ConfigMap, PVs and data. For a real upgrade rollback, restore only
the previously reviewed controller/config manifest and its immutable image,
then run `live` and a new synthetic claim; do not delete PVCs or downgrade
storage metadata. Version downgrade is not established by the stop/restore
test and requires its own version-specific evidence.

Before uninstall, inventory **all PVCs/PVs for this class**, including Pending
claims. `python storage.py uninstall-check --site site.json` is read-only
and rejects removal while any remain. There is no automated uninstall
command that could remove retained data. After a zero-consumer review,
remove only the nine recorded package-owned objects from the exact reviewed
manifest, and verify Layer 1/kube-vip still healthy. The namespace security
exception disappears only after the owned helper/controller workloads are
gone. Never use namespace deletion as a storage-data rollback.

For a retained consumer PV, an operator verifies PV UID, old claim UID,
node/path and data ownership; removes only the old claimRef with atomic UID
tests; creates a same-class/Filesystem/RWO PVC with explicit `volumeName`;
starts a correctly scheduled UID-1000 consumer; and validates known data.
The smoke implements this procedure solely for its own synthetic volume.
Do not reuse a released application PV for a test.

Namespace cleanup discovers all listable namespaced API types, including
installed CRDs, and inspects names/kinds without logging Secret contents.
Only default service-account/CA objects and events may remain. Unknown
objects or unreadable resource types stop namespace removal; the report
requires operator disposition rather than sweeping unrelated resources.

## Evidence and next gate

See [local static verification](evidence/2026-10-04-static.md) for actual
commands/results and their limits. It contains no live acceptance claim.

The [2026-10-05 Stage 1 installation](evidence/2026-10-05-lab-install.md)
records the authorized worker mounts, measured capacity, nine-object apply,
actual provisioner rollout and read-only Live pass. An API-default comparison
bug found during Live validation is corrected with explicit render defaults
and a regression test; strict drift guards remain enabled.
The [dated isolated smoke](evidence/2026-10-05-lab-smoke.md) records passed
PVC/Pod persistence, actual generated helpers, controller recovery, affinity
rejection, retained-volume rebind and complete synthetic-data cleanup.
Worker reboot/remount persistence remains untested; full lab completion,
backup/restore and production controls are incomplete.
