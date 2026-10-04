# pCloud milestone implementation progress

Status for this stacked PR: local implementation and validation, with no deployment authorization or live acceptance.

| Milestone | Implementation | Lab acceptance |
| --- | --- | --- |
| M3a lab filesystem storage | Available in this branch | Pending |
| M3b OpenBao secret management | Available in this branch | Pending |
| M3c observability filesystem backend | Available in this branch | Pending |
| M4 lab LGTM/Collector runtime | Next PR in the implementation series | Pending |

## Available decisions and work orders

- [M3a lab filesystem storage](adr/0001-lab-persistent-storage.md) and [package](../deploy/02-cluster-addons/storage/local-path/README.md).
- [M3b OpenBao secret management](adr/0002-lab-secret-management.md) and [package](../deploy/02-cluster-addons/secrets/openbao/README.md).
- [M3c observability filesystem backend](adr/0003-lab-observability-filesystem.md) and [package](../deploy/02-cluster-addons/storage/observability-filesystem/README.md).

Crossplane is deferred; it is not required for M3a–M5. Kafka and APISIX are M5. Implementation tests do not establish lab readiness, HA or DR.
