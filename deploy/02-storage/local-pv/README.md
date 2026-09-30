# Local persistent volumes (`deploy/02-storage/local-pv`)

Static local PersistentVolumes on a dedicated, guest-visible disk of a Kubernetes
node, for lab workloads that must keep data across pod restarts. It is
self-contained: its own guest-disk preparation (Ansible), Kubernetes objects,
configuration, tests and instructions. It reads nothing from Layer 1, Layer 2 or
any other package, and never touches the RKE2 data directory.

**Status: offline checks pass; both Hyper-V lab worker disks were prepared and
verified on 2026-09-30.** A temporary Kubernetes PVC and pod bound to worker-01,
kept data across pod replacement, refused to start while the disk was unmounted,
and recovered after remount. The test objects were removed; the StorageClass and
PVs are not installed now. This is single-host lab evidence, not HA or production
readiness. The committed inventory remains synthetic; the real inventory and
verification reports are git-ignored on the lab controller.

It provides node-pinned local storage. It does not provide replication, backup,
snapshots, quotas or high availability, and a lost node or disk loses its data.

| File | What it does | Changes a node? |
| --- | --- | --- |
| `inspect-disks.yml` | Reads the disks of a node and writes a report on the controller | No |
| `prepare-disks.yml` | Formats and mounts a disk that carries **no signature at all**, creates the volume directories | **Yes**, after typed authorization |
| `adopt-disks.yml` | Re-attaches a disk that already holds this package's filesystem; never formats | Mounts only, after typed approval |
| `check-storage.yml` | Verifies the data mount, filesystem, markers, volume directories and free space; fails closed | No |
| `render-pvs.yml` | Renders the PersistentVolume manifests for review, on the controller only | No node, no cluster |
| `kubernetes/` | The `pcloud-local` StorageClass (static, `WaitForFirstConsumer`, Retain) | Applied by a person |

## Test this package (offline)

Needs Bash and `ansible-core` (Linux, or WSL on Windows; Windows PowerShell cannot run
it). Run from any directory, including a copy of this folder elsewhere:

```bash
bash /path/to/local-pv/tests/verify-local-pv.sh            # decisions, refusals, hygiene
bash /path/to/local-pv/tests/verify-local-pv.sh --render   # also kubectl kustomize + kubeconform -strict
```

On WSL set `export ANSIBLE_CONFIG="$PWD/ansible.cfg"` when running playbooks by hand from a
Windows drive (Ansible ignores a config file in a world-writable folder). The test
contacts no node and no cluster, formats and mounts nothing, and writes only to a temporary
folder. It proves:

- 51 inventory and stamp rules, 80 disk-selection and authorization scenarios, and 21 mount
  verification scenarios, each fed to the same templates the playbooks run, from the raw
  text a node returns (negative cases included: root and RKE2 disks, mounted or partitioned
  disks, existing signatures, holders, swap, fstab references, read-only disks, size and
  identity mismatches, ambiguous matches, wrong or missing typed text, a label without the
  recorded UUID, an absent or wrong data mount, failed, unreported and truncated probes,
  partly inspected protected paths, missing tools, and stamps that are old, future-dated,
  failed, made for another configuration, or bypassed on a real inventory);
- the same guest identifier on two different nodes is accepted, on one node refused;
- every task that can change a node lives in one file, is reached only after the guards,
  and is skipped in check mode; the destructive commands appear nowhere else;
- the real playbooks refuse before any change: no `--limit`, several nodes, synthetic values,
  an unmatched identity, in normal and check mode, with `/etc/fstab` and the mount root
  unchanged;
- the real probe script, run read-only against the machine running the test, produces complete
  output, and when `wipefs`, `ls`, `grep`, `findmnt`, `find` or `dirname` is made to fail it reports
  that failure instead of an empty result (this needs a Linux machine; it changes nothing);
- rendered PVs are Retain, node-pinned, single-writer, claim-bound, marked
  capacity-unenforced, and named `*.SYNTHETIC-DO-NOT-APPLY.yaml` when built from synthetic
  values; the real `render-pvs.yml` accepts only a fresh, passing stamp made for the current
  configuration, and a real inventory cannot bypass that;
