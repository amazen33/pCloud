# M3c: monolithic observability filesystem backend

Standalone **lab-only** durable filesystem handover for Loki **3.7.8**,
Tempo **2.10.8** and Mimir **2.17.11**, each as one monolithic instance.
The exact pinned upstream examples, filesystem implementation and AGPL-3.0
licences have source URLs and SHA-256 records in `UPSTREAM.json`.

This package creates a restricted namespace and three explicit RWO filesystem
claims, exports storage-only configuration fragments and checks filesystem
conformance. It installs no observability server, S3 service, storage
provisioner, application or secret role. M4 owns the LGTM/Collector servers,
API access, retention and ingest/query/component recovery. A storage-fragment
or PVC pass does not prove those services run. No deployment is claimed.

## Profiles and boundaries

| Profile | Installs / consumes | Limits |
| --- | --- | --- |
| `lab-filesystem` | Restricted namespace plus one explicit claim each for Loki, Tempo and Mimir, using an already accepted Retain/WFFC class | Node-local, single instance; no S3 endpoint, replication, enforced write quota, snapshot API, HA or automatic backup |
| `supplied-filesystem` | Existing namespace and exact existing claim UIDs; renders nothing | Must pass the same filesystem/affinity checks; cannot delete or migrate supplied data; needs delegated isolated-test provisioning permissions for Smoke |

The selected alternative to an object-storage service is each component's
native local filesystem backend. It is restricted to this monolithic lab
version set. Distributed mode, multiple replicas, NFS and production profiles
are unsupported. Mimir uses separate blocks/ruler/Alertmanager directories;
its `filesystem` backend is distinct from the read-only `local` rule backend.
Changing to a new major version or object store needs a new reviewed migration.

## Prerequisites and input contract

Python 3.12+, dependencies in `requirements.txt`, explicit Kubernetes context
and a healthy Kubernetes **1.35** cluster. Supply the capabilities of an
accepted storage provider through `site.json`, never its files/state.
This package needs Retain, WaitForFirstConsumer, Filesystem, ReadWriteOnce and
an exclusive worker binding. The underlying filesystem must support directory
and file fsync, mmap, exclusive flock and atomic rename. These are tested,
not inferred from a provider name. A class alone is not acceptance evidence.

Copy `site.example.json` or `supplied.example.json` to ignored `site.json`.
`site.schema.json` rejects unknown fields, extra credentials, shared claims,
missing supplied UIDs and budgets below the three requested sizes. Inputs:

- `profile`, `context`, `namespace`, class and provisioner;
- each component's unique claim, requested GiB and explicit worker; supplied
  claims also require their actual UID;
- `max_total_gib`, the reviewed total request budget, and minimum byte/inode
  reserves; at least **20%**, **2GiB** and **10,000 free inodes** are required.

The example is 4Gi Loki + 4Gi Tempo + 8Gi Mimir. It is provisional sizing,
not evidence the historical lab can fit it. Reconcile with OpenBao data/audit,
RKE2, future Grafana/Collector and all existing workloads. Measure host thin-disk
growth, each worker's filesystem/inode reserves and memory before deployment;
the size budget and PVC requests are **not quotas or actual free-space proof**.
Smoke adds three 1Gi test claims whose retained volumes require disposition.

The package owns its claim labels and `pcloud.io/backend-owner` namespace label.
M4 owns workloads in that protected namespace and must preserve restricted
PSA **v1.35**. Limit who may create Pods or mount claims in this namespace:
filesystem UID tests do not replace Kubernetes authorization. No provider
SA, ClusterRole or host mount is installed here.

## Versioned outputs and M4 handover

```bash
python backend.py capability --site site.json --output /operator/handover/backend.json
python backend.py fragments --site site.json --output /operator/handover/storage-fragments.json
```

`capability.schema.json` defines `pcloud.observability-backend/v1`. Consumers
receive these files explicitly through their own configuration, never import
this package's Python or inspect sibling directories. The capability includes
component versions, claim/worker mapping, full mount path, security identity,
fragment hash and limitations. To verify the fragment hash, compute SHA-256
of Python `json.dumps(fragment_object, sort_keys=True).encode()`; the objects
contain ASCII keys/values only. A handover is not a live acceptance certificate.

