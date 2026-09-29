# Proposed ADR -- pCloud and IOT-EE ownership and the observability boundary

Status: proposed on 2026-09-29; not yet accepted by the project owner. It
will be numbered when accepted. It changes no ownership rule: the
boundary already exists in `docs/CONTRACT.md` section C, and this ADR only
records how it applies to observability. The Layer 3 installation and the
published capability contract it describes remain planned:
`deploy/03-observability/` contains planning text only, no Layer 3 package
exists, and no pCloud Layer 3 installation has been verified.

## Context

`docs/CONTRACT.md` section C already divides the two products: pCloud owns
reusable platform installation and operation, including the shared
observability stack, platform dashboards and platform-level alerts, and
IOT-EE owns IoT business capabilities, service-generated telemetry and
correlation, IoT dashboards, business alerts and application SLOs.
Observability is the first shared technology where that line matters in
practice, because a platform stack (collection, storage, query,
dashboards, alerting) is useful only if applications send telemetry to it.

The direction for that stack (Loki, Grafana, Tempo and Mimir, fed by an
OpenTelemetry Collector) was decided in IOT-EE, in its ADR 0015 and its
proposed observability-installation-sequence ADR. Those documents also
placed installation planning inside IOT-EE. Since the deployment packages
moved to pCloud, this ADR records the boundary for the stack in detail.
IOT-EE's documents are background, not a dependency: pCloud must not read
another repository's files to install or test itself (contract section C).

## Decision

1. **pCloud owns the platform observability stack** as a deployment phase
   (`deploy/03-observability/`, planned): its installation, upgrade,
   operation, backup and restore procedures, platform health, platform
   dashboards and platform-level alerts, and the tests that prove it works
   without any IOT-EE code.
2. **pCloud owns the versioned platform capability contract** for that
   stack: the endpoints, protocols, authentication and access boundaries,
   retention and limits that a consumer can rely on. The contract is
   published by pCloud and versioned; a consumer selects a version.
3. **IOT-EE owns application-side observability**: instrumentation of its
   services, service-generated logs, metrics, traces and audit events,
   correlation propagation, IoT and domain dashboards, business alerts and
   application SLOs, and the application deployment settings that consume
   the contract.
4. **The two products operate independently.** pCloud installs and passes
   its tests with no IOT-EE code, image, configuration or data. IOT-EE
   builds and passes its own tests without a pCloud checkout and without a
   pCloud-installed cluster. Each keeps its tests in its own repository.
5. **Integration uses only versioned endpoints and configuration.**
   IOT-EE receives endpoints, credentials and settings through explicit
   environment configuration that the environment operator supplies. Neither
   product reads the other's repository files, inventory, Terraform or
   OpenTofu state, kubeconfig, credentials or working directory, and no
   credential is committed to either repository or written to test results.
6. **Existing compatible infrastructure can replace pCloud-provided
   services.** IOT-EE must deploy and run on pCloud or on an existing
   cluster that supplies the same capabilities; installing a private cloud
   is never an application prerequisite. A replacement is "compatible" only
   when it passes the same capability conformance tests as the pCloud
   package. It is not called a drop-in replacement on the basis of a
   product name or a catalog entry.
7. **A capability that is missing, not declared or not tested is not
   assumed.** A required acceptance check whose capability is unavailable
   ends SKIP with the reason, and one whose test does not exist ends NOT
   IMPLEMENTED; either makes the run incomplete, in each product's runner.

## Ownership summary

| Concern | pCloud | IOT-EE |
| --- | --- | --- |
| Collector, log, trace and metric storage and query, Grafana | Installs, upgrades, operates, backs up | Consumes |
| Endpoint, authentication and retention contract | Defines, versions, publishes | Selects a version, configures |
| Platform health dashboards and platform alerts | Owns | Does not own |
| Service instrumentation, correlation propagation, log and span content | Does not own | Owns |
| IoT and domain dashboards, business alerts, SLOs | Provides the way to load them (open question) | Owns their content |
| Synthetic ingest, query, access, retention and recovery tests | Owns | Does not own |
| Telemetry from an IOT-EE service reaching the stack | Not tested by pCloud | Owns the integration test |
| Existing replacement stack | Documents the conformance tests | Deploys against it if compatible |

## Consequences

- IOT-EE's `deploy/03-observability/` planning text should later shrink to
  instrumentation and contract consumption, and its dashboards and alerts
  stay in IOT-EE. That edit, and retiring IOT-EE's older copies of
  infrastructure packages, need separate scoped IOT-EE work orders that
  preserve local state and unique work; neither is part of this ADR.
- pCloud must publish the capability contract before IOT-EE can write
  integration tests against it. Until it exists, integration checks that
  depend on it are NOT IMPLEMENTED, and required ones make a run
  incomplete rather than passing.
- Layer 3 cannot be called implemented from this ADR, a rendered manifest
  or a passing CI run. It needs an installation on a target cluster, the
  acceptance tests in `deploy/03-observability/README.md` (run with the
  authorization those tests require), and dated evidence.
- Lab acceptance and production high availability, disaster recovery and
  capacity are separate claims with separate evidence.

## Not decided here

- The format and hosting of the capability contract, and its first version.
- How IOT-EE dashboards and alert rules reach a pCloud-operated Grafana
  without either product reading the other's files (a versioned bundle
  applied through a documented interface is the likely shape).
- Tool versions, deployment topology, storage backends and licences for
  the stack; see the open choices in `deploy/03-observability/README.md`.
- The tenancy model of the stack and how it maps to IOT-EE tenants.
- Who operates an existing replacement stack and how its conformance is
  re-verified over time.

## Alternatives rejected

- **Keep observability installation in IOT-EE.** Rejected: it leaves two
  installation copies with no versioning decision and makes the application
  repository responsible for platform operations (contract section C).
- **Let IOT-EE read pCloud's configuration or state to find endpoints.**
  Rejected: it couples the products' working directories and moves
  credentials across repository boundaries.
- **Make the pCloud stack mandatory for IOT-EE.** Rejected: IOT-EE must run
  on any compatible existing cluster.

## Cross-references

`docs/CONTRACT.md` sections C, D, E and F; `deploy/03-observability/README.md`;
`deploy/README.md`; `tests/README.md`.
