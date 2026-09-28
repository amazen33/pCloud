# Folder replacement plan

Prepared repository: D:\project\pCloud-prepared-20260928
Final repository: D:\project\pCloud
Recovery folder: D:\project\pCloud-worktree-recovery-20260928
Remote: https://github.com/amazen33/pCloud.git (verified empty)

After explicit approval, move the entire current pCloud folder to the recovery
path, then move the prepared repository into its final pCloud path. Refuse
if the recovery path exists. Repair IOT-EE worktree registrations to point at
the three recovered checkout paths. This preserves ALL existing file contents,
including ignored provider caches, the nested clone under layer0-portable/~/,
and layer2-evidence-pr-body.txt. No VM, state or deployed cluster is changed.

Inventory before replacement:
- deployment-status: clean, PR #35 correction commit ac63396.
- layer1-cilium: clean, commit 206e426.
- layer0-portable: commit 00017f7; untracked ~/iotee-pr35 nested clone,
  and ignored OpenTofu provider caches. The nested clone is clean at ebc272c.
- shared IOT-EE checkout: dirty; its files and branch remain untouched.

The old checkouts are recovery material, not deployment packages to publish.
The prepared tree contains only committed deployment source exported from
ac633969995830f3cd3504dd60ea9325fadeab04 and new layout/provenance documents.
No existing repository has been deleted or reset. Nothing has been pushed to
pCloud on GitHub. Review the local prepared tree before approving replacement.
