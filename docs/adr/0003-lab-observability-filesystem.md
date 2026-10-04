# ADR-0003: lab observability filesystem backend

Status: implementation decision under owner-delegated lab engineering scope;
deployment approval and live acceptance pending. Date: 2026-10-04.

## Context

M3a supplies explicit worker-local Retain/WFFC storage but has not been accepted
live. M3b supplies a secrets installation/conformance boundary with pending
deployment. The owner selected current-lab completion before production.
M4 needs durable signal data, WAL and configuration boundaries without extra
unmeasured services on a single Hyper-V host. M3c allows an object store or
an explicit supported alternative; persistent filesystem volumes alone do not
choose the Loki/Tempo/Mimir topology or prove API behaviour.

## Decision

Implement native filesystem backends for **one monolithic instance each** of
Loki **3.7.8**, Tempo **2.10.8** and Mimir **2.17.11**. Their exact pinned
upstream examples/source support these storage modes. M3c owns separate claims,
storage-only fragments and filesystem conformance. M4 owns complete runtime
configuration, pinned binaries, network/auth, retention, APIs and recovery.

Use one separate Retain/WFFC RWO PVC per component, explicit worker placement,
full persistent mount and UID/GID/fsGroup 10001 under restricted PSA v1.35.
Persist WALs, object/block stores, indexes/caches and work directories;
separate Mimir blocks, ruler and Alertmanager stores. Export the versioned
backend capability and fragment hash through explicit files; no sibling imports.
Supplied filesystem claims are pinned by UID and get the same conformance gates.

## Options considered

| Option | Benefit | Cost / decision |
| --- | --- | --- |
| Native monolithic filesystem | No additional server or S3 credential lifecycle; supported lab configuration for this pinned set | One replica, worker affinity, file/inode limits, offline backup; selected for current lab |
| Installed S3-compatible object store | Shared object API and future backend topology options | Additional stateful service, capacity, TLS, credentials, API/conformance and recovery; separate later package if required |
| Supplied object store | Reuse an operated backend | Requires endpoint, credentials and verified S3/backend behaviours; not supplied or implemented by this decision |
| Replicated production storage/stack | Failure-domain availability and scaling | Needs independent hosts, capacity, HA/DR, retention and operations evidence; outside accepted lab scope |

## Tradeoffs and consequences

This is a lab profile with no S3 API or production-support claim. Native
filesystem modes must not be used to justify distributed deployments or
horizontal replicas. Changing major versions, topology or backend requires
a new configuration/data migration review; contemporary major-version docs
cannot substitute for these pinned source contracts.

RWO claims alone cannot prevent unauthorized Pod creators from mounting
another claim in the namespace. Protect namespace writes; test POSIX identity
denial and persistence independently. Requests are not quotas. Reserve at
least 20 percent plus explicit free byte/inode floors and measure combined
host/node growth with secrets and the rest of the platform before deployment.

Retain preserves data after claim removal; smoke leaves a reviewed inventory
of retained synthetic PVs for operator disposition. File copies must be
quiesced and recovered into isolated new volumes with the matching complete
runtime, followed by known-data queries. No backup/HA/DR/RPO/RTO guarantee is
inferred from rendering, filesystem probes or claim binding.

## Actions

Implement the standalone package, explicit profiles/schemas, recorded sources,
probe image lock, copied-package and real Linux POSIX checks, real render/schema
checks and CI/dispatcher registration. Preserve M3a/M3b staging. Installation
requires actual class, workers, namespace/claim UIDs, measured capacity and
environment/revision approval. M3c acceptance and M4 lab acceptance remain
linked until pinned component ingest/query, retention and isolated restore
evidence exists. Crossplane remains outside this lab's requirements.

Primary references: [Loki pinned filesystem documentation](https://github.com/grafana/loki/blob/v3.7.8/docs/sources/operations/storage/filesystem.md),
[Tempo pinned local configuration](https://github.com/grafana/tempo/blob/v2.10.8/example/docker-compose/local/tempo.yaml),
[Mimir pinned demo configuration](https://github.com/grafana/mimir/blob/mimir-2.17.11/docs/configurations/demo.yaml).
