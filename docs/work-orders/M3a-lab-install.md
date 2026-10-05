# M3a: lab installation and acceptance gate

Historical proposal from 2026-10-04. The owner subsequently authorized Stage 1;
see [2026-10-05 installation and remaining acceptance gates](../../deploy/02-cluster-addons/storage/local-path/evidence/2026-10-05-lab-install.md).
The inputs/status below describe the original proposal, not the current installation.

**Status:** Package implemented and statically validated; target inputs
missing. This is an installation proposal, not an approval or execution
record. Follow `docs/CONTRACT.md` sections D-F. No VM, disk or cluster
change has occurred in this implementation work.

## Reviewable result

- Package: `deploy/02-cluster-addons/storage/local-path/`, described in its
  [runbook](../../deploy/02-cluster-addons/storage/local-path/README.md).
- Source baseline: `9805d6f3cdb355ae33d6d35b283cec447c9ed9b9`, plus staged
  local changes. Produce an actual reviewed revision before deployment.
- Profile: accepted `node-local-lab`, Local Path Provisioner v0.0.37,
  immutable controller/helper/consumer images, nine local resources.
- Namespace: `pcloud-storage-system`, accepted hostPath/helper PSA exception.
- Class: non-default `pcloud-local-retain`, Filesystem/RWO,
  WaitForFirstConsumer, Retain. No replication, quota, backup or S3 claim.
- Installation never changes kube-vip's root render or another class's
  lifecycle, and does not create/format/mount disks or migrate RKE2 data.

## Inputs required before target review

Prepare ignored `site.json` according to the package schema. Obtain these
facts from the actual environment; do not infer them from September evidence:

| Input | Required evidence |
| --- | --- |
| Explicit kubeconfig context | Reviewed Kubernetes 1.35.x target, operator identity and scope |
| Eligible worker names and trusted SSH addresses | Node Ready, no DiskPressure, not control-plane/etcd, hostname label match; SSH host keys already trusted |
| Dedicated application-storage mount and per-worker UUID | Canonical `/var/lib/pcloud/storage/<name>` mount; filesystem distinct from OS and RKE2; protected root-owned marker; free bytes/inodes |
| Projected allocation and reserve | Per-worker free-byte floor and at least 20% bytes/inodes reserve; workload projection remains an input, not a quota |
| Hyper-V host budget | Measured free space, thin-disk growth allowance and observation within the preceding day; attach the measurement source |
| Resource request/limit review | Controller/helper budgets fit remaining measured lab capacity |
| Predecessor health | Current Layer 1 and kube-vip read-only checks; existing class/controller inventory and admission/RBAC access review |

The historical lab has no confirmed application mount. Its earlier 20 GiB
secondary disks and RKE2-on-OS arrangement do not satisfy the new Layer 1
mount policy. Disk preparation, growth and any RKE2 migration need a
separate measured plan identifying exact VM/disk/device/filesystem and data
preservation before owner authorization. This package supplies no such
host-change approval and must not manufacture it.

## Authorized sequence after inputs and revision are reviewed

1. Run the package's static entry and real render/schema mode.
2. Run read-only `preflight` with the actual ignored site input.
3. Render the actual site, publish its capability declaration, calculate
   the source/config/render digest and review the exact nine objects.
4. Obtain owner authorization for that target/revision/digest. Perform
   the separately reviewed diff, server-side dry run and apply using the
   explicit context; wait for availability, then run read-only `live`.
5. Run smoke only on a designated disposable installation with zero
   existing consumer PVCs/PVs of the class and a second healthy worker.
   Explicitly authorize controller stop/restore and synthetic data cleanup.
   A shared installation requires a separate test plan; never force the
   isolation gate to pass or borrow application data.
6. Recheck predecessor health and attach actual revision/digest/context,
   mount/capacity, generated helper, persistence/rebind/affinity/recovery
   and cleanup results as dated evidence. Keep any failure/incomplete
   disposition visible. Only then accept the lab milestone.

For supplied storage, installation is skipped and the provider's operator
supplies retention/rebind/cleanup evidence; the package does not assume
provider administration or host access.

## Subsequent lab work

The owner selected lab completion first. After the storage gate, continue
M3b secrets, M3c the supported observability backend, M4 OpenTelemetry/LGTM,
then independent M5 Kafka/APISIX packages. Select and document each lab
profile with the same standalone/static/live/recovery boundary. Crossplane
and production HA/DR remain separate future decisions. No downstream
package is claimed implemented merely because this plan names it.
