# pCloud milestone implementation progress

Status for this stacked PR: local implementation and validation, with no deployment authorization or live acceptance.

| Milestone | Implementation | Lab acceptance |
| --- | --- | --- |
| M3a lab filesystem storage | Available in this branch | Pending |
| M3b OpenBao secret management | Next PR in the implementation series | Pending |
| M3c observability filesystem backend | Next PR in the implementation series | Pending |
| M4 lab LGTM/Collector runtime | Next PR in the implementation series | Pending |

## Available decisions and work orders

- [M3a lab filesystem storage](adr/0001-lab-persistent-storage.md) and [package](../deploy/02-cluster-addons/storage/local-path/README.md).

Crossplane is deferred; it is not required for M3a–M5. Kafka and APISIX are M5. Implementation tests do not establish lab readiness, HA or DR.
