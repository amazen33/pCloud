# Layer 2: kube-vip LoadBalancer add-on

This directory is a self-contained, **lab-specific** Layer 2 package. Copy
the whole directory into another checkout to render and test it; Kustomize
reads only local files. It consumes a working Kubernetes API and does not
read Layer 0 state, Layer 1 inventory, or IOT-EE application configuration.

The package installs the v0.0.12 kube-vip cloud controller, a v1.2.4
kube-vip DaemonSet in services-only ARP mode, RBAC, and a ConfigMap assigning
LoadBalancer Services addresses from `10.20.0.40-10.20.0.49`. The pool and
`eth0` interface are specific to the current Hyper-V lab; inspect them
before using this package elsewhere. It does **not** create a control-plane
VIP, install storage, secrets, Kafka, APISIX, observability, or any user
application. The cloud-controller manifest is vendored from the pinned
upstream release; its source and SHA-256 are in `kube-vip/UPSTREAM.md`.

## Test this package alone

From any working directory, on Windows or Linux, with Python 3.12+ and
PyYAML 6.0.3:

```bash
python /path/to/02-cluster-addons/tests/verify-layer2.py
# Also render with Kustomize and validate the render and tests/smoke.yaml with
# kubeconform -strict (Kubernetes 1.35.0 schemas). Needs kubectl and
# kubeconform v0.7.0 on PATH; kubeconform downloads the schemas.
python /path/to/02-cluster-addons/tests/verify-layer2.py --render
```

This checks that all Kustomize resources are local, the pinned upstream
manifest is byte-for-byte intact, resource identities are unique, the
images and services-only mode are reviewed, the lab address pool is valid,
and the smoke manifest meets restricted Pod Security. It also runs the
`--render` logic against fake `kubectl` and `kubeconform` in a temporary
folder, to prove that a failed render stops before validation and that a
failed validation fails. CI runs it with `--render`. No test contacts a cluster or applies resources. In a pCloud
checkout, [tests/README.md](../../tests/README.md) describes running all
packages by mode.

## Lab preflight and reviewed install

The current lab has three Ready RKE2 nodes and `eth0`. This package was
installed and smoke-tested there on 2026-09-28; see
`evidence/2026-09-28-hyperv-lab.md`. Before another apply, verify the nodes, API, pool,
and existing LoadBalancer controllers still match the review:

```bash
kubectl get nodes -o wide
kubectl get pods -A --field-selector=status.phase!=Running
kubectl -n kube-system get deploy kube-vip-cloud-provider --ignore-not-found
kubectl -n kube-system get ds kube-vip-ds --ignore-not-found
kubectl get svc -A -o wide
kubectl kustomize /path/to/02-cluster-addons > /tmp/layer2-reviewed.yaml
kubectl diff -f /tmp/layer2-reviewed.yaml
kubectl apply --dry-run=server -f /tmp/layer2-reviewed.yaml
```

Review the rendered file and diff, then apply it only through the separately
approved infrastructure gate. After applying, wait for the cloud-controller
Deployment and kube-vip DaemonSet to become available. Apply the included
`tests/smoke.yaml` in its own restricted-Pod-Security namespace; prove that
its LoadBalancer Service receives a pool address and answers `layer2-ok`
from the Hyper-V host. Record the address, response, component logs, then
remove the smoke manifest. The lab's Windows NAT does not make that address
reachable from the LAN without a separately reviewed NAT mapping and
firewall rule.

Rollback is a reviewed `kubectl delete -f` of the same rendered manifest,
after checking whether any Services still depend on the assigned addresses.
Removing the controller while such Services are in use causes traffic loss.

The cluster has one physical host and one control plane. The 2026-09-28
LoadBalancer smoke test shows that this lab package works; it does not show
production high availability or disaster recovery. Storage and secrets are separate
Layer 2 packages. The [storage package](storage/local-path/README.md) implements
the [accepted lab profile](../../docs/adr/0001-lab-persistent-storage.md), with
static checks and gated live acceptance. Its
[2026-10-05 isolated smoke](storage/local-path/evidence/2026-10-05-lab-smoke.md)
passed persistence/recovery/helper/affinity/rebind and cleanup; worker reboot
persistence remains untested.
The [secrets package](secrets/openbao/README.md) implements the OpenBao lab
profile and supplied API conformance boundary, with static validation and
pending deployment/acceptance. Storage and secrets each have their own
render/test boundary; applying this kube-vip package installs neither.
The [M3c filesystem backend](storage/observability-filesystem/README.md) has its
own claims, storage-only fragments and conformance boundary; it supplies no
S3 service and installs no LGTM servers. Its live acceptance is also pending.
