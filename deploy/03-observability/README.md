# Layer 3: shared observability (planning only)

**Status: NOT IMPLEMENTED.** This directory contains only this planning
document. There is no manifest, chart, values file, installer, inventory,
package test or evidence. No pCloud Layer 3 installation has been
verified, and this document claims none. It is deliberately not listed in
`tests/layout-manifest.json`, because it is not a package yet. The
dispatcher lists its two future checks (`l3-live-observability`,
`l3-smoke-observability`) as NOT IMPLEMENTED; see
[tests/README.md](../../tests/README.md). Rendering manifests or passing CI
would not complete this phase (`docs/CONTRACT.md` section E).

The direction is the existing one: the OpenTelemetry Collector feeding
Loki (logs), Tempo (traces) and Mimir (metrics), with Grafana for query,
dashboards and the Tempo-based service graph. It was decided in IOT-EE
(its ADR 0015 and proposed installation-sequence ADR). The ownership
boundary itself already exists in `docs/CONTRACT.md` section C, and
[the proposed ownership ADR](../../docs/adr/XXXX-proposed-pcloud-iot-ee-ownership-and-observability-boundary.md)
records how it applies here. The installation and the published capability
contract described below remain planned. This document selects no new
tool, version, chart or storage backend; choices that are still open are
listed as such.

## 1. Scope and capabilities

pCloud owns installation, upgrade, operation, backup and restore, platform
health, platform dashboards and platform-level alerts for the shared stack.
It does not own service instrumentation, IoT or domain dashboards, business
alerts or application SLOs; those belong to IOT-EE and are out of scope
here. This phase installs nothing for any application.

Planned capabilities, each of which must be provable without IOT-EE code:

| Capability | Meaning |
| --- | --- |
| Ingest | An OTLP endpoint accepts traces, logs and metrics from workloads |
| Store and query | Each signal can be queried back through a documented endpoint |
| Service graph | Paired synthetic client and server spans produce a service-to-service edge |
| Dashboards and alerts | Platform health dashboards exist; a platform alert reaches a receiver |
| Access control | Ingest, query and administration are separately authorized |
| Retention | Each signal has a declared retention, applied and observable |
| Recovery | Components restart, ingestion resumes, and a failed component does not harm other layers |
| Capability contract | pCloud publishes the versioned endpoints, access boundaries and limits a consumer relies on |

Out of scope for this phase: application instrumentation, Kafka, APISIX,
network-flow tools such as Cilium/Hubble (a separate cluster-networking
decision that does not replace the application service graph), and any
change to Layers 0-2.

## 2. Deployment profiles

Profile selection is explicit (`docs/CONTRACT.md` section D). None of these
profiles is implemented.

| Profile | Intent | Status |
| --- | --- | --- |
| `lab` | The Layer 3 package on the single-host lab cluster: minimal replicas, short retention, owner-approved soak. Proves function only | Planned; needs the prerequisites in section 3 |
| `existing-cluster` | The same package on another compatible cluster that already satisfies the prerequisites | Planned |
| `external` | Layer 3 is not installed. The environment supplies the endpoints of an existing stack; pCloud verifies conformance with the live acceptance tests and installs none of the stack's backend dependencies | Planned; conformance tests not written |
| `production` | Highly available, capacity-planned, disaster-recovery-tested, operated 24x7 | Not designed; separate evidence required (section 9) |

An alternative stack is compatible only when it passes the live acceptance
checks in section 7; it is not called a drop-in replacement without that
evidence.

## 3. Prerequisites and predecessor outputs

Layer 3 consumes outputs from earlier phases and never reads their working
directories or state. Prerequisites depend on the profile (section 2).

**Installed profiles** (`lab`, `existing-cluster`, `production`) need the
following. The storage and secret-management capabilities must be
appropriate to the design chosen for the profile; that design is open, and
a simpler form may be acceptable in the lab if its data-loss effect is
stated.

