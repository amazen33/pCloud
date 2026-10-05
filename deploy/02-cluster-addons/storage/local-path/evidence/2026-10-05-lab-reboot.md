# M3a serial worker reboot acceptance

Date: 2026-10-05 (Africa/Cairo).
Status: both worker reboot/mount/data checks and synthetic cleanup PASS.
Together with the separate installation and isolated smoke records, this
completes the required `node-local-lab` M3a storage gates for this lab.

## Authorization, revision and target

The owner explicitly approved merging PR #11 and the concrete serial reboot
plan: worker-01 first, then worker-02, with temporary cordon/restore, two new
16 MiB synthetic volumes, persistence checks and identity-checked cleanup.
PR #11 merged with all 12 CI checks passing at reviewed head
`7a9b2db222074c42b555bebaf1f7299698e1e6cd`. Merge commit
`dc99e7c028c36033965b0cc58388dd22c6c86534` has the identical reviewed tree
and the verified prior-main/head parents. The run used that clean merged
revision and package/site/render digest
`2c21420bffc41e8f3a2dc19e802beacaa29279771c405ffd8b449be7dc114c41`.

Target: existing Hyper-V RKE2 `v1.35.7+rke2r1`, master `10.20.0.10`,
explicit context `default`; worker-01 `10.20.0.21`, worker-02 `10.20.0.22`.
Trusted WSL SSH used the master-held kubeconfig; no credentials were copied.
The external operator's source/configuration digest and clean Windows Git
attestation were checked before execution. The pinned Linux package ran its
unchanged strict installed preflight, filesystem and helper guards.
No installation reapply or checker bypass occurred.

## Actual execution

Run `186c50ca7f1b`, namespace `pcloud-storage-reboot-186c50ca7f1b`,
started at 07:05:38 UTC and completed at 07:13:51 UTC. Initially all three
nodes were healthy and schedulable, the class had zero existing PVCs/PVs,
the original storage controller was available and all four active kube-vip
Pods were Ready. Worker workloads were reviewed against the recorded
platform identities; unknown workloads would stop the run.

Each claim stayed Pending before its restricted, non-root consumer. Explicit
node affinity selected its respective worker; the controller provisioned a
new local PV there. Each consumer wrote a distinct random marker and synced
it to disk. This supplies positive provisioning evidence on **both workers**.

| Worker | Automatic mount and identity | Data recovery | Reboot-to-check completion |
| --- | --- | --- | --- |
| `rke2-worker-01` | PASS; boot ID changed, same node/PV/filesystem identities | PASS; identical SHA-256 through a replacement consumer on worker-01 | 163.16 seconds |
| `rke2-worker-02` | PASS; boot ID changed, same node/PV/filesystem identities | PASS; identical SHA-256 through a replacement consumer on worker-02 | 155.15 seconds |

The durations include health polling and consumer replacement; they are
measured test recovery intervals, not a promised availability SLO.
Before the next reboot, the runner required the first worker, API, original
controller Deployment and kube-vip to be healthy again. Both original test
Pods were reported Running after boot; the runner then replaced each owned
consumer to prove a fresh mount of the retained data. Neither worker was
rebooted more than once; the master was not rebooted.

Both guests automatically restored `/var/lib/pcloud/storage/app`, backed by
the existing dedicated data disk, distinct from OS/RKE2 filesystems:

- worker-01 UUID `0886d0a9-7a19-401a-aa01-35393557388e`.
- worker-02 UUID `702ee148-5ca8-42e1-baca-b917a30a4759`.

Each protected root-owned readiness marker matched its UUID. The mount unit
was active and its `RequiresMountsFor` included the backing data mount.
Before/after probes confirmed the reviewed persistent bind entry:

```fstab
/var/lib/pcloud/local-pv/data-01/dynamic-app /var/lib/pcloud/storage/app none bind,x-systemd.requires-mounts-for=/var/lib/pcloud/local-pv/data-01 0 0
```

This run did not edit fstab, create/format/resize disks or migrate RKE2 data.

## Volumes, helper evidence and cleanup

Only these two synthetic PVs were used; both were disposed:

