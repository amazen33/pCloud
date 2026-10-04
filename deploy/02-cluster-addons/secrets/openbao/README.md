# M3b: lab secrets

Standalone installation and lifecycle for **OpenBao 2.7.1**, pinned by OCI
manifest digest. The pinned container recipe, MPL-2.0 licence and API source
contracts are under `upstream/`; hashes and exact sources are in
`UPSTREAM.json`. This package needs no sibling package, Terraform state or
IOT-EE files. Python 3.12+, the pinned `requirements.txt`, kubectl and an
explicit context are its tools. Implementation and local validation are
available; **no installation, live acceptance or recovery is claimed**.

## Profiles and boundaries

| Profile | Installs / consumes | Limits |
| --- | --- | --- |
| `openbao-raft-lab` | One TLS OpenBao server with integrated Raft, worker-local RWO data and separate audit PVCs; Kubernetes JWT authentication and KV v2 | Manual initialize/unseal; one failure domain; no HA, auto-unseal, CSI injection, automatic migration, public ingress or application secrets |
| `supplied-vault-api` | Existing trusted HTTPS endpoint, KV v2 mount and Kubernetes auth mount | Renders nothing, cannot bootstrap, export or restore the provider; isolated conformance needs delegated test-admin rights; provider audit, restart and recovery evidence remains required |

`purpose=recovery` is supported only for the installed profile in a namespace
named `pcloud-secrets-test-*`. It is an isolated restore destination. No
production profile is implemented. The supplied profile is an API boundary,
not a claim that every Vault/OpenBao feature is interchangeable.

pCloud owns the server, platform mounts, reviewer RBAC, platform recovery and
audit operations. Consumers own application paths, policies, roles, secret
rotation and configuration. Nothing here installs an IOT-EE role or secret.
`capability.schema.json` defines `pcloud.secrets/v1`: endpoint, mount names,
JWT delivery and stated limits. Exporting it is a configuration handover;
live conformance is a separate gate. Crossplane is not required.

## Inputs and prerequisites

Copy the applicable example to ignored `site.json`; validate every field
against `site.schema.json`. Replace documentation addresses and names.
`context`, `namespace`, `purpose`, `endpoint`, `ca_file`, `kv_mount` and
`auth_mount` are mandatory. The endpoint is an HTTPS origin without credentials,
query or path. Supply a trusted CA file on the operator station; private keys,
initialization output, root tokens and unseal shares never belong here.
The supplied profile's client namespace must already exist in its selected cluster.

Installed profile also requires:

- Accepted Kubernetes 1.35 cluster, healthy workers with matching hostname
  labels, enforced Pod Security and NetworkPolicy support. Disable OS swap
  under the cluster policy: Raft runs with `disable_mlock=true` and no IPC_LOCK.
- An independently accepted Retain / WaitForFirstConsumer filesystem storage
  class and provisioner. Data requests 8Gi and audit 2Gi in the example;
  requests reserve scheduling intent, **not a write quota**. Measure host,
  node, filesystem byte/inode and memory reserves with all existing workloads.
  The default 256Mi request / 512Mi limit is a starting lab budget, not a
  demonstrated capacity requirement. Stop if the measured lab cannot fit it.
- A precreated, package-labelled namespace with restricted PSA v1.35 labels,
  and an operator-owned `kubernetes.io/tls` Secret named by `tls_secret`.
  Namespace creation and TLS material installation require the deployment gate.
  The certificate SANs must cover the operator endpoint and the server service
  names `openbao.<namespace>.svc` and
  `openbao-0.openbao-internal.<namespace>.svc`. Cert/key are mounted read-only;
  UID/GID/fsGroup 1000 is used, without root init containers or image entrypoint
  scripts. The executable is explicitly `/usr/bin/bao server`, never dev mode.
- Narrow operator CIDRs and Kubernetes API CIDRs, including the service IP and
  real API endpoints needed by the selected CNI's DNAT semantics. API egress
  permits TCP 443 and `api_port` only to these CIDRs; DNS goes to kube-system
  pods labelled `k8s-app=kube-dns`. Check the actual DNS labels / NetworkPolicy
  behaviour. Client namespaces opt in with `pcloud.io/secrets-access=true`.
  No LoadBalancer, Ingress or port forwarding is created by this package.

The server SA can **create TokenReviews only**, with no Secret read/list,
namespace selector access or cluster administration. Its projected reviewer
token lasts 3600 seconds and is reread by the plugin. No long-lived SA-token
Secret is generated. Consumer roles must bind exact SA names, namespaces and
the `pcloud-secrets` audience, with short token TTLs. Protect namespace labels
and SA creation permissions: namespace labels are network reachability,
not authentication or authorization.

## Standalone validation

From the package directory (or use absolute paths from any directory):

