#!/usr/bin/env python3
"""Verify one repository contains the three standalone deployment packages."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "deploy/00-infra/private-hyperv/tests/verify.ps1",
    "deploy/01-k8s-engine/rke2-ansible/tests/verify-layer1.sh",
    "deploy/02-cluster-addons/tests/verify-layer2.py",
    ".github/workflows/infra.yml",
    "docs/PROVENANCE.md",
)
for relative in REQUIRED:
    if not (ROOT / relative).is_file():
        raise SystemExit(f"Missing package entry: {relative}")
for directory, folders, files in os.walk(ROOT):
    path = Path(directory)
    if path != ROOT and (".git" in folders or ".git" in files):
        raise SystemExit(f"Nested Git metadata: {path.relative_to(ROOT)}/.git")
    if path == ROOT:
        folders[:] = [name for name in folders if name != ".git"]
if (ROOT / ".gitmodules").exists():
    raise SystemExit("Deployment packages must not be Git submodules")
print("pCloud layout checks passed: one repository, three package test entry points.")