| Needed | From | Current status |
| --- | --- | --- |
| A healthy Kubernetes cluster and API access | Layer 1, or an existing cluster | The lab cluster ran healthy on 2026-09-27; that is earlier-installation evidence only |
| A way to expose in-cluster services to consumers | Layer 2 kube-vip, an existing load balancer or ingress | kube-vip installed and smoke-tested in the lab on 2026-09-28 |
| Persistent storage with a stated reclaim policy | A Layer 2 storage package or the environment | **Not designed**; no storage class is confirmed in the lab |
| Durable storage for logs, traces and metrics (object storage, or a stated alternative) | Layer 2 or the environment | **Not designed**; the backend is an open choice |
| Secret management for credentials and tokens | Layer 2 or the environment | **Not designed** |
| Capacity for the components and their data | The target environment | Not measured; the lab has one physical host and 20 GiB unmounted secondary disks |
| Pinned component versions and licences approved by the owner | The owner | Not chosen; several LGTM components are AGPLv3 and the licence position needs owner review before a non-lab profile selects them |
| Namespace and Pod Security decision | This phase | Open (restricted Pod Security is the default expectation; any exception needs a recorded reason) |

An installed profile cannot start until the storage and secret-management
capabilities its design requires exist.

**External profile.** pCloud installs nothing, so none of the storage,
object-storage or secret-management rows above apply to it. It needs:

| Needed | From | Current status |
| --- | --- | --- |
| The endpoints of the existing stack: ingest, log, metric and trace query, Grafana | The environment operator | Not supplied; no such environment is identified |
| Credentials and the CA certificate, supplied through explicit environment configuration | The environment operator | Not supplied; never committed |
| Permission to run the conformance checks, including a designated isolated test scope for synthetic writes | The environment operator | Not agreed; without it the write-based checks end SKIP and the run is incomplete |
| The live acceptance checks in section 7 | pCloud | **NOT IMPLEMENTED** |

## 4. Configuration inputs

Inputs are explicit, git-ignored where they hold site data or credentials,
and documented with an example file when a package exists. Planned inputs:

- target cluster access (kubeconfig or context), namespace names, and the
  chosen profile;
- storage class and object-storage settings, with sizes and reclaim policy;
- retention per signal, ingest and query limits, and resource requests and
  limits per component;
- exposure: hostnames or addresses, TLS material, and the network paths
  consumers use;
- authentication and authorization: identity source for Grafana users,
  credentials for ingest and query, and roles;
- alert routing: the receiver for platform alerts;
- component versions, recorded and pinned.

For the external profile the inputs are instead the supplied endpoints,
credentials and CA certificate, and the designated test scope for the
conformance checks; there is no cluster, storage or component-version input
because pCloud installs nothing.

Credentials are supplied at install time through the environment's secrets
mechanism. They are never committed and never written to test results or
evidence.

**Outputs.** The phase produces the capability contract for consumers: the
ingest endpoint and protocols, the log, metric and trace query endpoints,
the Grafana address, the authentication method, tenancy or scoping headers
if used, declared retention and limits, and the CA certificate for TLS.
Its format, versioning scheme and publication location are not decided
(section 9). A consumer receives these values through its own explicit
environment configuration.

## 5. Installation and upgrade

Not written. Constraints the future package must meet:

- installation is an explicit, separately approved operation against a
  named cluster and a reviewed revision; static CI never applies anything;
- artifacts are static and rendered in CI (the tool is an open choice) and
  schema-validated, like Layer 2, with component and schema versions pinned
  and recorded;
- components run under restricted Pod Security or a reviewed, labelled
  exception, with resource requests and limits set;
- installation runs behind a review of the rendered output and a diff
  against the cluster, and produces a recorded inventory and version set;
- upgrades change one reviewed version set at a time, are rehearsed in
  the lab first, and state their data-compatibility effect;
- the package is self-contained: its own inputs, tests and documentation,
  no dependence on another package's state.

## 6. Static validation

Static checks contact no cluster and will be added with the package, run
through its own test entry point and CI. Required content:

- rendered manifests parse and pass schema validation for the target
  Kubernetes version;
- every image and chart reference is pinned and no remote resource is
  fetched at render time;
- resource requests and limits, restricted Pod Security settings, storage
  class, retention values and access-control settings are present;
