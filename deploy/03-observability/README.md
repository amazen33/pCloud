# Layer 3: standalone LGTM / OpenTelemetry lab package

**Implementation available; no pCloud cluster deployment or lab acceptance.**
This package renders one Loki, Tempo, Mimir, Grafana, Collector and TLS gateway
instance, using explicit M3c filesystem handovers. It has its own schemas,
image locks, tests and lifecycle documentation. It imports no sibling package
and reads no other product's state. See the [decision](../../docs/adr/0004-lab-observability-runtime.md)
and [work order](../../docs/work-orders/M4-lab-observability.md).

## Scope and supported profile

Only `lab` is implemented. `purpose=platform` is the platform installation;
`purpose=conformance` is an independently installed disposable test stack in
`pcloud-observe-test-*`. Production, external-stack conformance, HA, object
storage, automatic upgrades, backups and automatic host/cluster modifications
are not implemented. pCloud owns installation and platform health. IOT-EE owns
its instrumentation, application configuration, domain dashboards and alerts.

| Component | Pinned release | Role |
| --- | --- | --- |
| Loki | 3.7.8 | Native OTLP log storage / query |
| Tempo | 2.10.8 | Trace storage and paired-span service graphs |
| Mimir | 2.17.11 | Metrics, platform rules and Alertmanager |
| Grafana OSS | 13.2.3 | Provisioned data sources and platform dashboard |
| Collector contrib | 0.161.0 | HTTP OTLP gateway and bounded metrics scrape/export |
| nginx unprivileged | 1.30.5-alpine | TLS and separate ingest/query/admin authorization |

`images.lock.json` records verified OCI index and Linux-amd64 manifest/config
digests, entrypoints and original image users. Workloads explicitly run UID/GID
10001 with fsGroup 10001, restricted Pod Security, read-only root filesystems,
no privilege escalation, dropped capabilities and no service-account token.
The runtime UID overrides Grafana/nginx/Mimir defaults; the local executable
exercise is distinct from verifying those permissions on the real PVCs.

No Helm chart is required: render produces local resources; Kustomize and
strict Kubernetes 1.35.0 schemas validate them. Rendering fetches no artifacts.
Backend services are ClusterIP only. There are no cluster RBAC resources,
DaemonSets, host paths, external plugins or privileged init containers.
Grafana plugin preinstallation/automatic updates, plugin administration, remote
key retrieval and update checks are disabled. Alerting is owned by Mimir;
Grafana-managed alerting is disabled in this profile. Mimir explicitly starts
`all,alertmanager`: the `all` module set alone omits Alertmanager.

## Prerequisites and explicit inputs

1. Kubernetes 1.35, healthy untainted workers and a CNI that actually enforces
   ingress and egress NetworkPolicy. The lab's historical records do not prove
   these gates for this patch.
2. Accepted M3a Retain / WaitForFirstConsumer filesystem class, capacity and
   node-mount evidence, or a compatible accepted supplied capability.
3. Accepted M3b secret custody / rotation capability or a supplied equivalent.
   This package consumes existing Kubernetes Secrets: it does not store an
   OpenBao root token, initialize a secret server or automatically synchronize
   secret contents. An authorized operator must deliver and rotate them.
4. M3c exports `backend-capability.json` and `backend-fragments.json`. Operator
   exports are copied explicitly into this package's ignored input locations,
   never read from a sibling working directory. Their namespace matches the
   runtime namespace; original versions, fragment hash and schema must match.
   See [the interface snapshot](contracts/README.md). Each backend has a distinct
   existing claim pinned to its chosen worker; this package creates only the
   separate Grafana claim. M3c owns the namespace and backend claims.
5. `site.json`, based on `site.example.json`: context, namespace, purpose,
   gateway hostname/HTTPS origin, trusted client CA path, explicit worker and
   storage settings, Secret names, consumer and alert CIDRs, HTTPS alert
   receiver, retention, resource budget, query deadline and proposed soak.
6. A route from the operator/application network to the gateway ClusterIP,
   through a separately reviewed exposure configuration. This package creates
   no LoadBalancer, ingress or NAT changes. External routing must preserve
   gateway TLS and the original client-address policy; verify SNAT/CNI effects.

