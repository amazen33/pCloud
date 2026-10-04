# PR #4 integration validation — 2026-10-04

Integrated the static local-PV profile from
`5271cccbef332780035e084763230c36377b9afa` with main at
`b9f37354f70d29eb47e3d4395918e5ceec60c2e0`, after PRs #5–#9 merged.
Worktree: `C:\Users\amaze\.codex\worktrees\c580\pCloud`;
integration branch: `codex/pr4-storage-integration`.

The five shared-file conflicts were resolved by retaining both package
registrations, the current M3/M4 checks and the static local-PV checks.
Documentation distinguishes `pcloud-local` from `pcloud-local-retain`,
their separate ownership/markers/handovers, combined disk-capacity review,
and historical September evidence from current acceptance. The static-PV
CI job now uses the existing checksum-verified render-tool installer.
The storage playbooks/templates and M3/M4 runtime code were not modified.

## Local results

- Root layout: PASS, eight tracked package test entry points.
- Root rejection regressions: 53 PASS (56.358s).
- Dispatcher: 48 PASS / zero FAIL on each of PowerShell 7 and Windows
  PowerShell 5.1. The initial Windows 5.1 assertion failed because native
  sort ordering differs; comparing the exact reviewed Live allowlist as a
  set fixes that portable assertion without relaxing the allowed checks.
- Linux standalone `../tests/verify-local-pv.sh --render`: PASS, exit 0.
  Actual logic cases: 51 inventory/stamp, 80 selection/authorization,
  21 mount scenarios; four rendered volumes; guard ordering verified.
  Fresh/expired/future/failed/configuration-mismatched stamps and real
  inventory bypass attempts were exercised. Real local-kernel probe
  failures and refusal-before-change cases passed; test checks confirm
  `/etc/fstab` and the package mount-root state remained unchanged.
- Controlled render/schema failures propagate. Real Kustomize and strict
  Kubernetes 1.35.0 schemas: one StorageClass and four PVs valid, zero
  invalid/errors/skips.
- Linux versions: Python 3.14.4, ansible-core 2.20.1, Jinja 3.1.6,
  PyYAML 6.0.3; kubectl 1.35.0 and kubeconform 0.7.0. Render tools were
  downloaded to a private temporary directory, SHA256-verified against
  official release checksums, and removed after the completed run.
- 46 local Markdown targets resolved before adding this evidence link;
  authored working-tree/staged whitespace checks passed.

These are offline, local-controller and render checks. They do not refresh
the 2026-09-30 Hyper-V/worker evidence, establish current mounted capacity,
install PVs, migrate data, or complete M3a/M3b/M3c/M4 live acceptance.
Fresh remote CI and the GitHub merge are confirmed separately after this
integration revision is pushed; this record does not invent their outcome.