M4 must:

1. Use exactly one monolithic replica per component and the declared version.
2. Mount that component's entire separate PVC at
   `/var/lib/pcloud/observability`, with no subPath or shared claim. Apply the
   declared worker affinity without using `nodeName` (WFFC needs the scheduler).
3. Run UID/GID/fsGroup **10001**, `fsGroupChangePolicy=OnRootMismatch`,
   restricted settings and no root ownership init container. The storage
   provider's supported fsGroup behaviour must pass the probes.
4. Merge every storage fragment into its **complete** reviewed runtime config;
   the fragments are not runnable server configs. Persist Loki chunks/rules,
   active TSDB index/cache, WAL and compactor/ruler work; Tempo blocks, trace
   WAL and metrics-generator state; Mimir blocks, TSDB/WAL, index sync,
   compactor, rules and separate Alertmanager bucket/runtime state.
5. Set authentication/TLS/network boundaries, ring/replication, retention,
   resource and ingest/query limits itself. Fragments set none of these.
6. Verify actual pinned binary startup, synthetic ingest/query, block flush,
   process/Pod restart and known-data restore. Never claim filesystem tests
   prove a valid complete server configuration or a supported upgrade path.

## Standalone static validation

From the package directory or use absolute paths from any directory:

```bash
python -m pip install -r requirements.txt
python tests/verify-backend.py
python tests/verify-backend.py --render
```

The unchanged entry point is called directly by Ubuntu CI. It checks schemas,
claim/security/affinity guards, source/image hashes, fragments and copied-package
execution, then runs simulated API lifecycle success and failure cases.
**Linux** additionally executes actual POSIX operations on owned temporary files,
including unknown-file/symlink preservation and reserve rejection. Windows
reports those three tests as skipped: a Windows static pass proves its performed
checks, not POSIX semantics. Run the exact entry on a Linux station or an
existing WSL distribution for a complete offline pass.

`--render` requires kubectl **1.35.0** and kubeconform **0.7.0**. It runs
real local Kustomize and strict **Kubernetes 1.35.0** schemas for four
permanent objects and every probe action (12 Pods). Public schemas may be
downloaded; no cluster is contacted. The probe image is official Python
**3.14.8-alpine**, locked by immutable OCI manifest digest, not the rolling tag.
No test runs Loki, Tempo or Mimir or proves a mounted Kubernetes filesystem.

## Reviewed installation and read-only checks

Provide a concrete cluster, measured capacity and reviewed revision under
the deployment gate; review the render, server dry run and diff:

```bash
python backend.py render --site site.json > /operator/review/backend-claims.yaml
python backend.py preflight --site site.json
kubectl --context ACTUAL-CONTEXT apply --dry-run=server -f /operator/review/backend-claims.yaml
kubectl --context ACTUAL-CONTEXT diff -f /operator/review/backend-claims.yaml
# Apply only through the separately authorized environment/revision gate.
python backend.py live --site site.json --output /operator/evidence/backend-live.json
```

`preflight` checks server version, class, workers, namespace ownership/PSA and
all existing claim identities without mutation. `live` checks claim binding,
Retain, provisioner, access and exclusive node affinity. With WFFC, new
permanent claims may remain Pending until the M4 consumer schedules them.
That state is **INCOMPLETE**, not failed persistence or completed acceptance.
Bound claims also remain INCOMPLETE until POSIX and M4 API/recovery evidence.
The supplied profile is neither installed nor changed by render/preflight/live.

## Isolated filesystem conformance

Make ignored `review.json` with exactly `revision` (40-hex source commit) and
`artifact_digest` from `python backend.py digest --site site.json`. The digest
binds actual runtime/probe source, locked contracts/images, schemas, selected
inputs, claims and fragments. Review any staged/uncommitted patch separately;
a baseline commit alone does not attest the patch.

```bash
python backend.py smoke --site site.json --review review.json --allow-cluster-changes \
  --output /operator/evidence/backend-smoke.json
```

