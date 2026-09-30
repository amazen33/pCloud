# pCloud working contract

This is the single authoritative statement of the shared working rules for
this repository. `AGENTS.md` and `CLAUDE.md` point here and add only
pCloud-specific notes; if they ever disagree with this file, this file wins
and the pointer must be corrected. The project owner has final authority.
Codex coordinates architectural boundaries, sequencing and review; Claude
implements bounded work orders passed by the owner.

## Products

| Product | Local root | GitHub | Owns |
| --- | --- | --- | --- |
| pCloud | `D:\project\pCloud` (WSL `/mnt/d/project/pCloud`) | https://github.com/amazen33/pCloud | Reusable cloud/platform installation and operational capabilities |
| IOT-EE | `D:\project\IOT-EE` (WSL `/mnt/d/project/IOT-EE`) | https://github.com/amazen33/IOT-EE | IoT business capabilities, services, application contracts, configuration and application deployment artifacts |

Recovery material lives only in `D:\project\pCloud-worktree-recovery-20260928`.
Recovery checkouts are not active source repositories. Never copy their
`.git` metadata, backups, bundles or runtime files into either product.

## A. Repository and directory rules

1. Each product has exactly one Git repository at its root. Layers, phases,
   services and plugins are ordinary directories, not repositories.
2. Never run `git init`, `git clone` or `git worktree add` inside a product.
   Never add submodules or embedded repositories.
3. Work in the existing checkout by default. If isolation is needed, inspect
   existing checkouts first and explain why. An authorized temporary
   checkout must be outside both product roots, at an explicit absolute path.
4. A self-contained package has its own installation/configuration files,
   its own tests and documented test command, explicit inputs and outputs,
   no hidden dependence on another package's working directory, and no
   dependence on another package's Terraform/OpenTofu state. It is not a
   separate Git repository.
5. Never store backup directories, repository clones, Git bundles, staging
   snapshots or recovery patches inside a product. Reviewed test fixtures
   and documented evidence are allowed; ad hoc editing backups are not.
6. Use shell-appropriate absolute paths: PowerShell `D:\project\pCloud`,
   WSL `/mnt/d/project/pCloud`. Never pass a Bash `~/path` to PowerShell and
   never create a literal `~` directory in a repository.
7. Never rename or move a repository or worktree parent without identifying
   its Git metadata and updating worktree registrations.

## B. Required preflight

Before editing, report the target product and absolute root, branch and
HEAD, configured origin, tracked/untracked/relevant ignored changes, the
governing `AGENTS.md`/`CLAUDE.md` instructions, and the exact scope and
intended files. Use `git rev-parse --show-toplevel`, `git remote -v` and
`git status`. Stop dependent work if the root or origin is wrong.

Preserve unrelated work: no `git reset --hard`, `git clean`, blanket
checkout/restore, or automatic stash of another session's work.

Git inspection and locks:

- Read-only Git inspection runs with `GIT_OPTIONAL_LOCKS=0` (for example
  `export GIT_OPTIONAL_LOCKS=0` in Bash/WSL, `$env:GIT_OPTIONAL_LOCKS = '0'`
  in PowerShell), so that `git status` and similar commands never take or
  leave the index lock.
- Never remove a Git lock just because Git reports it. This covers
  `.git/index.lock`, `.git/HEAD.lock`, `.git/objects/maintenance.lock` and
  any other `*.lock` under `.git/`, as well as leftover
  `.git/objects/??/tmp_obj_*` files. Check running Git processes and the
  file's age first; preserve a confirmed stale lock in the recovery
  directory before removing it. If a lock appears during your work, stop
  and report its path and timestamp; do not retry repeatedly or delete it
  automatically.
- One Git writer per checkout. If your execution environment cannot delete
  the lock and temporary files Git creates during a write (for example a
  mounted folder where unlink is not permitted), make no Git writes there:
  stop, and hand staging and committing to Codex on Windows with the exact
  diff. Setting `core.createObject=rename` does not solve this: Git still
  cannot remove `HEAD.lock`, `maintenance.lock` and other lock files.

## C. Product ownership

pCloud owns infrastructure provisioning; operating-system and Kubernetes
installation; reusable networking, storage and secrets packages; installation
and lifecycle of the shared observability stack; platform health, platform
dashboards, backup/restore procedures and platform-level alerts.

