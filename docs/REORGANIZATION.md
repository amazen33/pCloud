# Completed folder reorganization — 2026-09-28

Repository: D:\project\pCloud
Recovery folder: D:\project\pCloud-worktree-recovery-20260928
Remote: https://github.com/amazen33/pCloud.git

The owner authorized continuing and moving any files into the named recovery
folder. The entire former pCloud folder was moved there, preserving ALL file
contents, including ignored provider caches, the nested clone under
layer0-portable/~/iotee-pr35, and layer2-evidence-pr-body.txt. The tested
prepared repository was moved into D:\project\pCloud. IOT-EE worktree
registrations were repaired to reference the recovered checkout paths.
No deployed infrastructure was changed; no previous repository was deleted.

Preserved checkout inventory:
- deployment-status: clean, PR #35 correction commit ac63396.
- layer1-cilium: clean, commit 206e426.
- layer0-portable: commit 00017f7; untracked ~/iotee-pr35 nested clone,
  and ignored OpenTofu provider caches. The nested clone was clean at ebc272c.
- shared IOT-EE checkout: dirty; its files and branch were not changed.

The new repository contains committed deployment source from
ac633969995830f3cd3504dd60ea9325fadeab04 and its own layout/provenance
files. Old editing checkouts are recovery material, not packages to publish.
This records local folder organization; it does not assert a GitHub push,
new lab installation, production readiness, or completion of all Layer 2.
