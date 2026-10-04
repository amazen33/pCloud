#!/usr/bin/env python3
"""Repository-wide layout invariants for pCloud (docs/CONTRACT.md, sections A and F).

Usage: python tests/verify-layout.py [--root DIR] [--manifest FILE]

Reads the package list from tests/layout-manifest.json. Exits 0 when every
check passes, 1 with one line per violation otherwise. Needs Python 3.10+,
PyYAML 6.0.3 and the git CLI; it never modifies the repository (read-only
Git commands run with GIT_OPTIONAL_LOCKS=0).

On disk (tracked or not): nested .git directories/pointer files, .gitmodules,
recovery/backup directories, Git bundles, literal "~" path components.
Tracked only (these may exist locally when ignored): local state and
configuration such as *.tfstate, *.tfplan, terraform.tfvars, real Ansible
inventories and .env files; submodule entries (gitlinks).
Manifest: governance pointers, required files, package test entry points
that exist and are tracked. CI coverage: each root check and package test
must be executed by a `run:` step of an enabled workflow job, resolved from
that step's effective working directory; comments, step names and other
jobs do not count. README references to test scripts must resolve exactly:
relative to the README's folder or to the repository root, or (below the
root only) as a bare `tests/<file>` or `/path/to/<name>/tests/<file>`
shorthand that names the declared test entry of a manifest package that
contains the README or sits below its folder.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath

import yaml

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
    "hosts.ini", "kubeconfig", "*.kubeconfig", "site.json", "review.json",
)
TRACKED_ALLOWED = ("*.example", "*.example.*")
SCRIPT_REF = re.compile(r"[A-Za-z0-9_./-]*tests/[A-Za-z0-9_.-]+\.(?:py|sh|ps1)")
BARE_SHORTHAND = re.compile(r"^tests/[A-Za-z0-9_.-]+$")
PLACEHOLDER = re.compile(r"^/path/to/[A-Za-z0-9_.-]+/(tests/[A-Za-z0-9_.-]+)$")
DISABLED = (False, "false", "${{ false }}")


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
    metadata = root / ".git"
    if metadata.is_symlink():
        errors.append("root .git must not be a symlink")
    elif not metadata.is_dir():
        # A registered linked worktree is one checkout of the same repository,
        # not a nested repository. Reject arbitrary gitdir pointers and detached
        # --separate-git-dir checkouts; verify Git's reciprocal registration.
        try:
            directory = Path(git(root, "rev-parse", "--absolute-git-dir").strip()).resolve()
            common_raw = Path(git(root, "rev-parse", "--git-common-dir").strip())
            common = (root / common_raw).resolve() if not common_raw.is_absolute() else common_raw.resolve()
            pointer = metadata.read_text(encoding="utf-8").strip()
            registrations = git(root, "worktree", "list", "--porcelain", "-z").split("\0")
            registered = any(field.startswith("worktree ") and Path(field[9:]).resolve() == root.resolve() for field in registrations)
            backref = Path((directory / "gitdir").read_text(encoding="utf-8").strip()).resolve()
            if not (pointer.startswith("gitdir: ") and directory.parent == common / "worktrees"
                    and common.is_dir() and common != directory and registered and backref == metadata.resolve()):
                raise ValueError("invalid registration")
        except (RuntimeError, OSError, ValueError):
            errors.append("root .git pointer must identify a registered linked worktree")
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


def _norm(path: str) -> str:
    normal = posixpath.normpath(path)
    return "" if normal == "." else normal


def _logical_lines(script: str) -> list[str]:
    """Join backslash (bash) and backtick (PowerShell) line continuations."""
    lines: list[str] = []
    current = ""
    for raw in script.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith("\\") or stripped.endswith("`"):
            current += stripped[:-1] + " "
            continue
        lines.append(current + raw)
        current = ""
    if current:
        lines.append(current)
    return lines


def _commands(line: str) -> list[list[str]]:
    """Recognize simple command boundaries; ambiguous quoting fails closed."""
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        commands: list[list[str]] = []
        current: list[str] = []
        for token in lexer:
            if token and all(char in ";&|()" for char in token):
                if current:
                    commands.append(current)
                current = []
            else:
                current.append(token)
        if current:
            commands.append(current)
        return commands
    except ValueError:
        return []


def _invoked_script(command: list[str]) -> str | None:
    """Count direct scripts or supported interpreter script operands, not data.

    Dynamic commands (-c, -m, -Command), syntax-only checks and unknown options
    do not establish invocation. This is declaration checking, not shell execution.
    """
    if not command:
        return None
    program = posixpath.basename(command[0]).lower()
    args = command[1:]
    if program.endswith((".py", ".sh", ".ps1")):
        return command[0]
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", program):
        while args and args[0].startswith("-"):
            option = args.pop(0)
            if option in ("-B", "-u", "-E", "-I", "-s", "-S", "-O", "-OO", "--"):
                continue
            return None
        return args[0] if args and args[0].endswith(".py") else None
    if program in ("bash", "sh", "bash.exe", "sh.exe"):
        while args and args[0].startswith("-"):
            option = args.pop(0)
            if option == "--" or (option.startswith("-") and len(option) > 1
                                   and set(option[1:]) <= set("euxv")):
                continue
            return None
        return args[0] if args and args[0].endswith(".sh") else None
    if program in ("pwsh", "powershell", "pwsh.exe", "powershell.exe"):
        while args and args[0].startswith("-"):
            option = args.pop(0).lower()
            if option in ("-noprofile", "-noninteractive", "-nologo"):
                continue
            if option == "-executionpolicy" and args:
                args.pop(0)
                continue
            if option == "-file":
                break
            return None
        return args[0] if args and args[0].lower().endswith(".ps1") else None
    return None


def executed_paths(root: Path, manifest: dict, errors: list[str]) -> set[str]:
    """Repository-relative paths that an enabled workflow job executes from a
    `run:` step, resolved against the step's effective working directory."""
    folder = root / manifest["workflows_dir"]
    executed: set[str] = set()
    if not folder.is_dir():
        return executed
    for path in sorted(folder.iterdir()):
        if path.suffix not in (".yml", ".yaml"):
            continue
        try:
            workflow = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            errors.append(f"cannot parse workflow {path.name}: {exc}")
            continue
        workflow_dir = ((workflow.get("defaults") or {}).get("run") or {}).get("working-directory", "")
        for job in (workflow.get("jobs") or {}).values():
            if not isinstance(job, dict) or job.get("if") in DISABLED:
                continue
            job_dir = ((job.get("defaults") or {}).get("run") or {}).get("working-directory", workflow_dir)
            for step in job.get("steps") or []:
                if not isinstance(step, dict) or "run" not in step or step.get("if") in DISABLED:
                    continue
                cwd = _norm(str(step.get("working-directory", job_dir) or ""))
                for line in _logical_lines(str(step["run"])):
                    for command in _commands(line):
                        token = _invoked_script(command)
                        if token is None or "${{" in token or token.startswith("/"):
                            continue
                        resolved = _norm(posixpath.join(cwd, token))
                        if resolved and not resolved.startswith("../"):
                            executed.add(resolved)
    return executed


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

    executed = executed_paths(root, manifest, errors)
    if not executed:
        errors.append(f"no executable CI run steps found in {manifest['workflows_dir']}")
    for check in manifest.get("root_checks", []):
        if check not in entries:
            errors.append(f"root check missing or untracked: {check}")
        elif check not in executed:
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
        elif entry not in executed:
            errors.append(f"package test not invoked by CI: {entry}")


