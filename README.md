# pCloud

Reusable private-cloud deployment packages in one Git repository. Each layer contains its own deployment files and tests; no layer is a separate repository or Git submodule.

```
pCloud/
  .git/                         one repository, created locally
  .github/workflows/infra.yml    static package checks (Layers 0-2)
  .github/workflows/layout.yml   layout/governance check on every change
  deploy/
    00-infra/private-hyperv/     Hyper-V provisioning and tests/
    01-k8s-engine/rke2-ansible/  Ubuntu/RKE2 configuration and tests/
    02-cluster-addons/           kube-vip manifests, tests/, evidence/
  tests/verify-layout.py         repository layout and governance check
  tests/test_verify_layout.py    proves each layout rejection
  tests/layout-manifest.json     packages, test entry points, governance files
  docs/CONTRACT.md               authoritative working contract (AGENTS.md, CLAUDE.md point here)
  docs/PROVENANCE.md             source revision and evidence limits
```

## Verification

Run these commands from the repository root. Python 3.12+ with PyYAML 6.0.3 is required for Layer 2. Layer 1 requires Bash and ansible-core (use Linux or WSL); Layer 0 requires Windows PowerShell or PowerShell 7.

```powershell
python tests/verify-layout.py
python tests/test_verify_layout.py
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/00-infra/private-hyperv/tests/verify.ps1
```

```bash
bash deploy/01-k8s-engine/rke2-ansible/tests/verify-layer1.sh
python deploy/02-cluster-addons/tests/verify-layer2.py
```

Each package can also be copied to another project and tested independently. The root layout check rejects nested Git metadata, submodules, recovery/backup folders, bundles, literal `~` paths, tracked local state or inventories, and package tests that are missing, untracked or not run by CI; `tests/test_verify_layout.py` proves each rejection in throwaway repositories under the system temporary directory. Each package test verifies its own layer. CI runs static checks only; it does not install infrastructure.

For deployment prerequisites and the installation sequence, see [deploy/README.md](deploy/README.md) and each package README. Local state, credentials and real inventory must remain ignored. This reorganization does not apply any infrastructure changes.

Only Layers 0-2 currently have implementation packages. Observability, storage, secrets, Kafka and APISIX remain planned; see the source evidence and limitations before making readiness claims.
