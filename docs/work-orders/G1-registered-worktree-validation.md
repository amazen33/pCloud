# G1: registered worktree validation

Scope within the owner's request to implement the remaining lab: fix the
repository gate that prevented validation in the owner-assigned managed
checkout `C:\Users\amaze\.codex\worktrees\c580\pCloud`.

Changes are limited to `tests/verify-layout.py`, its regression suite and
documentation. No Git metadata is rewritten and no checkout is created.
The guard now accepts a root `.git` pointer only when Git reports that root
as the top level, the metadata belongs under the common repository's
`worktrees/` directory, `git worktree list` registers the root and the
registration's `gitdir` points back to the same root `.git` file.
Root symlinks, arbitrary separate-git-dir checkouts, invalid registrations
and all nested `.git` metadata remain rejected. Site and review inputs are
also forbidden when tracked.

Acceptance: registered external fixture passes, tampered registration and
separate-git-dir fixtures fail, and all existing rejection tests pass.
The original root failure is retained in M3a's architecture-preparation
record; this change resolves it instead of skipping the gate.

The package entry point must be indexed for the root gate to recognize it.
Staging local implementation is not a commit, push, deployment or remote CI
result. No Git lock is removed. The existing Git process was confirmed to
be an fsmonitor daemon rather than a competing writer.
