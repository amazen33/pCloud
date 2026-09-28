#!/usr/bin/env python3
"""Repository-wide layout invariants for pCloud (docs/CONTRACT.md, sections A and F).

Usage: python tests/verify-layout.py [--root DIR] [--manifest FILE]

Reads the package list from tests/layout-manifest.json. Exits 0 when every
check passes, 1 with one line per violation otherwise. Standard library and
the git CLI only; it never modifies the repository.

On disk (tracked or not): nested .git directories/pointer files, .gitmodules,
recovery/backup directories, Git bundles, literal "~" path components.
Tracked only (these may exist locally when ignored): local state and
configuration such as *.tfstate, *.tfplan, terraform.tfvars, real Ansible
inventories and .env files; submodule entries (gitlinks).
Manifest: governance pointers, required files, package test entry points
that exist, are tracked, and are invoked by a CI workflow; README references
to test scripts must resolve.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

DEFAULT_ROOT = Path(__file__).resolve().parents[1]

# Directories that are editing backups or recovery material, never source.
# Dated "-backup-YYYYMMDD" folders are ad hoc snapshots; ordinary names such
# as "disaster-recovery" or "backup-restore" remain allowed for documentation.
RECOVERY_DIR = re.compile(
    r"(worktree-recovery|split-backup|(^|[-_.])backup-\d{8}|(^|[-_.])recovery-\d{8})",
    re.IGNORECASE,
)
BUNDLE_SUFFIXES = (".bundle",)
# Local state/configuration that may exist while ignored but must never be tracked.
TRACKED_FORBIDDEN = (
    "*.tfstate", "*.tfstate.*", "*.tfplan", "terraform.tfvars",
    "*.auto.tfvars", "*.auto.tfvars.json", ".env", ".env.*",
    "hosts.ini", "kubeconfig", "*.kubeconfig",
)
TRACKED_ALLOWED = ("*.example", "*.example.*")
SCRIPT_REF = re.compile(r"[A-Za-z0-9_./-]*tests/[A-Za-z0-9_.-]+\.(?:py|sh|ps1)")


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def tracked_entries(root: Path) -> dict[str, str]:
    """Map each index path (POSIX) to its mode."""
    entries: dict[str, str] = {}
    for record in git(root, "ls-files", "-s", "-z").split("\0"):
        if record:
            meta, path = record.split("\t", 1)
            entries[path] = meta.split()[0]
    return entries


def check_repository_root(root: Path, errors: list[str]) -> bool:
    try:
        top = Path(git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    except RuntimeError as exc:
        errors.append(f"not a Git repository: {root} ({exc})")
        return False
    if top != root.resolve():
        errors.append(f"--root must be the repository top level; Git reports {top}")
        return False
    if not (root / ".git").is_dir():
        errors.append("root .git must be a directory, not a worktree pointer")
    return True


def check_disk(root: Path, errors: list[str]) -> None:
    if (root / ".gitmodules").exists():
        errors.append(".gitmodules present: packages must not be submodules")
    for directory, folders, files in os.walk(root):
        path = Path(directory)
        relative = path.relative_to(root)
        if path == root:
            folders[:] = [name for name in folders if name != ".git"]
        elif ".git" in folders or ".git" in files:
            errors.append(f"nested Git metadata: {relative.as_posix()}/.git")
            folders[:] = [name for name in folders if name != ".git"]
        for name in list(folders):
            if name == "~":
                errors.append(f"literal '~' directory: {(relative / name).as_posix()}")
            if RECOVERY_DIR.search(name):
                errors.append(f"recovery/backup directory inside the product: {(relative / name).as_posix()}")
        for name in files:
            if name == "~":
                errors.append(f"literal '~' file: {(relative / name).as_posix()}")
            if name.endswith(BUNDLE_SUFFIXES):
                errors.append(f"Git bundle inside the product: {(relative / name).as_posix()}")


def check_tracked(entries: dict[str, str], errors: list[str]) -> None:
    for path, mode in sorted(entries.items()):
        parts = PurePosixPath(path).parts
        name = parts[-1]
        if mode == "160000":
            errors.append(f"submodule/gitlink tracked: {path}")
        if "~" in parts:
            errors.append(f"tracked path with a literal '~' component: {path}")
        if any(RECOVERY_DIR.search(part) for part in parts[:-1]):
            errors.append(f"tracked recovery/backup directory: {path}")
        if name.endswith(BUNDLE_SUFFIXES):
            errors.append(f"tracked Git bundle: {path}")
        if any(fnmatch.fnmatch(name, pattern) for pattern in TRACKED_FORBIDDEN) and not any(
            fnmatch.fnmatch(name, pattern) for pattern in TRACKED_ALLOWED
        ):
            errors.append(f"tracked local state/configuration: {path}")


def workflow_text(root: Path, manifest: dict) -> str:
    folder = root / manifest["workflows_dir"]
    if not folder.is_dir():
        return ""
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(folder.iterdir())
        if path.suffix in (".yml", ".yaml")
    )


def check_manifest(root: Path, manifest: dict, entries: dict[str, str], errors: list[str]) -> None:
    governance = manifest["governance"]
    contract = governance["contract"]
    if contract not in entries:
        errors.append(f"authoritative contract missing or untracked: {contract}")
    for pointer in governance["pointers"]:
        path = root / pointer
        if pointer not in entries or not path.is_file():
            errors.append(f"governance pointer missing or untracked: {pointer}")
        elif contract not in path.read_text(encoding="utf-8"):
            errors.append(f"{pointer} must reference the authoritative {contract}")
    for required in manifest.get("required_files", []):
        if required not in entries:
            errors.append(f"required file missing or untracked: {required}")

    ci = workflow_text(root, manifest)
    if not ci:
        errors.append(f"no CI workflows found in {manifest['workflows_dir']}")
    for check in manifest.get("root_checks", []):
        if check not in entries:
            errors.append(f"root check missing or untracked: {check}")
        elif check not in ci:
            errors.append(f"root check not invoked by CI: {check}")
    for package in manifest["packages"]:
        base, test = package["path"], package["test"]
        entry = f"{base}/{test}"
        if not (root / base).is_dir():
            errors.append(f"package directory missing: {base}")
            continue
        if not (root / base / "README.md").is_file():
            errors.append(f"package README missing: {base}/README.md")
        if not (root / entry).is_file():
            errors.append(f"package test entry point missing: {entry}")
        elif entry not in entries:
            errors.append(f"package test entry point not tracked: {entry}")
        elif entry not in ci and not (base in ci and test in ci):
            errors.append(f"package test not invoked by CI: {entry}")


def _norm(path: str) -> str:
    normal = posixpath.normpath(path)
    return "" if normal == "." else normal


def resolves(md: PurePosixPath, token: str, tracked: set[str]) -> bool:
    """A README script reference resolves when it names a tracked file relative
    to the README's folder, its parent folder or the repository root. Inside a
    package (not at the root), a shortened reference such as `tests/x.py` in a
    table or `/path/to/<package>/tests/x.py` also resolves when that tests/
    script is tracked in the README's folder or parent-folder subtree."""
    folder = md.parent
    if not token.startswith("/"):
        for base in (folder, folder.parent, PurePosixPath(".")):
            if _norm(posixpath.join(str(base), token)) in tracked:
                return True
    suffix = token[token.rfind("tests/"):]
    for base in {_norm(str(folder)), _norm(str(folder.parent))} - {""}:
        if any(path.startswith(base + "/") and path.endswith("/" + suffix) for path in tracked):
            return True
    return False


def check_readme_references(root: Path, entries: dict[str, str], errors: list[str]) -> None:
    tracked = set(entries)
    for path in sorted(p for p in tracked if p.endswith(".md")):
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        for token in sorted(set(SCRIPT_REF.findall(text))):
            if not resolves(PurePosixPath(path), token, tracked):
                errors.append(f"{path} references a test script that does not exist: {token}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    manifest_path = args.manifest or root / "tests" / "layout-manifest.json"

    errors: list[str] = []
    if check_repository_root(root, errors):
        try:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"cannot read layout manifest {manifest_path}: {exc}")
            manifest = None
        entries = tracked_entries(root)
        check_disk(root, errors)
        check_tracked(entries, errors)
        if manifest is not None:
            check_manifest(root, manifest, entries, errors)
        check_readme_references(root, entries, errors)

    if errors:
        for error in errors:
            print(f"LAYOUT ERROR: {error}", file=sys.stderr)
        return 1
    print(f"pCloud layout checks passed: one repository, {len(manifest['packages'])} "
          "package test entry points, no recovery material or tracked local state.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
