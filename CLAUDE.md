# CLAUDE.md

Read [docs/CONTRACT.md](docs/CONTRACT.md) before any work in this repository.
It is the single authoritative working contract; this file adds only
pCloud-specific notes and never overrides it. The notes are the same as in
[AGENTS.md](AGENTS.md):

- Root: `D:\project\pCloud` (WSL `/mnt/d/project/pCloud`); origin
  `https://github.com/amazen33/pCloud.git`.
- Packages and their test entry points are listed in
  `tests/layout-manifest.json`. Run before every push, from the root:
  `python tests/verify-layout.py` and `python tests/test_verify_layout.py`,
  then each changed package's own test (see `README.md`).
- Package history before 2026-09-28 is in IOT-EE; see `docs/PROVENANCE.md`.
