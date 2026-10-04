# Testing pCloud

This is the authoritative guide to testing pCloud. pCloud tests
infrastructure functionality: host provisioning, the Kubernetes engine,
cluster add-ons and, when implemented, the shared observability stack. It
does not test IOT-EE application behaviour; IOT-EE keeps its own tests in
its own repository.

Every package keeps its own test entry point and can be tested alone after
being copied out of this repository (see each package README). The
dispatcher `tests/run.ps1` runs those same entry points by mode and adds no
test logic of its own. CI calls the package entry points directly.

## Quick start

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests/run.ps1          # Windows PowerShell 5.1
```

```bash
pwsh -NoProfile -File tests/run.ps1                                          # PowerShell 7, Linux/macOS/Windows
```

The default is Static mode for all packages. Useful options:

| Option | Effect |
| --- | --- |
| `-Mode Static\|Live\|Smoke\|All` | Select checks by mode (default `Static`) |
| `-Package <path>` | Only this package, for example `deploy/02-cluster-addons` (`root` for the repository checks); comma-separated for several. Naming a planned package makes its checks required |
| `-Check <id>` | Only these checks, by id (see `-List`); a named check is required even if it is extended |
| `-Extended` | Also run the extended Static checks |
| `-AllowClusterChanges` | Consent for Smoke checks, which create and remove temporary cluster resources |
| `-List` | Print the selected checks and how each would run; runs nothing |
| `-ResultsDir <dir>` | Where to write results; must be outside the repository |

## Modes

| Mode | Contacts | May change | Notes |
| --- | --- | --- | --- |
| **Static** (default) | No managed host or cluster | Nothing; temporary folders only | "Static" means no access to live infrastructure, not necessarily no network. The core checks need no network. `-Extended` adds `tofu init` (downloads the pinned OpenTofu providers) and `kubeconform` (downloads Kubernetes schemas) |
| **Live** | An existing environment, read-only | Nothing on hosts or clusters; reports go to the results folder | Uses each package's git-ignored inputs, such as the real `inventory/hosts.ini`. A check is registered only after all of its tasks were confirmed read-only |
| **Smoke** | An existing cluster | Temporary resources that the check creates and removes | Runs only with `-AllowClusterChanges`. Without it, nothing runs, the missing consent is reported and the run is incomplete |
| **All** | Static (extended), Live and Smoke | As above | Smoke still needs `-AllowClusterChanges`; All never makes Smoke changes silently |

No mode provisions, repairs or removes infrastructure: the dispatcher never
runs `tofu apply`, `deploy-layer0.ps1`, `prep-hyperv-host.ps1`,
`site-rke2.yml`, `heal.yml` or `kubectl apply` of a package. Those remain
separately approved human actions described in each package README.

## Statuses, result and exit codes

Each check ends in one of:

| Status | Meaning |
| --- | --- |
| `PASS` | Ran and exited 0 |
| `FAIL` | Ran and exited non-zero, or could not be started (program missing or failing to launch). A launch failure is always `FAIL`; it never reuses an exit code left by an earlier check |
| `SKIP` | Did not run; the reason is printed: extended check without `-Extended`, missing prerequisite or input, or missing consent |
| `NOT IMPLEMENTED` | A planned check; no implementation exists yet |

A selected check is *required*, except:

- an extended check, unless `-Extended`, `-Mode All` or `-Check` selected it;
- a check of a planned package (currently Layer 3), unless `-Package` or
  `-Check` names it. It is still listed as `NOT IMPLEMENTED (optional)`.

The run result and exit code:

| Exit | Result | When |
| --- | --- | --- |
| 0 | `PASS` | Every required check passed |
| 1 | `FAIL` | Any check failed (takes precedence) |
| 2 | Usage or configuration error | An unknown mode, package or check name; results folder inside the repository; dispatcher catalog disagrees with `tests/layout-manifest.json` |
| 3 | `INCOMPLETE` | A required check was skipped or is not implemented, even if every other check passed; or no check was executed at all, including a valid package and mode that have no checks. A run that executed nothing never reports `PASS` |

Live, Smoke and All currently end `INCOMPLETE` (exit 3), because some of
their checks are not implemented yet (see below).

## Prerequisites by platform

| Checks | Windows | Linux (or WSL) |
| --- | --- | --- |
| Dispatcher | Windows PowerShell 5.1 or PowerShell 7 | PowerShell 7 (`pwsh`) |
| Repository layout (`root`) | Python 3.12+, PyYAML 6.0.3, Git | Same |
| Layer 0 | Windows PowerShell 5.1 or PowerShell 7; `-Extended` needs `tofu` 1.6+ | PowerShell 7; `-Extended` needs `tofu` |
| Layer 1 | **Only through WSL**: `wsl.exe` with a Linux distribution that has Bash and ansible-core. Windows PowerShell does not run Bash or Ansible itself; the dispatcher calls `wsl.exe -e bash -lc ...` and reports `SKIP` (prerequisite missing) when WSL or `ansible-playbook` inside it is not available | Bash and ansible-core (`python -m pip install ansible-core`) |
| Layer 2 | Python 3.12+ and PyYAML 6.0.3; `-Extended` needs `kubectl` and `kubeconform` on PATH | Same |
| Live checks | Layer 1 through WSL, with SSH access and trusted host keys inside the WSL distribution | SSH access and trusted host keys |

## Checks by deployment phase

Static commands are shown from the repository root and work from any
directory; the Live Ansible commands run from the package directory. The
dispatcher runs every check from its package directory, as a copied package
would. Ids are what `-Check` and `-List` use.

### Repository (`root`)

| Id | Mode | Command | Proves |
| --- | --- | --- | --- |
| `root-layout` | Static | `python tests/verify-layout.py` | One repository, no nested Git metadata, recovery material or tracked local state; each package test exists, is tracked and is run by CI; README test references resolve |
| `root-layout-regressions` | Static | `python tests/test_verify_layout.py` | Each layout rejection and registered linked-worktree acceptance, in throwaway repositories (48 tests) |

The dispatcher's own tests, `tests/run.Tests.ps1` (39 cases), check
selection including planned packages, exit codes, empty selections, launch
failures, consent and the results location with stub checks, and list the
real catalog to check its safety rules; CI runs them with PowerShell 7 on
Linux and Windows PowerShell 5.1 on Windows.

### Layer 0: `deploy/00-infra/private-hyperv`

| Id | Mode | Command | Proves |
| --- | --- | --- | --- |
| `l0-static` | Static | `deploy/00-infra/private-hyperv/tests/verify.ps1` | Scripts parse; storage capacity rules (3 accepted, 4 rejected cases); the package refers to nothing outside itself; `.gitignore` covers settings, state and disks; 33 helper unit tests |
| `l0-static-tofu` | Static, extended | `deploy/00-infra/private-hyperv/tests/verify.ps1 -RunTofu` | Adds `tofu fmt`, locked `init` and `validate` in a temporary copy, and the settings expression through the real `tofu console` |
| `l0-live-check` | Live | — | NOT IMPLEMENTED: `scripts/check-layer0.ps1` is read-only on the host but writes `out/` and may create `.terraform` inside the package; it needs a report-location option before the dispatcher runs it. Run it by hand as the package README describes |

### Layer 1: `deploy/01-k8s-engine/rke2-ansible`

| Id | Mode | Command | Proves |
| --- | --- | --- | --- |
| `l1-static` | Static | `bash deploy/01-k8s-engine/rke2-ansible/tests/verify-layer1.sh` | Syntax of every playbook; example inventory accepted; five `tests/inventories/bad-*.ini` rejected; documentation addresses rejected for a live run; default CNI and server template agree; only `ansible.builtin` modules; real inventory not tracked |
| `l1-live-inventory` | Live | `ansible-playbook -i inventory/hosts.ini tests/validate-inventory-live.yml` | The real inventory passes the live-run rules (contacts no host) |
| `l1-live-guard-cni` | Live | `ansible-playbook -i inventory/hosts.ini guard-cni.yml` | Installed primary CNI and ingress match the reviewed choice. Tasks: `assert`, `stat`, and two `command` reads with `changed_when: false` |
| `l1-live-health` | Live | `ansible-playbook -i inventory/hosts.ini health.yml -e report_dir=<results>` | Node services, Ready condition, disk and memory headroom, API readiness. Read-only on nodes; the JSON report goes to the results folder instead of the package's `reports/` |
| `l1-smoke-psa-probe` | Smoke | — | NOT IMPLEMENTED: the privileged-pod rejection is a manual README procedure |

The dispatcher sets `ANSIBLE_CONFIG` to the package's `ansible.cfg`, because
Ansible ignores `ansible.cfg` in a world-writable directory such as a
Windows drive mounted in WSL.

### Layer 2: `deploy/02-cluster-addons`

| Id | Mode | Command | Proves |
| --- | --- | --- | --- |
| `l2-static` | Static | `python deploy/02-cluster-addons/tests/verify-layer2.py` | Resources are local; the vendored upstream manifest is byte-identical; images and services-only mode are the reviewed ones; the address pool is valid; the smoke manifest meets restricted Pod Security; with fake `kubectl` and `kubeconform` in a temporary folder, a failed render fails without running `kubeconform`, a failed validation fails, and success passes |
| `l2-static-render` | Static, extended | `python deploy/02-cluster-addons/tests/verify-layer2.py --render` | Adds `kubectl kustomize` and `kubeconform -strict` (Kubernetes 1.35.0 schemas) for the render and `tests/smoke.yaml` |
| `l2-live-preflight` | Live | — | NOT IMPLEMENTED: the read-only `kubectl` preflight is a manual README procedure |
| `l2-smoke-loadbalancer` | Smoke | — | NOT IMPLEMENTED: applying, checking and removing `tests/smoke.yaml` is a manual README procedure |

### Layer 3: `deploy/03-observability` (planned)

Layer 3 (LGTM and the OpenTelemetry Collector) is **planned; no package
exists**. Its checks stay visible: Live, Smoke and All list them as
`NOT IMPLEMENTED (optional)`. Naming the package or a check makes them
required, so the run ends `INCOMPLETE` (exit 3):

```bash
pwsh -NoProfile -File tests/run.ps1 -Mode All -Package deploy/03-observability
```

| Id | Mode | Planned proof |
| --- | --- | --- |
| `l3-live-observability` | Live | Synthetic log, metric and trace ingest and query; a service-graph edge; access controls; retention |
| `l3-smoke-observability` | Smoke | Restart, failure isolation and rollback |

Static checks for Layer 3 will be added with its package. None of these
checks may be described as passing until they are implemented and have run.

## Results and evidence

Each run writes one log per executed check and `summary.json` (mode, revision,
whether the working tree was clean, each check's status, reason and exit code,
and the verdict) to a new folder under the system temporary directory, for
example `%TEMP%\pcloud-test-results\<UTC time>-<id>` on Windows. `-ResultsDir`
chooses another folder; one inside the repository is refused. Results are
current execution output, not evidence: they are never written into the
repository.

Historical evidence lives in dated files under package `evidence/` folders
(for example `deploy/02-cluster-addons/evidence/`) and in the dated
verification sections of the package READMEs. The dispatcher never writes
them. Recording new evidence is a separate, reviewed documentation change
that cites the run it describes.

Credentials are never committed and never passed to checks as arguments.
Live checks read connection details only from each package's git-ignored
inputs and your SSH agent or keys; their output, and therefore the logs,
contains no secrets. Review a log before attaching it anywhere.

## CI

CI runs Static checks only and never reaches a host or cluster. It calls
the package entry points directly, so `tests/verify-layout.py` can prove
each one runs; it does not go through the dispatcher.

| Workflow and job | Runner | Runs |
| --- | --- | --- |
| `layout.yml` / `layout` | ubuntu-latest | `python tests/verify-layout.py`, `python tests/test_verify_layout.py`, `pwsh -NoProfile -File tests/run.Tests.ps1` |
| `layout.yml` / `dispatcher-windows` | windows-latest | `tests/run.Tests.ps1` under Windows PowerShell 5.1 |
| `infra.yml` / `layer0` | windows-latest | `deploy/00-infra/private-hyperv/tests/verify.ps1 -RunTofu` |
| `infra.yml` / `ansible` | ubuntu-latest | `deploy/01-k8s-engine/rke2-ansible/tests/verify-layer1.sh`, from the package directory |
| `infra.yml` / `kubernetes` | ubuntu-latest | `python deploy/02-cluster-addons/tests/verify-layer2.py --render` |

Before every push, run from the root `python tests/verify-layout.py`,
`python tests/test_verify_layout.py` and each changed package's test, or
the dispatcher with `-Extended` for the packages you changed.

## Adding or changing a check

- Put test logic in the package's own entry point, never only in CI or in
  the dispatcher, so an exported package can run it.
- A package's Static entry point in the dispatcher catalog must equal its
  `test` in `tests/layout-manifest.json`; the dispatcher refuses to run
  otherwise.
- Register a Live check only after confirming every task is read-only on
  the hosts it contacts, and write its reports outside the package.
- A check that creates cluster resources is a Smoke check and must clean up
  after itself.
- Replace a NOT IMPLEMENTED entry only in the change that makes the check
  pass, and extend `tests/run.Tests.ps1` when the dispatcher's rules change.

## Consumers

pCloud will own a versioned platform capability specification (for example
the observability endpoints Layer 3 provides). Consumers such as IOT-EE will
select a version and supply endpoints through explicit environment
configuration, never by reading this repository's files or state. The
specification does not exist yet.
