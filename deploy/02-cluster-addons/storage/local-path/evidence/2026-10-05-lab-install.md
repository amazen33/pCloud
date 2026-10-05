# M3a Stage 1 Hyper-V lab installation

Date: 2026-10-05 (Africa/Cairo).
Status: installed; read-only Live checks passed; isolated acceptance pending.

This is the Stage 1 record. The owner subsequently approved the isolated
smoke; its [separate dated evidence](2026-10-05-lab-smoke.md) records the final
persistence/recovery/helper/affinity/rebind and cleanup pass. The subsequently
authorized [serial worker reboot](2026-10-05-lab-reboot.md) passed mount/data
persistence on both workers and cleanup. The Stage 1 results below retain
their original scope and point-in-time limits.

## Authorization and revision

The owner explicitly approved "Stage 1 storage deployment" after reviewing
the two-worker mount proposal and nine rendered storage objects.
Installed source: `3a7eee6c673821a56331a7b324ef65f827db3e7d`.
Reviewed package/site/render digest:
`82f4f3d05e4b8d997358255842ed96a1ed23d2935fb944f5b227d44947ce59ed`.
The reviewed locked controller/helper image references and storage manifest were applied.
Read-only Live validation initially failed on Kubernetes API defaults; this
change declares those defaults explicitly in the render. The deployed API
configuration already has those values; no checker bypass or reapply was used.
The correction is recorded in this source change and its regression test.

Target: existing RKE2 `v1.35.7+rke2r1`, master `10.20.0.10`, workers
`rke2-worker-01` and `rke2-worker-02`. The operator used trusted WSL SSH and
the master's local RKE2 kubeconfig, context `default`, behind a fixed-target
wrapper. No kubeconfig, token, SSH key or secret content was copied into Git.

## Worker changes

The existing worker ext4 data mounts at `/var/lib/pcloud/local-pv/data-01`
were verified before mutation, distinct from each node's OS/RKE2 filesystem.
On each worker, a new root-owned `dynamic-app` subtree was bind-mounted at
`/var/lib/pcloud/storage/app`. Its protected marker has the M3a format
`pcloud-storage-v1:<filesystem UUID>`. Each boot entry requires the existing
source filesystem mount; systemd loaded that dependency. The old static-PV
directories retain their own ownership and lifecycle.

Expected/observed UUIDs:

| Worker | Filesystem UUID |
| --- | --- |
| rke2-worker-01 | `0886d0a9-7a19-401a-aa01-35393557388e` |
| rke2-worker-02 | `702ee148-5ca8-42e1-baca-b917a30a4759` |

Only the new directories/marker, bind mounts and exact new `/etc/fstab` entries
were added. Root-only pre-change fstab copies and results are retained under
`/root/pcloud-stage1-20261005` on each worker. No disk was formatted/resized;
no VM restarted; no existing static-PV or RKE2 data was migrated or removed.
Boot dependency configuration was verified; reboot persistence is untested.

## Capacity and installation

The owner supplied the Hyper-V inventory: each of the three VMs has 2 vCPUs,
4 GiB RAM, a 30 GiB differencing OS disk and a 20 GiB dynamic data disk.
The two worker data filesystems had about 19.3 GiB available and almost all
inodes free. A 14 GiB per-worker projection passes the 20% byte/inode reserve
and minimum 2 GiB free-byte floor. PVC sizes are not enforced write quotas.
E: had 132118740992 bytes free; the reviewed allowance is 40 GiB additional
growth with a minimum 20 GiB host reserve. These are point-in-time lab budgets.

The package preflight passed for both workers before apply. All nine owned
resources were installed: Namespace, ServiceAccount, Role, ClusterRole,
RoleBinding, ClusterRoleBinding, Deployment, StorageClass and ConfigMap.
The namespace passed server dry-run and was created first so subsequent
namespaced dry-run/diff operations could run. Full server dry-run and diff
then succeeded before applying the complete reviewed manifest.

Deployment `pcloud-storage-system/local-path-provisioner` rolled out successfully:
one available replica, controller Running/Ready, zero restarts at observation.
Observed controller image ID:
`docker.io/rancher/local-path-provisioner@sha256:e757967a5ec338f6a9b371c5a9688bedaa8c3578ea3dd4db329ea0084be0a86f`.

Installed class `pcloud-local-retain` has provisioner `pcloud.io/local-path`,
Retain, WaitForFirstConsumer and non-default status. No PVC or PV of that
class existed at the final observation. Provisioning/persistence and helper
execution are therefore not yet proven by a workload.

## Live failure and narrow correction

The original strict Live comparison rejected the API-stored Deployment because
Kubernetes added `env[].valueFrom.fieldRef.apiVersion: v1` and
`volumes[].configMap.defaultMode: 0644` to omitted fields. The render now declares
both values. Exact input, mount, security, ownership, configuration and RBAC
comparisons remain enabled. Regression coverage reproduces the original
failure, accepts the stored defaults and rejects altered API version,
namespace reference, file mode, mount write permission and extra environment.

Windows: 25 package tests passed, with real Kustomize and strict Kubernetes
1.35.0 schema validation. Linux: the same 25-test entry passed, including actual
helper shell guards, using pinned PyYAML 6.0.3/jsonschema 4.25.1 in a private WSL
virtual environment. The corrected package's real read-only Live check passed
against the installed objects and both mounted workers using those pinned
dependencies. Initial WSL execution used jsonschema 4.19.2; that result was
repeated with the reviewed version. Root layout passed with eight packages;
all 53 root rejection tests passed. Remote CI is recorded separately in the PR.

All three cluster nodes remained Ready without memory/disk pressure; kube-vip
controller and all three kube-vip agents were Running/Ready after installation.

## Evidence location, limits and next gate

The operator artifact folder for this chat is
`C:\Users\amaze\.codex\visualizations\2026\10\04\01a10762-9d31-7740-a175-7f046b0fdfce\pcloud-hyperv-lab`.
It retains mount results, source/digest checks, server dry-run/diff/apply,
rollout, API-default comparison, Live output and filtered installed identities.
The directory's date is its creation date; this installation occurred October 5.
Commit only reviewed summaries; operator artifacts are not another source repo.

Full M3a acceptance is incomplete. Next gate: authorize isolated synthetic
PVC/Pod persistence, delayed binding, affinity rejection, controller stop/restore,
rebind and retained test-volume disposition. The Stage 1 approval does not
authorize those smoke mutations or VM reboot tests. Preserve retained data.
OpenBao, M3c backend claims and observability remain undeployed. No production
capacity, replication, HA, backup/restore or disaster recovery claim is made.