```bash
python -m pip install -r requirements.txt
python tests/verify-secrets.py
python tests/verify-secrets.py --render
```

Offline checks use schemas, recorded upstream hashes, security/ownership
guards, copied-package execution, simulated API/cluster lifecycle and an
ephemeral localhost TLS server. OpenSSL is required for generating its
temporary certificate; Windows uses Git for Windows' bundled OpenSSL.
`--render` requires kubectl **1.35.0** and kubeconform **0.7.0** on PATH. It
runs real Kustomize and strict Kubernetes **1.35.0** schemas for both installed
purposes, with public schema downloads. CI invokes this exact entry point.
These tests do not run an OpenBao binary, contact a cluster or prove installation.

## Installation and initialization

Select an actual environment and reviewed source revision under the repository
deployment gate. Review the licence, measured capacity, TLS custody, operator
access, rendered manifests, server-side dry run and diff before authorized apply.
Prepare the namespace / TLS Secret first. Then:

```bash
python bao.py render --site site.json > /operator/review/openbao.yaml
python bao.py capability --site site.json
python bao.py preflight --site site.json
kubectl --context ACTUAL-CONTEXT apply --dry-run=server -f /operator/review/openbao.yaml
kubectl --context ACTUAL-CONTEXT diff -f /operator/review/openbao.yaml
# Apply the reviewed file only through the separately authorized deployment gate.
```

`preflight` is read-only: version, class, workers, TLS Secret type/key names
and existing-resource ownership. It never prints TLS data. An uninitialized
or sealed server is deliberately **not ready**. TCP startup/liveness probes
avoid restarting a server merely because it needs unseal. Kubelet HTTPS
readiness does not validate certificates; the client validates CA and hostname.

Use the pinned official `bao` CLI from a secure operator station with
`BAO_ADDR` and `BAO_CACERT`. Perform `bao operator init` and
`bao operator unseal` interactively, with separate operator/key custodians
and an owner-agreed threshold. Never save initialization output to this
repository, logs or chat; never put shares/tokens in command arguments.
Arrange a controlled private management route that works while readiness is
false; the headless service publishes not-ready addresses for that purpose.
The package never initializes, captures keys or auto-unseals.

For API operations supply a short-lived operator token via environment
`PCLOUD_BAO_TOKEN`, using a secure prompt/credential manager; unset it after
use. Live checks need delegated read access to mounts/auth/audit plus cluster
resource reads. Bootstrap needs the specific sys mount/auth/audit operations
and mount configuration writes. Use the initialization token only for initial
setup, establish operational policies, then revoke it. No token is installed
as a Kubernetes Secret by this package.

Create ignored `review.json` with **exactly**:

```json
{"revision":"REPLACE-WITH-40-HEX-SOURCE-COMMIT","artifact_digest":"REPLACE-WITH-DIGEST"}
```

`python bao.py digest --site site.json` binds runtime source, schemas, locks,
pinned contracts, configuration, render and operator CA bytes. Review the
actual patch separately if the worktree is dirty; a commit identifier alone
does not attest staged changes. Changing inputs/source/CA invalidates the digest.
After operator initialization/unseal and authorized review:

```bash
python bao.py bootstrap --site site.json --review review.json --allow-cluster-changes
python bao.py live --site site.json --output /operator/evidence/secrets-live.json
```

Bootstrap enables persistent JSON file audit with `log_raw=false` **before**
platform mounts, KV v2 with required CAS and ten retained versions, and
Kubernetes authentication using local projected reviewer files. It refuses
foreign mounts and conflicting audit devices. Partial failures leave these
persistent controls intact for inspected/idempotent retry; no rollback disables
audit. Bootstrap installs no consumer roles. Supplied providers configure and
prove these capabilities themselves.

## Conformance and persistence acceptance

```bash
python bao.py smoke --site site.json --review review.json --allow-cluster-changes \
  --output /operator/evidence/secrets-smoke.json
```

This mutation requires delegated test-admin rights to create/delete an isolated
KV mount, ACL policy and role on the selected auth mount, plus UID-scoped
namespaces/SAs and short JWT TokenRequests. It verifies:

- Exact namespace/SA/audience binding; wrong values are denied.
- KV v2 read, CAS rotation/version, stale CAS failure, denied writes/cross-path
  reads, short token TTL and explicit token revocation denial.
- Installed profile: persistent audit entries for the unique test mount,
  HMAC client tokens and no raw synthetic secret value. Raw audit data stays
  in memory and is never included in reports.
- Cleanup revokes test tokens, checks role/policy/mount ownership before
  deletion, uses Kubernetes UID preconditions and lists every namespaced API
  type before deleting a test namespace. Foreign/replaced resources are
  preserved and produce FAIL. Finalizers/permission failures also fail cleanup.