The example requests total 700m CPU / 1344Mi RAM, with limits 3200m / 2688Mi,
and adds a 2Gi Grafana claim to the 16Gi backend requests. These are provisional
lab settings, not measured capacity or enforceable filesystem quotas. Budget
free bytes/inodes and host thin-disk allocation, alongside OpenBao and RKE2.
Example retention is 24h for each signal; proposed soak is 24h. Operator review
must accept actual capacity, retention and soak before installation.

### Existing Secret contracts

All four Secrets exist in the runtime namespace and have separate names.
No plaintext values, private keys, htpasswd files or generated Secret YAML
belong in Git or test reports. Use the accepted custody mechanism to deliver:

| Secret input | Required keys | Purpose |
| --- | --- | --- |
| `tls_secret` | `tls.crt`, `tls.key` | Valid server chain/private key; SAN matches gateway hostname |
| `access_secret` | `ingest.htpasswd`, `query.htpasswd`, `admin.htpasswd` | Separate strong passwords/user sets per role; compatible crypt hashes |
| `grafana_secret` | `admin-password`, `secret-key` | Grafana admin login and stable encryption/signing key |
| `alert_secret` | `token`, `ca.crt` | Alert webhook bearer credential and trusted receiver CA |

Only the gateway mounts role hashes/TLS key; only Grafana mounts its login/key;
only Mimir mounts the webhook token/CA. Mount mode is 0440 / fsGroup 10001.
Preflight reads Secret metadata only: contents, validity, distinct role files
and rotation must be verified under operator custody. Basic credentials have
separate role files; reusing users/passwords across them defeats separation
and fails live negative checks. Grafana UI needs gateway admin auth plus its
own login; disable sign-up and anonymous access. Local users are the lab
identity profile; SSO/team roles remain future work. Grafana sessions and
plugin/data-source administration are accessible only through the admin route.

## Access and capability contract

`python observability.py capability --site site.json --backend backend-capability.json --fragments backend-fragments.json`
exports `pcloud.observability/v1`: versions, artifact hash, endpoint/auth
boundaries, retention and loss limits. Publish the reviewed export through an
operator-controlled artifact channel; consumers configure it explicitly.

| Gateway path | Authorized credential | Allowed operation |
| --- | --- | --- |
| `/ingest/v1/logs`, `/ingest/v1/traces`, `/ingest/v1/metrics` | ingest | POST OTLP/HTTP only |
| `/query/loki/...`, `/query/tempo/...`, `/query/mimir/prometheus/...` | query | Allowlisted GET query/health endpoints only |
| `/grafana/` | gateway admin plus Grafana login | Grafana administration/UI |

Unknown routes return 404; disallowed methods return 405. Backend auth and
client tenant headers are stripped. There is one trusted platform scope:
**no IOT tenant isolation or user-to-tenant mapping is claimed**. All authorized
query users can read all data. Do not expose this profile to mutually
untrusted tenants. TLS 1.2+ protects client-to-gateway traffic; backend traffic
is plaintext inside the isolated namespace. Cluster/network administrators
can bypass it. This is an explicit constrained-lab trust model.

The Collector accepts HTTP OTLP only (JSON/protobuf), not public OTLP/gRPC.
It exports native Loki OTLP, Tempo OTLP/HTTP and Prometheus remote write to
Mimir. Platform YAML rules use Mimir's read-only `local` rule loader from an
immutable ConfigMap; this explicitly replaces M3c's optional filesystem rule
bucket fragment. Signal blocks/WAL and Alertmanager storage keep the persistent
M3c paths. Rule-management APIs stay disabled.

