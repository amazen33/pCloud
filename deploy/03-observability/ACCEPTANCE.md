# M4 required lab acceptance

**All live cluster gates are pending.** Automated Live/Smoke deliberately return
INCOMPLETE after their subchecks. Record cluster, full revision/patch digest,
operator approval, versions, actual outputs and limitations outside source;
commit only reviewed dated evidence without credentials. This checklist does
not grant permission to change infrastructure.

| Required gate | Operation / isolated evidence |
| --- | --- |
| Predecessors | Current cluster health; M3a mounted byte/inode/host capacity, Retain/WFFC; M3b custody/rotation/audit/restart/restore; M3c POSIX/remount/different-UID and retained-probe-PV disposition |
| Runtime | All image IDs, restricted UID 10001 mounts, exact config/retention, pinned workers, resource consumption and ready inventory |
| Access | Trusted TLS SAN/chain; no/invalid credential read/write denial; ingest cannot query, query/admin cannot ingest; separate role Secret contents; unauthorized namespace cannot reach private backends; verified CNI and SNAT effects |
| Signals | Isolated conformance stack OTLP log/trace correlation and metric query within deadline; no unbounded run/user labels; no partial success; series bound |
| Service graph | Paired synthetic client/server spans create the expected Mimir edge; Grafana Tempo serviceMap configuration and visible edge; expiry/missing edge diagnostic |
| Platform view | Provisioned data sources/dashboard retrievable by authorized Grafana login; anonymous login disabled; query credential cannot administer |
| Alert receiver | Synthetic firing rule reaches the designated HTTPS test receiver with expected authenticated payload; redact credential/header values |
| Retention | Compare effective component configs and declared retention; in a separately approved disposable installation, record synthetic data expiry after >=24h plus component compaction/deletion delay; never shorten shared retention |
| Restart | Restart each authorized test component separately; old synthetic log/metric/trace remains queryable; new ingestion resumes; record drops/retry windows and mounted WAL/block evidence |
| Failure isolation | During each test component outage, read-only Layer 1/2 health remains good; record dependencies that also lose visibility |
| Soak | Owner-approved duration with ingress/query rates, component CPU/RAM, volume growth/free inodes, queue/refusal/drop metrics and no unexplained loss |
| Rollback / removal | Previous reviewed release/config restored only if data compatible; scoped removal preserves foreign objects, namespace and all persistent claims; disposition recorded |
| Recovery limits | No backup/HA/DR declared: document loss on host/volume failure; do not mark restore as tested or acceptable production RPO/RTO |

Smoke can exercise only a separately installed `purpose=conformance`
`pcloud-observe-test-*` stack with exact revision/digest review and consent.
It creates no Kubernetes resources. Synthetic data remains there until normal
retention; the operator owns scoped installation/removal and retained-PV
handling. A platform-purpose stack is rejected even with the consent flag.
A write that is expected to be denied is still Smoke, because a broken control
could accept it. Live uses GET only and performs no ingest, retention change,
restart or credential rotation. No automatic acceptance aggregator converts
these manual evidence requirements to PASS.
