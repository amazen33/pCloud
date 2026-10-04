# ADR-0001 -- persistent filesystem storage for the pCloud lab

**Status:** Accepted by the project owner on 2026-10-04 in this conversation,
following review of the M3a proposal. Acceptance covers the lab storage
profile and its namespace security exception. A standalone package is now
implemented and statically validated; no lab installation/acceptance exists
and no installation or conformance result is claimed. Deployment requires
its own authorization naming the target cluster and reviewed revision.

**Decider:** Project owner. **Architectural review:** Codex.
**Implementation:** Codex under the owner's Claude engineering-role instruction,
through [M3a's work order](../work-orders/M3a-lab-storage.md).

## Context

The single-host Hyper-V lab has one RKE2 server and two workers in the
historical records. Only kube-vip has Layer 2 installation evidence. The
20 GiB secondary disks were unmounted; RKE2 was using the OS disks. The
standalone Layer 1 package's newer dedicated data mount has not been applied.
These are historical observations, not current node measurements.

The repository's example nodes have 2 vCPUs and 4 GiB RAM each. Storage
capacity and observability load have not been measured. M3a must expose
explicit filesystem persistence without claiming worker failover, host HA
or disaster recovery. The [contract](../CONTRACT.md) permits explicit lab
profiles with stated limitations and requires independent packages.

## Decision

Implement a standalone `node-local-lab` profile using Rancher Local Path
Provisioner **v0.0.37**, with a separate `supplied-storage` conformance mode.
This release is a candidate implementation pin, not a compatibility claim.
Review source, images and release advisories again when implementing;
record actual vendored-file hashes and image digests. Validate against
Kubernetes **1.35.0** schemas and the target RKE2 revision.

The installed profile has these boundaries:

| Concern | Accepted design contract |
| --- | --- |
| Package | `deploy/02-cluster-addons/storage/local-path/`, copied and tested independently |
| Controller namespace | `pcloud-storage-system`; dedicated to platform operators |
| Controller identity | `pcloud.io/local-path`, explicitly set using the supported provisioner-name option |
| Consumer StorageClass | `pcloud-local-retain`; never cluster-default |
| Scheduling | `WaitForFirstConsumer`, explicit eligible-worker node selection and generated PV node affinity |
| Volume | Filesystem, ReadWriteOnce; select the upstream `local` volume type explicitly |
| Reclaim | `Retain`; PVC deletion leaves operator-managed retained data |
| Capacity | PVC requests are declarations, not enforced filesystem quotas; require measured physical headroom and workload budgets |
| Expansion/snapshots | No advertised volume expansion, CSI snapshot, clone or replication capability |
| Backup | No backup provided by this profile; permanent node/disk/host loss may lose all data |
| Output | Versioned, non-secret storage capability description; consumers explicitly select class/profile |

Only explicitly listed worker nodes may provision. Non-listed nodes and the
control plane receive no storage path. Consumers must use the same eligible
node label or affinity; rejecting a path alone does not guide the scheduler.

The storage root must be an existing, operator-provided filesystem mounted
for application data, distinct from both the node OS filesystem and the
RKE2 data filesystem. The package never creates, formats or resizes disks
or moves existing RKE2 data. A missing mount cannot fall back to writing
application data into the OS directory beneath the mountpoint. Use a
backing-filesystem identity marker plus node mount checks; prove refusal
when that marker is absent or invalid before calling the installer safe.
Disk/mount preparation is a separate infrastructure work order.

This separation protects the Kubernetes engine from unbounded local-path
volume consumption. A lab capacity budget must name projected workload
usage and reserve at least 20% free space and inodes, plus an absolute free
space floor chosen for that workload. These are accepted guard policies,
not measured performance or sufficiency claims. Thin/dynamic Hyper-V disk
capacity also requires host-volume headroom; guest free space alone is
insufficient. Layer 3 must later supply ongoing capacity alerts.

## Security boundary

The controller should meet restricted Pod Security. Helper pods need host
filesystem access and therefore require a documented exception in their
dedicated namespace: `enforce=privileged`, `audit=restricted` and
`warn=restricted`, pinned to the target minor version. This namespace is
not available to application deployers. Do not modify Layer 1's global
Pod Security exemption list or enable an unsafe helper template.

Record the actual generated helper pod and effective admission result;
namespace labels and a rendered template alone do not prove the boundary.
Use the smallest reviewed RBAC, resource limits and API access. Operators
alone may change the ConfigMap, StorageClasses, node eligibility and helper
scripts. Keep upstream path-validation safeguards enabled. Require safe
volume-directory containment and restrict helpers to the selected storage
root. Ordinary consumer and smoke pods use restricted Pod Security.

## Options considered

| Option | Benefit | Cost / limitation | Disposition |
| --- | --- | --- | --- |
| Local Path Provisioner | Dynamic filesystem PVs with a small installation surface | Node affinity; no enforced volume capacity or replication; helper host-access exception | Selected for this lab |
| Longhorn | Replicated block volumes and a richer storage lifecycle | Extra node services/resources and security surface; replicas still share this host | Defer until failure-availability requirement and capacity justify it |
| Static local PVs | No dynamic provisioner controller | Manual PV inventory and lifecycle for every volume | Available alternative if the dynamic-helper exception is declined |
| Supplied storage | Reuse an existing compatible environment | Requires declared capabilities and independent conformance evidence | Supported through conformance mode; installs nothing |
| Rook/Ceph | Distributed storage and possible object-storage capabilities | Greater operational and resource scope than established lab requirements | Separate future decision |

Longhorn's published V1 minimum recommendation is three nodes, four vCPUs
and 4 GiB memory per node. The repository's example CPU allocation is below
that recommendation; no live capacity comparison has been made. Deferral
is an architectural choice, not a statement that Longhorn cannot run here.

## Consequences and recovery

Pod recreation on the same eligible node must preserve a written marker.
An unavailable node makes its volume unavailable; another worker does not
receive a copy. Retained data needs a documented PV inventory and operator
rebind/disposal procedure. Controller rollback preserves classes, PVs and
data and must not use deletion of the whole rendered package.

A change to another storage provider requires a separately tested data
migration. Renaming or changing a class does not migrate existing volumes.
Before uninstall, refuse if package-owned PVs remain, including Released
retained PVs; export the reviewed inventory and resolve ownership first.

M3a does not close the [Layer 3 backend requirement](../../deploy/03-observability/README.md).
Select and validate object storage or a supported backend alternative in
M3c. Secrets, Kafka, APISIX, Crossplane and application deployment are outside
this decision. Production storage requires its own HA, encryption,
capacity, backup and recovery objectives.

## Acceptance and open gates

The [work order](../work-orders/M3a-lab-storage.md) defines static, Live and
Smoke checks. Profile and security-exception acceptance is recorded above.
Measured filesystem/host capacity, authorized installation, smoke evidence and
reviewed rollback are outstanding. M3a remains incomplete until they pass.

## Primary sources checked on 2026-10-04

- [Pinned Local Path Provisioner documentation](https://github.com/rancher/local-path-provisioner/tree/v0.0.37): node-path configuration, capacity limitation, helper safeguards and local volume selection.
- [Pinned controller options](https://github.com/rancher/local-path-provisioner/blob/v0.0.37/main.go): configurable provisioner identity.
- [Path traversal advisory](https://github.com/rancher/local-path-provisioner/security/advisories/GHSA-jr3w-9vfr-c746): versions below v0.0.34 affected; preserve fixed validation behaviour.
- [Kubernetes StorageClasses](https://kubernetes.io/docs/concepts/storage/storage-classes/): binding and reclaim semantics.
- [Longhorn V1 hardware guidance](https://longhorn.io/docs/1.13.0/best-practices/#minimum-recommended-hardware): comparison reference, not a selected Longhorn version.