Smoke requires separately approved environment/revision and rights to create
isolated namespaces/claims/Pods, read Nodes/PVs and all namespaced API metadata,
read probe logs and UID-scoped deletion. It creates a unique
`pcloud-backend-test-*` namespace, never mounts permanent or supplied claims.
For each role it creates one 1Gi temporary claim on the explicit worker and:

- Runs a non-root writer using bounded synthetic files, byte/inode reserves,
  fsync, mmap, exclusive flock and atomic rename.
- Removes that Pod with its original UID precondition, creates a new reader
  and verifies the synthetic hash after remount.
- Runs UID/GID 10002 with no fsGroup and a read-only mount, requiring access
  to the UID-10001 mode-0700/0600 synthetic marker to be denied.
- Rechecks bound identity/Retain/affinity; removes only the two exact verified
  synthetic files and their owned directory. Unknown files, changed identities
  or symlinks stop cleanup. No recursive volume/root deletion is used.
- Deletes only the original test PVC/Pods by UID after confirming Retain,
  inventories retained PV names/UIDs/claim UIDs and audits all listable
  namespaced types before deleting the original test namespace. Unknown objects
  or finalizers are preserved and fail the run.

The class is not stopped or deleted; PVs are never deleted or switched to
Delete. **INCOMPLETE / exit 3** remains until the operator reviews the retained
synthetic PV disposition and M4 evidence. Failed/partial operations report
FAIL and preserve uncertain resources; investigate the recorded run namespace.
Removing an empty retained test volume/base directory is a distinct approved
provider operation with confirmed PV/claim/mount ownership; this package
does not make that destructive decision. Do not repeat probes indefinitely
without disposing their retained capacity.

## Upgrade, backup, recovery and removal

Version changes require new upstream contracts, locked images, fragment checks
and actual isolated server compatibility/recovery evidence in M4. Preserve
existing claim identities; this package does not resize, migrate or rewrite
stored data. Failed configuration upgrades use the previous reviewed config
only when binary/data compatibility is established. Do not downgrade binaries
against changed indexes/blocks without a rehearsed restore.

No backup automation or snapshot API is supplied. For lab recovery:

1. Define the acceptable data-loss interval and maintenance outage with the owner.
2. Stop ingestion, drain/flush components through their supported APIs, then
   stop the exact workload instances under the reviewed maintenance gate.
   A live directory copy does not prove a consistent WAL/block/index backup.
3. Have the storage operator capture **every** component PVC's quiesced tree,
   ownership/modes, matching config/version and integrity manifest into private
   off-host storage. Do not put archives, backup trees or source recovery
   material in this product. Record flush/stop evidence and any uncertainty.
4. Restore to new dedicated volumes in an isolated namespace, preserving
   ownership/modes and matching component versions/config. Never overwrite
   existing platform/supplied volumes. Re-run filesystem checks, then M4
   known-log/metric/trace queries, access denial and alert/service-graph checks.
5. Resume only after operator evidence/signoff. Preserve the source and backup
   on failure; an isolated verified restore and reviewed cutover are separate.

Retain is not a backup; node-local data becomes unavailable on worker loss,
and one Hyper-V host is one failure domain. No HA, DR or RPO/RTO is claimed.
At inode/byte exhaustion ingestion or queries can fail before configured
retention takes effect. M4 must monitor space/inodes, backlog, rejected writes
and compaction, with measured growth/retention and an owner-approved soak.

`python backend.py uninstall-check --site site.json` is read-only, rejects
permanent claims and retained PV references, and still requires an operator
data-disposition review before removal. No automatic data deletion or supplied
namespace/claim removal is implemented.

## Evidence

Reports go outside all repositories and contain identifiers, hashes, status
and safe retained-PV inventory only; raw command/API diagnostics are suppressed.
Record actual context, source/digest and dirty status, measured capacity,
provider/PV identities, POSIX and remount/UID denial, cleanup/disposition,
complete M4 runtime/API/retention/restore and owner soak evidence.
See [local static evidence](evidence/2026-10-04-static.md).
