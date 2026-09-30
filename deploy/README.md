# Deployment layers and current lab evidence

pCloud contains independently testable installation packages extracted from IOT-EE and a
Kustomize add-on tree. Each package reads its own settings and accepts a
documented handover from its predecessor; no package reads another layer's
working directory or Terraform state.

| Step | Directory | Owns | Independent check | Status |
| --- | --- | --- | --- | --- |
| Layer 0 | `00-infra/private-hyperv/` | Hyper-V host preparation, VMs, disks, network and inventory output | `powershell -NoProfile -ExecutionPolicy Bypass -File tests/verify.ps1`; optional `-RunTofu` | Package merged; its current revision has not been applied to the lab |
| Layer 1 | `01-k8s-engine/rke2-ansible/` | Ubuntu preparation, RKE2, Canal, dedicated RKE2 data mount and health | `bash tests/verify-layer1.sh` | Package merged; its new storage path has not been applied to the lab |
| Layer 2 | `02-cluster-addons/` | kube-vip LoadBalancer add-on | `python 02-cluster-addons/tests/verify-layer2.py`; `--render` adds `kubectl kustomize` and kubeconform (run in CI) | kube-vip package installed in the lab; `10.20.0.40` smoke test passed on 2026-09-28. A secrets package is not designed yet |
| Storage | `02-storage/local-pv/` | Static local PersistentVolumes on a dedicated guest disk per node: read-only disk inspection, blank-disk preparation and package-disk adoption (typed authorization), verification, PV rendering; the `pcloud-local` StorageClass | `bash 02-storage/local-pv/tests/verify-local-pv.sh` (`--render` adds `kubectl kustomize` and kubeconform; run in CI) | **Implemented and tested offline only; not applied to any node or cluster.** The lab workers' disks are unverified and every example is synthetic. No replication, backup, snapshot or quota |
| Layer 3 | `03-observability/` (planning [README](03-observability/README.md) only) | LGTM and OpenTelemetry Collector | None exists; synthetic logs, metrics, traces, service graph, access, retention and recovery tests required | **NOT IMPLEMENTED**; planned. No manifest or package; no pCloud Layer 3 installation has been verified |
| Platform services | Not implemented | Kafka and APISIX, each in its own replaceable package | Per-package install, health, security, persistence/routing and rollback tests required | Planned |

Every check by mode (Static, Live, Smoke), its prerequisites on Windows and
Linux/WSL, and the dispatcher that runs them are described in
[tests/README.md](../tests/README.md).

Layer 0 uses the pinned `windsorcli/hyperv` provider locally on the Windows
host. Copy its whole directory to use it in another project. Its
`scripts/deploy-layer0.ps1` produces git-ignored `out/ansible-inventory.ini`,
`out/nodes.json` and `out/network.json`; it does not write into Layer 1.
Layer 1 accepts an INI inventory from any compatible VM provider, with its
real `inventory/hosts.ini` and site overrides git-ignored. Follow each
package's README for prerequisites, review gates and installation commands.

## Lab status and limits

On 2026-09-27, the owner and read-only checks confirmed three running
Hyper-V VMs and RKE2 `v1.35.7+rke2r1`: one control-plane node and two
workers were Ready, API `/readyz` was healthy, and secrets encryption was
enabled. This is evidence of the **earlier lab installation**, not a live
test of the newly merged standalone packages. The running cluster uses Canal
and RKE2's bundled ingress-nginx. Its 20 GiB secondary disks are unmounted;
RKE2 data is on each OS disk. The new Layer 1 storage policy requires an
explicit disk of at least 40 GiB and has not been run on these nodes.

The lab is one physical Hyper-V host behind Windows NAT with one control
plane. It has neither host nor control-plane high availability. Layer 2
kube-vip is installed and smoke-tested. Storage classes, a secrets backend,
Kafka, APISIX, and the observability stack have no confirmed lab installation.
None of the current
checks establishes production capacity, HA, disaster recovery or 24/7
operation.

## Installation sequence and package boundaries

1. **Layer 0:** run its own offline test; review a saved OpenTofu plan and
   host capacity; install only after the infrastructure approval gate. Export
   its inventory as a handover file.
2. **Layer 1:** put the handover inventory in its ignored `inventory/hosts.ini`,
   set and verify each node's `rke2_data_device`, run its own offline test and
   inventory validation, verify SSH host keys at the VM console, then perform
   the separately approved Ansible run. Canal is the default. RKE2 does not
   support changing a running cluster's primary CNI in place.
3. **Layer 2 prerequisites:** install and test the chosen LoadBalancer,
   storage and secrets components as separate packages. `02-cluster-addons/` currently
   contains only kube-vip manifests; it does not install storage or a vault.
   `02-storage/local-pv/` provides local persistent volumes (see its README); using
   it needs the owner steps to verify each worker's SSH host key and disk, and
   separately authorized formatting and apply steps, none of which has happened.