| PV | UID | Original marker SHA-256 |
| --- | --- | --- |
| `pvc-af5853af-7dfe-44e0-a8e3-a5cad1baf641` | `b17ea1bc-2cd9-48ec-9b6e-63e66e2057dd` | `92e8e1a36cacc0a5aaf5f275c547d70b5b0c4372388697cfbcc88cd7bd6dc77e` |
| `pvc-10ef25b6-9b30-4db1-baf8-4f8120554a9b` | `96448ef8-7c09-4ac4-a3f2-59fed4e91f0b` | `697395c1190818c610043bb36d4884c0fd995efe5e6dbe5058ca98064f4f053e` |

UID/claim/node/path guards preceded cleanup. After deleting only owned test
Pods/PVCs and verifying Released PVs, the runner changed only these PVs
from Retain to Delete. The provider removed their exact data directories.
Read-only probes on both workers confirmed no synthetic paths remained.
The metadata-only namespace inventory excluded unexpected resources before
the owned namespace was removed. No direct host-directory deletion occurred.

The actual API watch captured four generated helpers: setup and teardown on
each worker. Their image, bounded resources, disabled token automount and
exact `/var/lib/pcloud/storage/app` host path passed the unchanged guards.
Helpers retained empty effective security contexts and the accepted
`privileged` namespace exception; they are **not restricted-compliant**.
The test consumer namespace enforced restricted Pod Security.

The runner exited zero with `status: PASS`, `cleanup: PASS`, no remaining
cordons, strict post-reboot/final Live PASS and healthy API/3 nodes/1 storage
replica/4 active kube-vip Pods. An independent read-only inventory at
07:15:48 UTC reconfirmed these health/identity checks, both workers schedulable,
zero class PVCs/PVs, absent test namespace and absent test directories.
The original controller Deployment UID remained
`7c688b0c-28f1-4309-ba16-4173adeb2eab`.

## Evidence handover and limits

Operator artifacts remain outside source at
`C:\Users\amaze\.codex\visualizations\2026\10\04\01a10762-9d31-7740-a175-7f046b0fdfce\pcloud-hyperv-lab`.

| Artifact | SHA-256 |
| --- | --- |
| `worker-reboot-result.json` | `4563c636ef990580a90762ffd4551876628177a1da586017f150b367cbb255af` |
| `worker-reboot-final-inventory.json` | `2fa9cd6d859d7dfeb8958974a482b69f4085fcb7dcdb6df901d4878189a90549` |
| `worker-reboot-runner.py` | `2bd4052c303d17d21d92ebc7757a0c059dd4ea6eeec2e74ac92cb677b87751c2` |
| `storage-lab-acceptance-20261005.json` | `cc3612ddaf5f5aa3f34a3c5ed93d3ae97d2db6b256c8cc7642d2f61481caa0ae` |

`storage-lab-acceptance-20261005.json` ties the reboot review/operator/result,
process exit and independent inventory to the earlier passing smoke and
explicit `storage-capability.json` handover using SHA-256 references.
The earlier reports retain their historical reboot-pending state; this
subsequent record establishes boot persistence on both workers.

Local checks before the evidence PR push: root layout PASS (eight packages),
53 root regression tests PASS, 29 storage tests PASS on Windows and pinned
Linux (actual shell guards executed), storage real Kustomize/strict Kubernetes
1.35.0 schemas PASS, and unchanged parent kube-vip offline/failure/render
checks PASS (9 installed and 3 smoke resources valid). Initial sandbox render
attempts failed on tool/filesystem access; the unchanged checks passed with
the required access. These static checks are separate from the live results
above. CI remains a separate check on the pushed evidence revision.

M3a's required lab storage gates have passed. OpenBao/M3b, M3c backend claims
and LGTM/M4 remain undeployed. Their live capacity, TLS, key-custody and
acceptance gates remain separate. These results do not accept the current
M1/M2 packages or their unapplied 40 GiB RKE2 data-mount policy.
Node-local data remains unavailable during its worker outage and may be lost
with permanent disk/node loss. There is no replication, enforced PVC quota,
backup/restore, host HA, DR or production acceptance.