def resolves(md: PurePosixPath, token: str, tracked: set[str], packages: list[dict]) -> bool:
    """Exact resolution only; a broken explicit path is never repaired by
    matching another file with the same name."""
    folder = str(md.parent)
    if token.startswith("/"):
        placeholder = PLACEHOLDER.match(token)
        # Documented '/path/to/<package>/tests/<file>' examples inside a package.
        return bool(placeholder) and any(
            (folder + "/").startswith(pkg["path"] + "/") and placeholder.group(1) == pkg["test"]
            for pkg in packages
        )
    for base in (folder, "."):
        candidate = _norm(posixpath.join(base, token))
        if candidate and not candidate.startswith("../") and candidate in tracked:
            return True
    if BARE_SHORTHAND.match(token) and folder != ".":
        # Bare 'tests/<file>' names the declared test entry of the package that
        # contains this README, or of a package below this README's folder
        # (such as the deploy/README.md package table). Never at the root.
        return any(
            token == pkg["test"] and ((folder + "/").startswith(pkg["path"] + "/")
                                      or pkg["path"].startswith(folder + "/"))
            for pkg in packages
        )
    return False


def check_readme_references(root: Path, entries: dict[str, str], packages: list[dict],
                            errors: list[str]) -> None:
    tracked = set(entries)
    for path in sorted(p for p in tracked if p.endswith(".md")):
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        for token in sorted(set(SCRIPT_REF.findall(text))):
            if not resolves(PurePosixPath(path), token, tracked, packages):
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
        check_readme_references(root, entries, manifest["packages"] if manifest else [], errors)

    if errors:
        for error in errors:
            print(f"LAYOUT ERROR: {error}", file=sys.stderr)
        return 1
    print(f"pCloud layout checks passed: one repository, {len(manifest['packages'])} "
          "package test entry points, no recovery material or tracked local state.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