4. **Layer 3 observability (not implemented):** install the Collector and
   LGTM stack before application instrumentation. Its future package must
   prove synthetic ingest/query, a service-graph edge, access controls,
   retention, restart, failure isolation and rollback without IOT-EE
   application code; see [03-observability/README.md](03-observability/README.md).
5. **Platform services:** install Kafka and APISIX in separate version-pinned
   packages. Kafka must prove durable produce/consume, broker recovery and
   authorization. APISIX must prove HTTPS routing, JWT enforcement, rate
   limiting, WebSocket/gRPC handling and rollback. Instrument both against
   the installed observability stack. Do not mark either installed based on
   a catalog entry or an ADR alone.
6. **Applications:** release services only after the required platform
   dependencies have passed their own readiness gates.

HAProxy is an optional external load-balancer package for a topology that
needs it, such as a future multi-server control plane. It is not part of the
single-host lab. The current lab runs RKE2's bundled ingress-nginx, but
fresh Layer 1 installations disable it so ingress can be supplied by a
separately installed, replaceable gateway package. If APISIX and NGINX
coexist, document their distinct traffic roles and test the handoff.
Removing ingress-nginx from the running lab requires a separately reviewed
migration; the Layer 1 guard prevents a routine rerun from doing it.

CI runs static and offline checks only; it never applies infrastructure.
The Layer 0 `-RunTofu` option additionally runs OpenTofu in a temporary copy.
On the owner's Windows session, Application Control blocked `tofu.exe`, while
the 33 default PowerShell checks passed and GitHub CI validated OpenTofu.
That local restriction is not evidence of a failed or successful live apply.

## Phase dependencies and deployment profiles

Each phase consumes documented outputs of the phase before it, never its
working directory or state. A phase may be reused or skipped when the
environment already supplies its capabilities, provided it passes the same
capability tests. "Planned" and "not designed" mean no implementation
exists.

| Phase | Needs from earlier phases | Provides | Status |
| --- | --- | --- | --- |
| Layer 0 | A Windows Hyper-V host | VMs, network, inventory handover | Package merged |
| Layer 1 | Ubuntu VMs and an inventory, from Layer 0 or any compatible provider | Kubernetes API and kubeconfig | Package merged |
| Layer 2 | A working Kubernetes API | LoadBalancer addresses (kube-vip); a secrets package is not designed | kube-vip installed in the lab |
| Storage | A working Kubernetes API, nodes with a verified, blank, dedicated disk, and verified SSH host keys | Node-pinned local volumes (`pcloud-local`); no replication, backup or quota | Implemented offline only; not applied; worker disks unverified |
| Layer 3 | Installed profiles: a healthy cluster, exposure of services, and the storage and secret-management capabilities their design requires. External profile: supplied endpoints and credentials only | The observability capability contract (planned): ingest, query, dashboards, alerts | **NOT IMPLEMENTED**; installed profiles also need the storage phase applied (implemented offline only) and a secrets package (not designed) |
| Platform services | Layers 0-3 or their equivalents | Kafka and APISIX packages | Planned |
| Applications (IOT-EE) | The platform capabilities they consume, through versioned endpoints and configuration | IoT services with their own instrumentation and dashboards | Owned and released by IOT-EE |

Layer 3 profiles (none is implemented; details in
[03-observability/README.md](03-observability/README.md)):

| Profile | Phases pCloud installs | Layer 3 prerequisites | Notes |
| --- | --- | --- | --- |
| Lab | Layers 0-3 on the single-host lab | Cluster, service exposure, storage and secret management suited to the lab design | Proves function only; no HA, DR or capacity claim |
| Existing cluster | Layer 3 on a compatible cluster installed elsewhere | The same, supplied by that cluster | Layers 0-2 skipped when the cluster already supplies their capabilities |
| External observability | None for Layer 3 | Supplied endpoints, credentials and a designated test scope; none of pCloud's backend dependencies | The environment supplies the stack; pCloud verifies it with the live acceptance checks |
| Production | To be designed | To be designed | Needs separate HA, DR, capacity and 24x7 evidence |

The ownership boundary with IOT-EE already exists in
[the working contract](../docs/CONTRACT.md) (section C): pCloud owns
platform installation, operation and the capability contract; IOT-EE owns
instrumentation, domain dashboards and alerts. Neither reads the other's
repository files, inventory or state.
[The proposed ownership ADR](../docs/adr/XXXX-proposed-pcloud-iot-ee-ownership-and-observability-boundary.md)
records how that applies to observability. The Layer 3 installation and the
published capability contract remain planned.