SA deletion is not a substitute for explicit revocation of an issued token.
Consumers must enforce TTLs and revoke compromised tokens. Renewal after SA
revocation and real provider behaviours still need deployment-specific tests.

Even a successful automated conformance run reports **INCOMPLETE / exit 3**
until these operator-controlled acceptance steps are recorded:

1. During an approved maintenance window on the initial lab, create a unique
   synthetic secret in a separately owned test mount. Record its version and
   value in private operator evidence, never in this repository. Confirm no
   business consumers will be interrupted.
2. Separately authorize deletion of the exact server Pod UID, preserving both
   PVCs. Observe the replacement remain sealed/not-ready. Keyholders manually
   unseal it using the controlled management route; verify CA/hostname again,
   then read the same secret/version and confirm audit continuity.
3. Perform the isolated snapshot recovery below and record known-secret read,
   access denial and audit restoration. Dispose only the reviewed synthetic
   data. Supplied providers supply their equivalent restart/audit/recovery proof.

No server restart is hidden in the dispatcher or conformance command.

## Backup and isolated recovery

Snapshots are sensitive encrypted store copies; their keys, policies and
metadata remain sensitive. Loss of snapshot seal keys prevents recovery.
Export/restore uses a **Linux operator station**: snapshot directories mode
0700, new exports mode 0600, no symlinks, no repository path, no overwrite,
bounded 128MiB. Windows callers use an explicitly configured Linux station
or WSL rather than relying on POSIX permission emulation. Keep the snapshot,
unseal custody and integrity evidence in independently secured off-host storage.
Export alone does not prove an RPO, RTO or DR capability.

```bash
python bao.py backup --site site.json --review review.json --allow-sensitive-export \
  --snapshot /operator/private-backups/lab.snap
```

Backup needs Raft snapshot read permission; it performs no cluster mutation.
The report contains size/hash only. No upload or destination overwrite occurs.
For restore, prepare a **new isolated** purpose=recovery installation with
its own namespace, TLS and new retained PVCs. Initialize/unseal that empty
destination interactively. Do not bootstrap it or add engines/identities/roles.
Its only token must be its initialization token, supplied via operator environment.
Use destination `site.json`, not the source site. Review includes one extra key
`snapshot_sha256` matching the actual source bytes, with a new destination digest.

```bash
python bao.py restore --site site.json --review review.json --allow-cluster-changes \
  --allow-store-restore --snapshot /operator/private-backups/lab.snap
```

The restore command rejects a platform namespace, extra engines/auth/policies,
identities/token roles, audit configuration, extra tokens, multi-node Raft,
unsafe file permissions or a mismatched snapshot hash. Only then does it submit
the official force-restore API to the disposable destination. It reports
**INCOMPLETE**, requiring the original snapshot keyholders to unseal, known
synthetic data/version reads, scoped denial and restored audit-path validation.
The source audit device refers to the same filesystem path; the destination's
separate audit PVC starts without historical audit files. Preserve/copy audit
history through independently secured evidence storage; Raft snapshots do not
back up the audit PVC. If restore fails, preserve source/snapshot; investigate
and recreate only the explicitly disposable target under a new review.

## Upgrade, rollback and removal

Pin a candidate release/image and source contracts in a separate reviewed
change; run package tests/render/schema checks. Review official release/storage
compatibility notes and validate a fresh snapshot restore with the original
keys before upgrading. Measure resource/audit growth; plan downtime and manual
unseal. One Raft instance is unavailable during restart/worker failure.

Do not assume binary downgrade can read upgraded Raft state. Failed upgrades
require an isolated known-good-version restore and reviewed cutover; do not
overwrite the live store or delete PVCs as rollback. TLS rotation is a reviewed
Secret update plus explicit restart/reunseal and trusted-client check.

`python bao.py uninstall-check --site site.json` is read-only and rejects any
namespace PVC or retained PV reference, including released volumes. Even when
none exist it reports INCOMPLETE until operator data-disposition/key-custody
review. No uninstall/data deletion is automated. Preserve data/audit claims,
snapshots and key custody according to the owner retention decision.

Audit volume exhaustion makes requests fail closed; monitor writable capacity,
inodes and audit errors. Rotate file audit with a separately reviewed mechanism
that preserves history and reopens the file through the supported audit lifecycle;
no automatic rotation or off-host shipping is implemented. A Retain policy is
neither a backup nor worker/host-failure availability.

## Evidence

Reports contain check identifiers, status, run ID, revision/digest and cleanup
status only; API error bodies, JWTs, tokens, shares, secret values and private
keys are suppressed. `--output` must be outside repositories. Static fixtures
are clearly distinct from live evidence. Record actual environment, clean/dirty
source state, rendered digest, measured capacity, TLS/auth/audit checks, restart
and isolated recovery, failure/cleanup and operator signoff before M3b acceptance.
See [static evidence](evidence/2026-10-04-static.md).