- the package refers to nothing outside its own directory;
- negative fixtures prove that an unpinned image, missing retention, or an
  unauthenticated endpoint configuration is rejected.

Until the package exists these checks are NOT IMPLEMENTED.

## 7. Live smoke and acceptance tests

These run against an installed stack (installed profiles) or against
supplied endpoints (external profile), are separate from static CI, and
must not use IOT-EE application code. Each check reports PASS, FAIL, SKIP
or NOT IMPLEMENTED; a required check that cannot run, including one that
lacks the authorization below, makes the run incomplete. Once implemented,
the read-only checks belong to `l3-live-observability` and the Smoke
operations to `l3-smoke-observability` in the dispatcher.

### Classification and authorization

| Class | Operations | Authorization needed |
| --- | --- | --- |
| **Live (read-only)** | Query health and status endpoints; read the effective configuration (retention, limits, authentication); read platform dashboards and alert state; queries with missing or wrong credentials that must be rejected; TLS certificate verification; the read-only health checks of Layers 1 and 2 | Permission to reach the environment and read-only credentials. Writes no telemetry and changes nothing |
| **Smoke (changes state)** | Synthetic writes, including ingest requests that must be rejected (if the control were broken they would write); temporary workloads such as the emitter; retention changes; component restart; backup and restore; removal and rollback; triggering a synthetic alert | The applicable owner or environment authorization for that operation, naming the cluster and the reviewed revision (`docs/CONTRACT.md` section F). The dispatcher's `-AllowClusterChanges` consent is required and does not replace that authorization |

Without the authorization a Smoke check ends SKIP with the reason.

**Isolation rules for every Smoke operation:**
- synthetic writes go only to a designated test scope (a dedicated
  namespace, tenant or instance), never to a scope that carries real data;
- retention tests use a disposable instance or namespace created for the
  test. They never alter retention on a shared or production instance; if
  no disposable instance can be provided the test is SKIP, not run on a
  shared one;
- restart, restore, removal and rollback act only on the resources the test
  created, or on an installation the authorization names. A restore goes
  to a disposable instance;
- cleanup deletes only resources the run created and labelled, records what
  it removed, and never deletes unrelated data;
- for the external profile, only the read-only Live checks and writes to
  the designated test scope apply. Restarting, removing or rolling back
  the supplied stack belongs to its operator and is not part of that
  profile's conformance set.

### Checks

**Ingest and query.**
- *Smoke (synthetic write, then read-only queries).* A temporary emitter
  sends one trace, one log record and one metric sample to the test scope.
  The log record carries the trace ID and span ID of the trace and a run
  identifier as a log field. The log is found by the run identifier and the
  trace by its trace ID, and the log's trace ID is shown to match the
  stored trace. Each query completes within a stated time limit.
- The metric is checked without correlating it by a per-run label: it uses
  a fixed test metric name and a small fixed set of label values, and is
  found by that name and the expected value within the test's time window.
  A unique run, trace or user identifier is never a metric label.
- *Cardinality.* The test asserts that it created no more than a stated
  small number of series, and that no identifier of unbounded value appears
  as a metric label. Sensitive identifiers are not metric labels either.
- *Service graph.* Paired synthetic client and server spans produce a
  service-to-service edge in Grafana. A missing or expired edge is visible
  as a diagnostic condition.

**Access controls.**
- *Live (read-only):* a query with no credentials or invalid credentials is
  rejected; an ingest-only credential cannot read data; the transport is
  encrypted with a certificate the consumer can verify; the effective
  authentication settings match the contract; no credential or token
  appears in component logs or in the test output.
- *Smoke (attempted writes that must be rejected, in the test scope):*
  ingest without valid credentials is rejected; a read-only role cannot
  write or change configuration; a query credential cannot ingest.
- If the profile uses tenancy or scoping, data written to one test scope
  cannot be read from another (*Smoke*, because it writes first). Whether
  the stack is multi-tenant is an open choice (section 9).

