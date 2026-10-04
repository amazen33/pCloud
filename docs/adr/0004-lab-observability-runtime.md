# ADR-0004: restricted monolithic LGTM lab runtime

**Status:** Accepted for implementation under delegated lab engineering scope;
deployment and lab acceptance pending.
**Date:** 2026-10-04
**Deciders:** Project owner delegated the remaining lab implementation to Codex
in the Claude engineering role. Site-specific installation needs its own approval.

## Context

M3a/M3b/M3c have implementation and local checks but no accepted live installation.
The lab is one Hyper-V host; HA/DR claims are inappropriate. M3c selected native
filesystem storage for Loki 3.7.8, Tempo 2.10.8 and Mimir 2.17.11, each monolithic.
M4 must preserve those explicit outputs without sibling code/state reads, protect
access, keep application work independent of telemetry outages, and expose
reviewable signal/graph/alert and lifecycle gates without declaring them passed.

## Decision

Implement a self-contained `deploy/03-observability` lab package, one instance
each of the three backends, Grafana OSS 13.2.3, Collector contrib 0.161.0 and
nginx unprivileged 1.30.5-alpine. Lock public OCI artifacts and primary source
contracts. Render local Kubernetes resources and validate real Kustomize/schema
output. Consume explicit JSON backend handover/schema/hash; M3c owns namespace
and backend claims, M4 adds Grafana's distinct retained claim only.

Use a single HTTP OTLP Collector gateway with bounded in-memory queues/retries;
no per-node agent, durable replay or application auto-instrumentation. Tempo's
metrics generator sends service graphs/span metrics to Mimir; Grafana has
provisioned sources and a platform dashboard. Mimir loads static YAML platform rules through its read-only `local` loader
(ConfigMap), explicitly replacing the candidate filesystem rule-bucket fragment
whose mutable object format differs. Signal blocks/WAL and Alertmanager storage
retain their M3c paths. Mimir manages its native Alertmanager, explicitly enabled in addition to Mimir's `all` target, sending to a supplied HTTPS receiver with Secret-based
token/CA. Collector scrapes bounded component health/metrics; total stack loss
needs an independent external monitor.

A TLS gateway uses separate ingest/query/admin password-hash files, strict
methods/query route allowlists, and strips backend auth/tenant headers. Grafana
additionally requires its login. Kubernetes policies restrict private backend
paths. Secrets are explicit existing namespace references under M3b/operator
custody; no automatic OpenBao synchronization or new root-token use.

Trust is a single platform scope. Query users read all platform data; no tenant
isolation, SSO or consumer identity-to-IOT tenant mapping. Client traffic uses
verified TLS; backend namespace traffic is plaintext under enforced policies.
Cluster/network admins remain trusted. This model must change before serving
mutually untrusted tenants. Only the lab profile is implemented.

## Options considered

| Option | Complexity / capacity | Consequence |
| --- | --- | --- |
| Separate chart operators/distributed stores, agents plus gateways | High for this host | Extra replicas/CRDs and orchestration; requires replicated/object storage and separate design |
| Grafana all-in-one demo container | Low | Runtime/volume/access boundaries and lifecycle not independently controlled |
| Independent monolithic workloads and explicit gateway | Moderate | Selected: portable package, clear storage/role boundaries; single-instance outages and local disk loss remain |

## Trade-offs and consequences

- Native configs and locks avoid remote render dependencies and chart defaults,
  but pCloud owns version-specific config/rehearsal maintenance.
- Password roles/private policies are practical for the trusted lab; no enterprise
  identity or tenant isolation is implied. Operator credential delivery/rotation
  and target CNI enforcement are required.
- Retain/WFFC and fixed workers keep data stable across process restarts; host
  failure is not protected. No backup is declared.
- In-memory queues bound capacity but lose data on restart, overflow or prolonged
  outage. Consumers must export asynchronously with bounded failure handling.
- StatefulSets use OnDelete for operator-controlled upgrades. Template/secret
  changes need explicit reload/restart; applying manifests alone is insufficient.
- Live is GET/Kubernetes-read only. Smoke only writes into a separately reviewed
  installed conformance namespace; success remains INCOMPLETE until manual
  receipt/retention/restart/isolation/soak/rollback evidence is recorded.

## Action items

1. Implement schemas, immutable pins, rendering, capability, read-only drift checks
   and isolated API conformance; direct package test/CI/dispatcher integration.
2. Validate actual pinned executables separately from Kubernetes schemas and
   document exact local results without promoting them to lab acceptance.
3. Supply real storage/custody/exposure/capacity inputs, authorize installation,
   and perform all package acceptance gates before starting platform dependents.
4. Design SSO, tenancy, HA/DR and production capacity under separate milestones.

## Primary contracts

- [Pinned Collector OTLP HTTP exporter](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.161.0/exporter/otlphttpexporter/README.md)
- [Pinned Collector Prometheus remote write](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.161.0/exporter/prometheusremotewriteexporter/README.md)
- [Grafana pinned defaults](https://github.com/grafana/grafana/blob/v13.2.3/conf/defaults.ini)
- [nginx Basic authorization](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html)
- [M3c backend selection](0003-lab-observability-filesystem.md)

`UPSTREAM.json` records downloaded source bytes/hashes; image locks record OCI
metadata. Hashes prove integrity of downloaded artifacts, not signatures,
vulnerability scans, operator approval or successful deployment.
