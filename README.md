# pCloud

Reusable private-cloud deployment packages in one Git repository. Each layer contains its own deployment files and tests; no layer is a separate repository or Git submodule.

```
pCloud/
  .git/                         repository metadata (registered worktrees use a pointer)
  .github/workflows/infra.yml    static package checks (Layers 0-3)
  .github/workflows/layout.yml   layout/governance check on every change
  deploy/
    00-infra/private-hyperv/     Hyper-V provisioning and tests/
    01-k8s-engine/rke2-ansible/  Ubuntu/RKE2 configuration and tests/
    02-storage/local-pv/         static local PVs, disk guards and tests/
    02-cluster-addons/           kube-vip manifests, tests/, evidence/
      storage/local-path/       standalone lab storage, schemas and guarded acceptance
      storage/observability-filesystem/  lab LGTM data/WAL claims, handover and POSIX probes
      secrets/openbao/          standalone lab TLS/Raft secrets and guarded lifecycle
    03-observability/           standalone lab LGTM/Collector, TLS gateway and tests/
  tests/README.md                testing guide: modes, prerequisites, checks by phase
  tests/run.ps1                  dispatcher to the package and repository checks
  tests/run.Tests.ps1            dispatcher tests
  tests/verify-layout.py         repository layout and governance check
  tests/test_verify_layout.py    proves each layout rejection
  tests/layout-manifest.json     packages, test entry points, governance files
  docs/CONTRACT.md               authoritative working contract (AGENTS.md, CLAUDE.md point here)
  docs/PROVENANCE.md             source revision and evidence limits
```

## Testing

See [tests/README.md](tests/README.md) for modes, prerequisites on Windows and Linux/WSL, and every check by deployment phase. From the repository root, the default static run of all packages is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests/run.ps1
```

Each package can also be copied to another project and tested alone with the command in its README. The root layout check rejects nested Git metadata, submodules, recovery/backup folders, bundles, literal `~` paths, tracked local state or inventories, and package tests that are missing, untracked or not executed by a CI `run:` step from the right working directory; `tests/test_verify_layout.py` proves each rejection. CI runs static checks only; it does not install infrastructure.

For deployment prerequisites and the installation sequence, see [deploy/README.md](deploy/README.md) and each package README. Local state, credentials and real inventory must remain ignored. This reorganization does not apply any infrastructure changes.

Layers 0-2 have implementation packages, including
[lab filesystem storage](deploy/02-cluster-addons/storage/local-path/README.md).
Storage and [lab secrets](deploy/02-cluster-addons/secrets/openbao/README.md)
and the [observability filesystem handover](deploy/02-cluster-addons/storage/observability-filesystem/README.md)
have local validation. Local-path storage has passed the required lab installation,
isolated smoke and serial worker reboot gates; secrets/backend remain undeployed. The [lab LGTM/Collector runtime](deploy/03-observability/README.md) is now
implemented with offline validation; no Layer 3 deployment or lab acceptance.
Kafka and APISIX remain planned.

The [milestone tracker](docs/MILESTONES.md) separates design, implementation
and lab acceptance. M3a has an [accepted lab storage design](docs/adr/0001-lab-persistent-storage.md)
and an [implementation work order](docs/work-orders/M3a-lab-storage.md).
The owner authorized Codex to implement that work in the Claude engineering
role. M3a's [Stage 1 lab installation](deploy/02-cluster-addons/storage/local-path/evidence/2026-10-05-lab-install.md)
has verified application mounts, a running provisioner and read-only Live pass;
its [isolated persistence/recovery smoke](deploy/02-cluster-addons/storage/local-path/evidence/2026-10-05-lab-smoke.md)
and cleanup passed. Both workers then passed
[reboot/remount/data persistence and cleanup](deploy/02-cluster-addons/storage/local-path/evidence/2026-10-05-lab-reboot.md),
completing M3a's required node-local lab gates. Crossplane is
deferred; it is not a dependency of this lab.

The [static local-PV package](deploy/02-storage/local-pv/README.md) is also
implemented, with dated disk-preparation/temporary smoke evidence from
2026-09-30. See [storage profile selection](deploy/README.md#storage-profile-selection)
for its relationship to the local-path lab profile and current acceptance gates.
