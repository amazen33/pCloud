# pCloud milestones

Status updated on 2026-10-05. M3a Stage 1 was deployed from
`3a7eee6c673821a56331a7b324ef65f827db3e7d`; this change corrects its Live
checker/render defaults and records the actual installation. Other deployment
claims retain their dated limits; see [deployment evidence and limits](../deploy/README.md).

These identifiers formalize the layer-based plan. A design, implementation,
static pass and lab acceptance are separate states. A milestone requiring
lab acceptance stays incomplete until the dated evidence exists.

| Milestone | Scope | Current state | Exit requirement |
| --- | --- | --- | --- |
| M0 | Repository split, governance, product boundaries and test dispatch | Implemented in the repository; the explanatory ownership ADR is still proposed | Preserve the contract, layout guard, CI invocation and package independence |
| M1 | Layer 0 Hyper-V provisioning | Package merged; this revision not applied to the lab | Reviewed plan, host-capacity check, authorized apply and recorded inventory handover |
| M2 | Layer 1 OS/RKE2 engine | Package merged; dedicated data-mount policy not applied to the lab | Authorized fresh install or separate migration, health/security checks and revision-specific evidence |
| M3a | Layer 2 persistent filesystem storage | Stage 1 installed on 2026-10-05: worker mounts/capacity, nine-object apply, provisioner available and read-only Live pass; isolated acceptance incomplete | Authorized persistence/helper/affinity/rebind/controller-recovery tests, retained-data disposition and dated evidence |
| M3b | Layer 2 secret management | OpenBao lab / supplied API profiles implemented with TLS/Raft, guarded lifecycle, local TLS/static/render tests and CI/dispatcher integration; no deployment/live acceptance | Measured capacity, trusted TLS and key custody; live authorization/rotation/audit, restart/reunseal and isolated snapshot restore evidence |
| M3c | Observability data backend / object-storage capability | Native monolithic filesystem profile implemented: distinct claims, pinned storage fragments/capability, guarded probes and local Linux POSIX/static/render validation; no deployment | Accepted mounted filesystem/remount/UID checks, retained test-PV disposition; signal-specific ingest/query/recovery evidence with M4 |
| M4 | Layer 3 OpenTelemetry and LGTM | Standalone lab runtime, explicit M3c inputs, TLS role gateway, capability, guarded API conformance and offline/render checks implemented; isolated Linux signal/TLS/dashboard/alert/restart exercise passed; no cluster deployment | Required M3 capabilities or supplied equivalents; independent ingest/query, access, retention, recovery and soak evidence |
| M5 | Kafka and APISIX installation packages | Planned | Per-package persistence/routing, authentication, recovery and rollback evidence |
| M6 | Production HA, DR, capacity and operations | Not designed | Separate topology, capacity evidence, RPO/RTO and restore tests, runbooks and named operators |

kube-vip is the implemented portion of Layer 2, with historical lab smoke
evidence dated 2026-09-28. It does not complete M3a-M3c. M1/M2 need current
revision evidence; M3a implementation can proceed independently against a
compatible supplied cluster. Its installation cannot proceed until its own
node, filesystem and capacity prerequisites pass.

## Static local-PV profile

PR #4 adds the independent [static local-PV package](../deploy/02-storage/local-pv/README.md).
It records worker disk preparation and worker-01 temporary binding/remount
evidence dated 2026-09-30. That record is historical; current site readiness is
unverified and the test Kubernetes objects were removed. It does not complete
M3a local-path acceptance or M3b/M3c/M4 deployment. Both profiles have explicit
[selection and handover boundaries](../deploy/README.md#storage-profile-selection).

## Current bounded work

M3a is defined in the [accepted lab storage ADR](adr/0001-lab-persistent-storage.md)
and the [Claude implementation work order](work-orders/M3a-lab-storage.md).
The owner authorized Codex to implement the work in the Claude engineering
role and selected lab completion before production. The work order covers
code and static checks; see the [package](../deploy/02-cluster-addons/storage/local-path/README.md).
The owner authorized Stage 1 installation on 2026-10-05; current mounts,
capacity, provisioner rollout and read-only Live pass are recorded in
[the dated installation evidence](../deploy/02-cluster-addons/storage/local-path/evidence/2026-10-05-lab-install.md).
Isolated storage acceptance remains a later gate.
No infrastructure modification is authorized by this milestone tracker.
The [installation proposal](work-orders/M3a-lab-install.md) lists the actual
remaining inputs and deployment/acceptance gate; local static results are
recorded separately from live acceptance.

M3c selects the explicit native filesystem alternative for one monolithic
instance each of Loki, Tempo and Mimir; see the
[backend decision](adr/0003-lab-observability-filesystem.md),
[work order](work-orders/M3c-lab-backend.md) and
[standalone package](../deploy/02-cluster-addons/storage/observability-filesystem/README.md).
It provides no S3 API. M4 installation requires accepted M3a/M3b capabilities
and M3c mounted-filesystem readiness; signal-specific M3c data acceptance is
completed alongside M4 ingest/query and component recovery. Implementation
may proceed against the explicit handover while those live gates remain pending.

M3b is implemented under the same delegated engineering scope; see the
[secret-management decision](adr/0002-lab-secret-management.md),
[work order](work-orders/M3b-lab-secrets.md) and
[standalone package](../deploy/02-cluster-addons/secrets/openbao/README.md).
Its automated conformance deliberately stays INCOMPLETE until operator restart
and isolated restore evidence is recorded. No lab changes have been performed.
M4's [lab LGTM/Collector package](../deploy/03-observability/README.md) is
implemented under [ADR-0004](adr/0004-lab-observability-runtime.md) and its
[work order](work-orders/M4-lab-observability.md). Automated conformance stays
INCOMPLETE until the [live acceptance gates](../deploy/03-observability/ACCEPTANCE.md)
are evidenced. M3/M4 deployment prerequisites remain pending; the next bounded
implementation milestone is M5's independent Kafka and APISIX lab packages.

## Decision gates

- Owner acceptance of the M3a lab profile and its Pod Security exception is
  recorded in ADR-0001. Implementation is available for review; installation
  and acceptance remain separate gates.
- Provide measured host/node capacity and a mounted application-storage
  filesystem before an installation work order. Disk creation, formatting
  and migration require their own reviewed scope under the contract.
- Revisit replicated storage when worker-failure availability is required.
  Replicas on this one Hyper-V host do not provide host-failure protection.
- Revisit Crossplane when pCloud has a defined need for self-service,
  continuously reconciled infrastructure APIs with supported providers.
  It is not a dependency of M3a-M5.

The owner overrode the normal implementation-role split for this work.
[The contract](CONTRACT.md) continues to govern ownership and deployment gates.