- a failed `kubectl` or `kubeconform` fails the render check (controlled fake tools).

The offline test alone does **not** prove real-node or Kubernetes behaviour. The
2026-09-30 authorized lab run below supplies real-node evidence for both workers
and a Kubernetes binding smoke test for worker-01. It does not prove portability
to another host or production durability.

## Verify the worker before touching it (owner steps)

Host-key verification stays on; `ansible.cfg` sets `host_key_checking = True` and the tests
fail if it is ever disabled. For each new environment, verify the workers' SSH host
keys before trusting them.

1. An authorized person opens the worker's console (Hyper-V Manager, `vmconnect`) and prints the
   fingerprints: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256` (and the ecdsa and
   rsa keys). Whether a console login exists on these key-only cloud images is not known; if it
   does not, obtaining a trusted path is an owner decision outside this package.
2. On the controller: `ssh-keyscan -t ed25519,ecdsa,rsa <address> > keys.tmp` then
   `ssh-keygen -lf keys.tmp -E sha256`, and compare with the console.
3. Only if they match exactly, append the keys to the controller's `known_hosts` (WSL and
   Windows keep separate files). Check with
   `ssh -o StrictHostKeyChecking=yes -o BatchMode=yes <user>@<address> true`.
4. Copy `inventory/hosts.example.yml` to `inventory/hosts.yml` (git-ignored), set the real
   addresses, then run the read-only inspection, one node at a time:
   `ansible-playbook -i inventory/hosts.yml inspect-disks.yml --limit <node>`.
5. Read the report (`out/reports/inspect-<node>.json`, or `-e local_pv_report_dir=...`) and copy
   the disk's identity and exact size into the inventory. Nothing has changed on the node yet.

## Disk identity

A disk is identified by facts read from the node, never by `/dev/sdb`, and identity is scoped
to the node: each node's guards see only its own devices, so two independent VM disks may carry
the same guest identifier, while one node may not declare it twice.

- Preferred: `identity.by_id` (a `/dev/disk/by-id/...` link), or `serial` / `wwn`, plus the exact
  `size_bytes`. Exactly one device on the node must match all declared attributes.
- Fallback: `identity.scsi_hctl` alone is refused. It needs `vendor`, `model` and an
  `identity_review` (who reviewed it, when, and at least two supporting facts), and those
  attributes must match the device too.
- Kernel names, a `device:` key, and unknown keys are rejected.

## Guards

Every guard runs before any change, again just before the change, and a doubt is a refusal.
**An unreadable source is never an empty (safe) one.** The node probe reports an explicit status
for each tool, each protected path, `fstab`, swap, the stable-link listing, and the holders and
signatures of each disk, and ends with an end marker; the parser treats a failed report, a
missing report and a cut-short probe as refusals (`ok`, `absent` and `fail|reason` are different
things). The playbooks refuse a disk that:

- does not match its identity exactly once, or has a different size;
- is not provably unprotected: **each** of `/`, `/boot`, `/boot/efi`, `/var/lib/rancher`,
  `/var/lib/kubelet` and `/var/lib/containerd` is inspected on its own. A path is fine if it maps
  to disks (which are then protected) or is genuinely absent (its parent is searchable and the
  path is not there); a failed lookup, a path that maps to no disk, a path that was not reported,
  or an unsearchable parent is a refusal. Finding the root disk is not enough;
- has partitions or child devices, has holders (LVM, md, device-mapper), is mounted, is swap, is
  referenced in `/etc/fstab`, or is read-only;
- carries any filesystem or signature (blank preparation only), or its signature probe failed;
- needs a tool the node lacks or did not report: `lsblk`, `findmnt`, `wipefs`, `blkid`, `stat`,
  `df` and the other read-only tools always, plus `mkfs.ext4`, `mount`, `chattr` and `install` for
  preparation and the last three for adoption (adoption never formats, so it does not need
  `mkfs.ext4`).

## Prepare, adopt, verify

- **Prepare** (`prepare-disks.yml --limit <node>`) is for a blank disk. `--check` prints the plan
  and the exact text to type and changes nothing. A normal run asks for
  `FORMAT <node> <disk> <device> <size_bytes>`; anything else is refused. **A rerun cannot
  reformat:** once prepared the disk has a signature and blank preparation refuses it, and a
  recorded filesystem UUID refuses it too. Without a terminal the prompt does not wait (Ansible
  warns and returns an empty answer), and an empty answer fails the authorization check, so a
  non-interactive run changes nothing.
- **Adopt** (`adopt-disks.yml --limit <node>`) re-attaches a package disk, for example a
  preserved data disk on a replacement VM with the same node name. It needs the recorded
  filesystem UUID, an ext4 filesystem with that UUID and label `lpv-<disk>`, exactly one
  signature, and the typed text `ADOPT <node> <disk> <device> <uuid>`. A label alone is refused.
  It never formats and creates only missing volume directories.
- **Verify** (`check-storage.yml`) is read-only and fails closed: absent or wrong data mount,
  filesystem UUID that is not the recorded one, wrong device, non-ext4, read-only mount, mount
  on the root filesystem, missing markers or volume directories, wrong owner or mode, or a
  filesystem at the critical level (default 85%, warning 70%). "Not mounted" is believed only when
  `findmnt` demonstrably works (it finds `/`) and `/proc/self/mounts` agrees; a probe error, a
  disagreement or a cut-short probe is a failure. It writes a verification stamp on the controller.
- **Verification stamps** bind a passing check to the exact configuration it covered: a SHA-256
  digest of the node, the disk's identity, size and recorded UUID, the mount path, and each
  volume's name, subdirectory, owner, group and mode (capacity and claim names are not verified on
  the node, so they are not covered). `render-pvs.yml` accepts a stamp only if it passed, names the
  recorded UUID, carries the digest of the *current* configuration, is not dated more than five
  minutes in the future, and is at most `local_pv_stamp_max_age_hours` old (default 24). Any
  covered change, or age beyond the limit, needs `check-storage.yml` again. The stamps can be
  skipped only for an inventory that declares `local_pv_synthetic: true` and has nothing but
  documentation addresses (the example); asking to skip them (`LOCAL_PV_REQUIRE_STAMPS=false`) for
  any other inventory is itself an error.
- After preparation, record the reported `filesystem_uuid` in the inventory, run `check-storage.yml`,
  and only then `render-pvs.yml`. Applying the reviewed files with `kubectl` is a separate,
  authorized step; applying the StorageClass is too.

Formatting is a separately authorized act naming the verified disks. No preparation, formatting,
mounting or apply is authorized by this repository's tests or documentation.

## Capacities are not quotas

A PV's `capacity` on a shared directory only decides which claims can bind. It is **not**
enforced: any consumer can fill the whole filesystem and affect every volume on that disk.
Rendered PVs say so in an annotation. The inventory validator keeps the *declared* ceilings
honest with estimates (`inventory/group_vars/all/main.yml`): about 3% ext4 overhead, 1% reserved
blocks (`mkfs.ext4 -m 1`), and 20% of the usable space kept unallocated. For a 20 GiB disk that is
about 19.2 GiB usable and a declared budget of at most 15 GiB. The overhead figure is an
estimate, not a measurement. Real isolation needs a filesystem per volume (LVM or partitions),
project quotas, or a disk per volume; none is implemented. Loki, for one, does not delete data
based on disk usage, so free space needs monitoring: `check-storage.yml` warns at 70% and fails at
85%, and the consuming stack should alert on the same filesystems.

## Fail closed on the data mount

If a data disk is not mounted at its path, nothing may write where it should be. Layers, and
what is actually established:

| Layer | Established? |
| --- | --- |
| Each PV path is a **subdirectory** created after mounting, so an absent mount leaves no such path on the root filesystem | Verified on worker-01: kubelet reported `FailedMount` because the PV path did not exist; it did not create that path on the OS disk |
| The empty mountpoint is made immutable (`chattr +i`) before mounting, so nothing can be created there while unmounted | The immutable attribute and absent volume subdirectory were observed while worker-01 was unmounted; protection on other hosts is unverified |
| Marker files: `.pcloud-local-pv` on the filesystem and `.pcloud-volume` in each volume name the disk, UUID and node. A consumer should refuse to start unless its marker is present (an init container or probe) | Files were verified on both workers; the temporary worker-01 pod checked marker presence. A durable Layer 3 consumer-side check remains required |
| `check-storage.yml` and `render-pvs.yml`: an absent or wrong mount, or a UUID that is not the recorded one, fails verification, and rendering needs a passing stamp | Established offline and on worker-01 with the mount deliberately absent; both workers passed after remount/reboot |
| `nofail` in `/etc/fstab` keeps the node booting when the disk is missing | This is **availability, not protection**: a missing disk does not stop boot, so verification and monitoring must catch it |

The worker-01 smoke test establishes these behaviours in this lab only. Repeat the test for other platforms before relying on it there.

## Node loss and recovery

The data lives on one node's disk. A pod restart or node reboot keeps it. If the node is down,
its pods stay Pending (node affinity forbids other nodes) and the data is intact when it returns.
If the VM or disk is lost, the data is lost: prepare the new disk (authorized), remove the
Released PVs and PVCs, and redeploy. A preserved data disk re-attached to a replacement VM with
the same node name is adopted, not formatted. A renamed node needs the PVs recreated (node
affinity is immutable); Retain keeps the data. There are no backups or snapshots. This package
never deletes data; reclaiming a Released PV is a manual, separately approved step.

## Handover to consumers

A consumer needs only the StorageClass name (`pcloud-local`), its PVC name and namespace (the PV's
`claimRef`), and a size at or below the PV's capacity. The pod runs as the volume's numeric
`owner_uid`/`owner_gid` (directories are created with that owner and mode, so nothing depends
on `fsGroup` behaviour, which is unverified on local volumes). Workloads must be pinned to the
node and use a single replica or `Recreate` strategy.

## Layout

```
ansible.cfg, .gitignore
inventory/hosts.example.yml         SYNTHETIC example (real inventory/hosts.yml is git-ignored)
inventory/group_vars/all/main.yml   defaults and capacity estimates
*.yml                               the playbooks in the table above
tasks/ templates/                   guards (templates/*.j2 are pure decisions: facts, selection, mount report,
                                    inventory rules, configuration digest, stamp), the single mutating task file
kubernetes/                         StorageClass
tests/                              verify-local-pv.sh, check-logic.yml, scenarios.yml
```

## Lab evidence (2026-09-30)

- Host-side Hyper-V SCSI attachment and each guest's `0:0:0:1` path identified the
  dedicated 20 GiB data VHDX; the protected OS/RKE2 disk was `sda`. Both worker
  inspections reported zero signatures and no blockers on `sdb`, and check mode
  changed nothing before each typed, one-node preparation.
- Both data disks were formatted as ext4, mounted by their recorded UUIDs,
  checked with `check-storage.yml`, rebooted one at a time, and checked again.
  `/var/lib/rancher` and `/var/lib/kubelet` stayed on `/dev/sda1`; all three
  Kubernetes nodes returned Ready. The UUIDs and passing stamps remain only in
  the git-ignored lab inventory and reports.
- The full two-worker inventory rendered four PVs from fresh stamps; the live
  Kubernetes API accepted all four with `kubectl apply --dry-run=server`.
- A temporary `pcloud-local` StorageClass, worker-01 PVs, PVC and restricted
  BusyBox pod proved `Bound`/`Running`, an on-disk test file across pod
  replacement, `FailedMount` with the data disk unmounted and no volume path
  on the OS disk, then pod recovery after remount. The namespace, pod, PVC,
  PVs, StorageClass and test file were removed. The final storage check passed.

## Limitations

- This is one single-host Hyper-V/RKE2 lab run. Worker-02's Kubernetes binding
  and missing-mount behaviour, other host types, upgrade/replacement and disk
  loss recovery have not been tested live.
- A transient SSH connectivity loss occurred during validation while Hyper-V
  still reported all three VMs running; it recovered without a VM restart.
  Its cause remains unknown, so this run does not establish network stability.
- A verification stamp proves only that a check passed within the freshness window for that
  configuration; it does not watch the mount afterwards. Monitoring must.
- The probe's tool and protected-path lists are fixed; a node layout they do not cover (for
  example RKE2 data under a path that is not listed) is not detected.
- No replication, backup, snapshot, quota, monitoring stack or HA.