Batches are bounded at 256 records; exporter queues at 256 batches,
with one consumer and 30s maximum retries. Memory limiting precedes batching.
Queues are in-memory: restart, overflow, rejected telemetry and outages beyond
the retry window can lose data. Consumers must configure bounded asynchronous
export/retry and let application work proceed during an observability outage.
No durable Collector replay guarantee is made. Metric resource promotion is
restricted to service name/namespace; arbitrary input metric labels still need
application cardinality discipline and Mimir limits. Synthetic metrics have
fixed labels, never run IDs. Loki indexes only service name/namespace; run and
trace identifiers stay structured metadata. Tempo generates service graphs
and span metrics, which Mimir stores; Grafana Tempo serviceMap points to Mimir.

## Offline validation

From any working directory (substitute the package path):

```powershell
python -m pip install -r requirements.txt
python tests/verify-observability.py
python tests/verify-observability.py --render
```

The direct CI entry is `python deploy/03-observability/tests/verify-observability.py --render`.
`--render` needs kubectl 1.35.0 and kubeconform 0.7.0, and downloads public
Kubernetes schemas if uncached; it contacts no cluster. Tests cover schemas,
explicit handover/digest, storage/retention, pinned source bytes, access routes,
restricted workloads, copied-package execution and simulated signal/graph/alert
lifecycle. They do not establish server startup or live acceptance.

Optional Linux executable exercise (large public artifact downloads):

```bash
python tests/fetch-runtime.py --directory /tmp/pcloud-observe-runtime-unique
python tests/validate-runtime.py --directory /tmp/pcloud-observe-runtime-unique
```

Use a new child of the Linux temporary directory (`/tmp`, or `TMPDIR=/var/tmp`
where WSL clears /tmp between sessions). The fetcher verifies immutable Linux image manifests and
each layer checksum, extracts only needed executable/config/library
files and archive-validated library aliases, and uses no container daemon. The exercise creates synthetic temporary
files, locally generated test-only TLS/credentials, and loopback processes;
root execution drops child processes to UID/GID 10001. It rewrites component
DNS/path/port settings to avoid local conflicts, so it is not proof of the
unmodified container/PVC environment. Test processes and synthetic data are
removed on exit; operator removes only the downloaded temporary runtime folder
after checking its resolved path. See the [dated evidence](evidence/2026-10-04-static.md)
for actual results and limits.

## Installation, upgrade and live checks

See [the operational runbook](RUNBOOK.md) for the concrete review, install,
restart/rollback/removal sequence and persistence handling. There is no
automatic apply/delete in this CLI. Render/preflight are available before a
separate environment/revision installation approval:

```powershell
python observability.py render --site site.json --backend backend-capability.json --fragments backend-fragments.json
python observability.py preflight --site site.json --backend backend-capability.json --fragments backend-fragments.json
```

Live uses `PCLOUD_OBSERVE_INGEST`, `PCLOUD_OBSERVE_QUERY`,
`PCLOUD_OBSERVE_ADMIN` environment variables, each `username:password`, and the
trusted CA from site input. It reads Kubernetes resources and endpoint health,
compares rendered configuration/specs and checks query access denials. It never
writes telemetry. Raw API/command errors and credentials are suppressed;
redirects and ambient proxies are disabled. Reports must be new JSON files
outside both product repositories. The read-only check remains **INCOMPLETE**
until independent acceptance evidence is provided.

Smoke requires an already authorized, separately installed conformance stack,
explicit write consent, current revision and exact artifact digest in an
ignored `review.json` with exactly `revision`, `artifact_sha256`, `namespace`,
`purpose` (`conformance`). It rejects the platform namespace before any access.
The test writes bounded synthetic signals, denies missing/query/admin ingest
credentials, queries correlated logs/traces/metrics, checks bounded metric
labels, paired-span service graph and firing platform alert. No resource is
created/deleted or shared retention changed. Test data expires under its
isolated retention. Failure leaves diagnostic data in that isolated stack.

```powershell
python observability.py smoke --site site.json --backend backend-capability.json --fragments backend-fragments.json --review review.json --allow-cluster-changes --output /absolute/outside/repositories/report.json
```

Successful automated API checks remain **INCOMPLETE** pending actual receiver
receipt, retention expiry, restart/recovery, layer failure isolation, rollback
and owner-approved soak. The [acceptance checklist](ACCEPTANCE.md) defines those
required records; no absent check silently becomes PASS.
