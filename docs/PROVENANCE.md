# Source provenance

The initial deployment packages were exported from amazen33/IOT-EE commit
ac633969995830f3cd3504dd60ea9325fadeab04 (PR #35 corrections), including
Layers 0 and 1 previously merged into IOT-EE and the kube-vip Layer 2 package.
Source repository: https://github.com/amazen33/IOT-EE
Review: https://github.com/amazen33/IOT-EE/pull/35

Only committed deploy/ files and the infrastructure workflow were copied.
Application code, production credentials, inventories, Terraform state,
provider caches, and old worktree Git pointers were not imported.
The former deploy/k8s path is deploy/02-cluster-addons here; package-relative
paths and the vendored cloud-controller bytes remain unchanged.

The September 28 lab evidence describes the earlier IOT-EE installation.
It is retained as historical evidence, not a claim that this reorganized
pCloud tree has been applied or that all of Layer 2 is complete.
Missing approvals/log excerpts/render digests are not reconstructed.

Old editing checkouts and all their untracked/ignored files were preserved
in D:\project\pCloud-worktree-recovery-20260928 with explicit owner approval.
Their Git history remains in IOT-EE; pCloud has its own root repository.
