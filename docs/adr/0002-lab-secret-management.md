# ADR-0002: lab secret management

Status: implementation decision under the owner's delegated engineering scope;
deployment approval and lab acceptance pending. Date: 2026-10-04.

## Context

The owner selected completion of the current lab before production and
authorized Codex to implement the remaining pCloud work. M3a supplies node-local
Retain/WFFC filesystem storage but has no current live acceptance. M3b needs
reusable secret storage, authenticated delivery and recovery without IOT-EE
configuration or production availability claims. The historical lab is a
single Hyper-V failure domain with limited, not freshly measured capacity.

## Decision

Implement `deploy/02-cluster-addons/secrets/openbao/` with OpenBao **2.7.1**,
verified immutable OCI manifest, trusted TLS, one integrated-Raft instance,
manual initialization/unseal, worker-local data and separate persistent audit
volumes. Enforce restricted PSA v1.35; bypass the upstream dev-mode entrypoint
with `/usr/bin/bao server`. Disable mlock for Raft and require disabled host swap.

Use exact Kubernetes SA/namespace/audience bindings and projected short-lived
reviewer tokens with TokenReview-only server RBAC. Provide KV v2, required CAS,
short client leases, explicit revocation and HMAC file auditing. Platform
installation and consumer secret/role ownership stay separate.

Also provide an explicit supplied Vault API profile. It renders nothing and
must demonstrate the same KV/auth/access behaviour; no broad compatibility,
provider-administration or recovery claim follows from selecting it.

## Options considered

| Option | Benefit | Cost / decision |
| --- | --- | --- |
| Kubernetes Secrets alone | Small runtime footprint | Does not provide this package's KV version/CAS, audited API and lease lifecycle; not selected for the reusable lab secrets service |
| Installed OpenBao with Raft | MPL-2.0 project, explicit storage/API boundary, independent operations | Another stateful server; manual key custody, downtime and audit operations; selected for lab implementation |
| Supplied Vault/OpenBao service | Reuse an operated service without lab server overhead | Must prove scopes, TLS, audit and recovery; supported through conformance, not assumed equivalent |
| HA / external auto-unseal | Availability and automated restart | Separate failure domains, KMS/custody, capacity and recovery design; deferred to production scope |

## Tradeoffs and consequences

The installed profile can demonstrate persistence and authorization while
remaining unavailable during seal/restart or worker/host loss. Retained local
PVCs do not provide replication or backup. Audit files are outside Raft snapshots;
audit exhaustion fails requests closed. Snapshot export and isolated restoration
are sensitive, operator-controlled operations; restored unseal custody uses the
original snapshot keys. No plaintext credentials belong in manifests or reports.

Conformance success still yields INCOMPLETE until observed restart/reunseal and
known-data isolated restore evidence. Production HA/DR, RPO/RTO, continuous
audit shipping, automatic rotation, injection and application configuration are
separate work. Crossplane has no requirement in this lab milestone.

## Actions

Implement the standalone package, schemas, locked upstream contracts, copied
package / real TLS / lifecycle safety checks, real Kustomize/schema validation,
CI and dispatcher registration. Before deployment select actual context,
measured capacity, accepted storage, TLS material, private management access,
operators/keyholders and reviewed revision/digest. Obtain deployment approval
under [the contract](../CONTRACT.md); record live conformance and recovery evidence.

Official references: [OpenBao 2.7.1 release](https://github.com/openbao/openbao/releases/tag/v2.7.1),
[Kubernetes authentication](https://openbao.org/docs/auth/kubernetes/),
[Raft storage](https://openbao.org/docs/configuration/storage/raft/).
