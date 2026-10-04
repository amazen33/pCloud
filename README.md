# pCloud

Reusable private-cloud deployment packages in one Git repository. Each layer contains its own deployment files and tests; no layer is a separate repository or Git submodule.

```
pCloud/
  .git/                         repository metadata (registered worktrees use a pointer)
  .github/workflows/infra.yml    static package checks (Layers 0-2)
  .github/workflows/layout.yml   layout/governance check on every change
  deploy/
    00-infra/private-hyperv/     Hyper-V provisioning and tests/
    01-k8s-engine/rke2-ansible/  Ubuntu/RKE2 configuration and tests/
    02-cluster-addons/           kube-vip manifests, tests/, evidence/
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

Only Layers 0-2 currently have implementation packages. Further observability, storage, secrets, Kafka and APISIX work follows the milestone tracker; see the source evidence and limitations before making readiness claims.

## Lab implementation progress

- [M3a lab filesystem storage](deploy/02-cluster-addons/storage/local-path/README.md): implemented with local validation; deployment and live acceptance pending.
- [M3b OpenBao secret management](deploy/02-cluster-addons/secrets/openbao/README.md): implemented with local validation; deployment and live acceptance pending.

See [milestones](docs/MILESTONES.md). Crossplane remains deferred for this lab.