IOT-EE owns IoT domain/application code and services; device, tenant,
authorization, workflow, payment and migration behaviour; service-generated
logs, metrics, traces and audit events; correlation propagation; IoT
dashboards, business alerts and application SLOs; and application
deployment settings that consume platform capabilities.

IOT-EE must be deployable on pCloud or on an existing compatible cluster;
installing a private cloud is never an unavoidable application prerequisite.
pCloud must not depend on IOT-EE application code to install or to pass its
package tests. Cross-product integration uses documented, versioned
configuration and capability contracts; never read a sibling repository's
local files, credentials, inventory or state. For shared technology,
separate installation ownership from application-configuration ownership,
and do not maintain two independent copies of one installation package
without an explicit versioning decision.

## D. Phases and packages

pCloud keeps `deploy/00-infra/private-hyperv/`,
`deploy/01-k8s-engine/rke2-ansible/`, `deploy/02-cluster-addons/` and
`deploy/02-storage/local-pv/` (the storage phase).
Shared observability is planned under `deploy/03-observability/`; a planned
directory is not proof of implementation. Deployment files and package
tests stay together; root `tests/` holds repository-wide invariants.

Each phase documents: scope and capabilities; supported deployment
profiles; prerequisites and predecessor outputs; explicit configuration
inputs; installation and upgrade; offline validation; live smoke/acceptance
tests; rollback/recovery; evidence requirements and known limitations.
Profile selection is explicit; a phase may be reused or skipped when an
existing environment already supplies its capabilities. Alternative tools
and providers must pass the same capability/conformance tests and are not
called drop-in replacements without evidence.

## E. Observability sequence

Establish infrastructure, cluster health and storage/object-storage
capabilities first; then install and validate the shared observability
package; then deploy application workloads. IOT-EE supplies its own
instrumentation and dashboards; pCloud supplies documented endpoints and
access/configuration boundaries.

Always distinguish offline checks, successful lab installation, resilience
tests, production capacity, and HA/DR/24x7 readiness. Rendering manifests or
passing CI never completes a phase. Never invent approvals, logs, checksums,
test results or compliance evidence.

## F. CI/CD

- Every implemented package has a documented test entry point, run locally
  before a push and invoked unchanged by CI.
- Validate configuration schemas and rendered manifests; pin and record
  supported component and schema versions.
- Static CI stays separate from deployment. Deployment jobs select a
  specific environment and reviewed revision and use environment-scoped
  credentials behind protected gates.
- No OpenTofu/Terraform apply, cluster change, VM power-off or destructive
  cleanup without applicable owner authorization.
- `tests/verify-layout.py` (driven by `tests/layout-manifest.json`) rejects
  nested Git metadata, submodules, recovery/backup directories, bundles,
  literal `~` paths, tracked local state and configuration, and missing or
  untracked package test entry points. A package or root check counts as
  run by CI only when a `run:` step of an enabled workflow job executes it
  from the step's effective working directory; comments, step names and
  other jobs do not count. Recognition is limited to direct script calls and
  supported Python/Bash/PowerShell script invocations; merely passing a test
  path to echo, inline code or a syntax-only check does not count. Unknown
  invocation forms require extending the guard and its tests before use.
  This checks declared invocation, not general shell semantics or runtime
  success; actual CI must still pass. README references to test scripts must resolve
  exactly (README folder or repository root), except the documented
  shorthands `tests/<file>` and `/path/to/<package>/tests/<file>`, which are
  accepted below the root only for the declared test entry of a package
  that contains the README or sits below its folder. `tests/test_verify_layout.py`
  proves each rejection.
- Never bypass a failing check or force-push to make a gate green.

## G. Coordination and handoff

Before coding, state product, phase/service, files, acceptance criteria,
tests and non-goals. Do not expand a task into the other product without
explaining the dependency and obtaining a scoped work order. One writer per
branch or working tree; confirm no other agent is editing the same files.

At handoff report the repository, branch and exact commit; changed files and
resulting behaviour; tests run with their actual results; CI status;
unavailable verification and remaining risks; and the next bounded task.
Never approve your own PR. Push or merge only within the authorization
granted for that task.