**Retention.** Each signal's retention is declared.
- *Live (read-only):* read the effective retention from the running
  component and compare it with the declared value. This is weaker evidence
  and is recorded as such.
- *Smoke (isolated resources only):* in a disposable instance or namespace
  configured with a deliberately short retention, synthetic data disappears
  after the stated interval. This never changes a shared or production
  instance's retention. The lab's retention values are not production
  values.

**Recovery (all Smoke, subject to the isolation rules).**
- each component of the test installation is restarted in turn; ingestion
  resumes, previously ingested synthetic data is still queryable, and the
  backlog or drop behaviour matches the declared, bounded policy;
- while a component is down, the read-only health checks of Layers 1 and 2
  still pass, showing failure isolation;
- if the profile declares backups, a backup is taken and restored into a
  disposable instance, which returns the synthetic data; if it declares
  none, the data-loss effect is stated;
- removal and rollback return the test installation to its earlier state,
  with the handling of persistent data stated explicitly (kept or
  deleted), and touch nothing outside it.

**Platform alerting (Smoke).** A synthetic failure condition in the test
scope triggers a platform alert that reaches a designated test receiver.

## 8. Rollback and recovery

Not written. The future package must document, per profile:

- rollback of an installation or upgrade to the previous version set, and
  what happens to persistent data in each case;
- restore from backup, or a clear statement that data is not recoverable;
- removal of the whole layer without affecting Layers 0-2 or any workload;
- the effect of an observability outage on consumers: telemetry export
  must fail bounded (explicit buffer and drop policy) and must not stop an
  application from doing its work.

Rollback is tested in an isolated lab installation, with the authorization
and isolation rules of section 7, before any claim is made.

## 9. Evidence requirements and known limitations

**Evidence.** Dated records go in `deploy/03-observability/evidence/` once
the package exists, in the style of
[the Layer 2 record](../02-cluster-addons/evidence/2026-09-28-hyperv-lab.md).
A record states the cluster, the revision, the version set, the checks run
with their actual output, what was not tested, and what is not established.
Test-run output from the dispatcher is current execution output, not
evidence, and is never written into the repository. Approvals, logs,
checksums and results are never invented; an unavailable verification is
recorded as blocked, not passed. Historical evidence is not edited.

**Lab acceptance is not production readiness.**

| Claim | Requires | Status |
| --- | --- | --- |
| Lab function | Sections 6 and 7 pass on the lab cluster, plus an owner-approved soak interval with recorded resource use, backlog and drop behaviour, and rollback evidence | Not started |
| Production HA | Replicas across failure domains, durable storage that survives node loss, upgrade without data loss, tested failover | Not designed |
| Disaster recovery | Owner-set recovery point and time targets, backups stored outside the failure domain, a timed restore | Not designed |
| Production capacity | Ingest and query volume measured or modelled, storage growth and retention sized, limits set from measurement | Not designed |
| 24x7 operation | Named on-call, alert routing to real receivers, runbooks, upgrade and incident procedures | Not designed |

The current lab is one physical Hyper-V host behind Windows NAT with one
control plane. A lab pass says nothing about HA, DR, capacity or 24x7
operation, and a short smoke test does not show continuous-service
stability.

**Open choices (not decided here).**
- component versions and the licence position for AGPLv3 components;
- the installation tool (for example Helm-rendered or Kustomize
  manifests) and the deployment mode of each component;
- the durable storage backend and whether one is required in the lab;
- the Collector topology (per-node agent, gateway, or both) and its
  buffering and drop policy;
- the Grafana identity source and role model;
- whether the stack is multi-tenant, and how tenants map to IOT-EE tenants;
- alert receiver, and how IOT-EE dashboards and alert rules are delivered
  to Grafana without either product reading the other's files;
- the capability contract's format, versioning and publication;
- retention values, resource sizing and the soak interval (owner input);
- whether the cluster's network layer adds a separate flow view.

**Known limitations.** No pCloud Layer 3 installation has been verified.
No storage class, object storage or secret-management backend is confirmed
in the lab, so an installed profile cannot start there today. There is no
package and no Layer 3 test; the external profile's conformance checks are
unwritten as well.
