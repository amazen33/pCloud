#!/usr/bin/env python3
"""Rejection tests for tests/verify-layout.py.

Usage: python tests/test_verify_layout.py

Each test builds a small throwaway Git repository in the system temporary
directory (never inside this product), confirms that the valid fixture
passes, applies exactly one violation, and asserts that the verifier fails
with the expected message. Needs PyYAML 6.0.3 (as the verifier) and git.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

VERIFIER = Path(__file__).resolve().with_name("verify-layout.py")
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "layout-test", "GIT_AUTHOR_EMAIL": "layout-test@example.invalid",
    "GIT_COMMITTER_NAME": "layout-test", "GIT_COMMITTER_EMAIL": "layout-test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0",
}
MANIFEST = {
    "governance": {"contract": "docs/CONTRACT.md", "pointers": ["AGENTS.md", "CLAUDE.md"]},
    "required_files": ["docs/PROVENANCE.md"],
    "workflows_dir": ".github/workflows",
    "root_checks": ["tests/verify-layout.py"],
    "packages": [
        {"path": "deploy/00-infra/demo", "test": "tests/verify.ps1"},
        {"path": "deploy/01-engine/demo", "test": "tests/verify-layer1.sh"},
    ],
}
WORKFLOW = """name: fixture
on: [push]
jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - run: python tests/verify-layout.py
      - run: ./deploy/00-infra/demo/tests/verify.ps1
  engine:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: deploy/01-engine/demo
    steps:
      - run: bash tests/verify-layer1.sh
"""
FILES = {
    "docs/CONTRACT.md": "# contract\n",
    "AGENTS.md": "Read docs/CONTRACT.md.\n",
    "CLAUDE.md": "Read docs/CONTRACT.md.\n",
    "docs/PROVENANCE.md": "provenance\n",
    "README.md": "Run `python tests/verify-layout.py` and `deploy/00-infra/demo/tests/verify.ps1`.\n",
    "tests/layout-manifest.json": json.dumps(MANIFEST, indent=2) + "\n",
    ".github/workflows/ci.yml": WORKFLOW,
    "deploy/00-infra/demo/README.md": "Test: `tests/verify.ps1`.\n",
    "deploy/00-infra/demo/tests/verify.ps1": "Write-Output ok\n",
    "deploy/00-infra/demo/terraform.tfvars.example": "name = \"lab\"\n",
    "deploy/01-engine/demo/README.md": "Test: `bash tests/verify-layer1.sh`.\n",
    "deploy/01-engine/demo/tests/verify-layer1.sh": "echo ok\n",
    "deploy/01-engine/demo/inventory/hosts.example.ini": "[servers]\n",
    "deploy/README.md": "| demo | `tests/verify.ps1` |\n",
    "docs/disaster-recovery/runbook.md": "Allowed: ordinary recovery documentation.\n",
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=GIT_ENV,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class LayoutRejectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pcloud-layout-test-"))
        self.repo = self.tmp / "product"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "tests").mkdir()
        shutil.copy(VERIFIER, self.repo / "tests" / "verify-layout.py")
        for relative, content in FILES.items():
            self.write(relative, content)
        self.commit()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, relative: str, content: str = "x\n") -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def commit(self) -> None:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "fixture")

    def run_verifier(self, root: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(VERIFIER), "--root", str(root or self.repo)],
            capture_output=True, text=True, env=GIT_ENV,
        )

    def assert_passes(self) -> None:
        result = self.run_verifier()
        self.assertEqual(result.returncode, 0, result.stderr)

    def assert_rejects(self, expected: str, root: Path | None = None) -> None:
        result = self.run_verifier(root)
        self.assertEqual(result.returncode, 1, f"expected rejection containing {expected!r}")
        self.assertIn(expected, result.stderr)

    # --- baseline -----------------------------------------------------------
    def test_valid_fixture_passes(self) -> None:
        self.assert_passes()

    def test_registered_external_worktree_passes(self) -> None:
        checkout=self.tmp / "linked"
        git(self.repo, "worktree", "add", "--detach", str(checkout))
        result=self.run_verifier(checkout)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_rejects_unregistered_worktree(self) -> None:
        checkout=self.tmp / "linked"
        git(self.repo, "worktree", "add", "--detach", str(checkout))
        registration=next((self.repo / ".git" / "worktrees").iterdir())
        (registration / "gitdir").write_text(str(self.tmp / "wrong" / ".git"))
        self.assert_rejects("root .git pointer must identify a registered linked worktree",checkout)

    def test_rejects_separate_git_directory(self) -> None:
        metadata=self.tmp / "external-metadata"
        # Git provides a valid top-level checkout, but this is not a linked
        # worktree with reciprocal registration in the product repository.
        git(self.repo,"init","--separate-git-dir",str(metadata))
        self.assert_rejects("root .git pointer must identify a registered linked worktree")

    def test_rejects_tracked_site_input(self) -> None:
        self.write("deploy/00-infra/demo/site.json","{}\n")
        self.commit()
        self.assert_rejects("tracked local state/configuration: deploy/00-infra/demo/site.json")

    def test_rejects_tracked_review_input(self) -> None:
        self.write("deploy/00-infra/demo/review.json","{}\n")
        self.commit()
        self.assert_rejects("tracked local state/configuration: deploy/00-infra/demo/review.json")

    def test_ignored_local_state_is_allowed(self) -> None:
        self.write(".gitignore", "*.tfstate\nterraform.tfvars\nhosts.ini\n")
        self.commit()
        self.write("deploy/00-infra/demo/terraform.tfstate", "{}\n")
        self.write("deploy/00-infra/demo/terraform.tfvars", "x = 1\n")
        self.write("deploy/01-engine/demo/inventory/hosts.ini", "[servers]\n")
        self.assert_passes()

    # --- repository structure -----------------------------------------------
    def test_rejects_nested_git_directory(self) -> None:
        (self.repo / "deploy/00-infra/demo/.git").mkdir()
        self.assert_rejects("nested Git metadata: deploy/00-infra/demo/.git")

    def test_rejects_nested_git_pointer_file(self) -> None:
        self.write("deploy/01-engine/demo/.git", "gitdir: D:/elsewhere/.git/worktrees/x\n")
        self.assert_rejects("nested Git metadata: deploy/01-engine/demo/.git")

    def test_rejects_embedded_clone(self) -> None:
        clone = self.repo / "deploy/00-infra/demo/vendor-clone"
        clone.mkdir()
        git(clone, "init", "-q")
        self.assert_rejects("nested Git metadata: deploy/00-infra/demo/vendor-clone/.git")

    def test_rejects_gitmodules_file(self) -> None:
        self.write(".gitmodules", "[submodule \"x\"]\n\tpath = x\n\turl = https://example.invalid/x\n")
        self.assert_rejects(".gitmodules present")

    def test_rejects_tracked_gitlink(self) -> None:
        git(self.repo, "update-index", "--add", "--cacheinfo",
            "160000,0123456789abcdef0123456789abcdef01234567,deploy/vendored")
        self.assert_rejects("submodule/gitlink tracked: deploy/vendored")

    def test_rejects_root_that_is_not_top_level(self) -> None:
        self.assert_rejects("--root must be the repository top level", self.repo / "deploy")

    def test_rejects_directory_that_is_not_a_repository(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assert_rejects("not a Git repository", plain)

    # --- recovery material and paths ----------------------------------------
    def test_rejects_untracked_split_backup_directory(self) -> None:
        self.write("_split-backup-20260927/index.backup")
        self.assert_rejects("recovery/backup directory inside the product: _split-backup-20260927")

    def test_rejects_worktree_recovery_directory(self) -> None:
        self.write("pCloud-worktree-recovery-20260928/notes.md")
        self.assert_rejects("recovery/backup directory inside the product: pCloud-worktree-recovery-20260928")

    def test_rejects_tracked_dated_backup_directory(self) -> None:
        self.write("deploy/00-infra/demo/docs-backup-20260101/README.md")
        self.commit()
        self.assert_rejects("tracked recovery/backup directory: deploy/00-infra/demo/docs-backup-20260101/README.md")

    def test_rejects_git_bundle_even_untracked(self) -> None:
        self.write("deploy/fix.bundle", "not really a bundle\n")
        self.assert_rejects("Git bundle inside the product: deploy/fix.bundle")

    def test_rejects_literal_tilde_directory(self) -> None:
        self.write("deploy/00-infra/demo/~/iotee-pr35/README.md")
        self.assert_rejects("literal '~' directory: deploy/00-infra/demo/~")

    # --- tracked local state / configuration --------------------------------
    def test_rejects_tracked_tfstate(self) -> None:
        self.write("deploy/00-infra/demo/terraform.tfstate", "{}\n")
        self.commit()
        self.assert_rejects("tracked local state/configuration: deploy/00-infra/demo/terraform.tfstate")

    def test_rejects_tracked_state_backup(self) -> None:
        self.write("deploy/00-infra/demo/terraform.tfstate.backup", "{}\n")
        self.commit()
        self.assert_rejects("tracked local state/configuration: deploy/00-infra/demo/terraform.tfstate.backup")

    def test_rejects_tracked_saved_plan(self) -> None:
        self.write("deploy/00-infra/demo/destroy.tfplan", "plan\n")
        self.commit()
        self.assert_rejects("tracked local state/configuration: deploy/00-infra/demo/destroy.tfplan")

    def test_rejects_tracked_tfvars(self) -> None:
        self.write("deploy/00-infra/demo/terraform.tfvars", "x = 1\n")
        self.commit()
        self.assert_rejects("tracked local state/configuration: deploy/00-infra/demo/terraform.tfvars")

    def test_rejects_tracked_real_inventory(self) -> None:
        self.write("deploy/01-engine/demo/inventory/hosts.ini", "[servers]\nnode ansible_host=10.0.0.1\n")
        self.commit()
        self.assert_rejects("tracked local state/configuration: deploy/01-engine/demo/inventory/hosts.ini")

    def test_rejects_tracked_env_file(self) -> None:
        self.write(".env", "TOKEN=x\n")
        self.commit()
        self.assert_rejects("tracked local state/configuration: .env")

    # --- governance and package entry points --------------------------------
    def test_rejects_missing_governance_pointer(self) -> None:
        git(self.repo, "rm", "-q", "CLAUDE.md")
        self.assert_rejects("governance pointer missing or untracked: CLAUDE.md")

    def test_rejects_pointer_that_does_not_reference_contract(self) -> None:
        self.write("AGENTS.md", "Own rules here, no reference.\n")
        self.assert_rejects("AGENTS.md must reference the authoritative docs/CONTRACT.md")

    def test_rejects_missing_package_test_entry(self) -> None:
        git(self.repo, "rm", "-q", "deploy/01-engine/demo/tests/verify-layer1.sh")
        self.assert_rejects("package test entry point missing: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_rejects_untracked_package_test_entry(self) -> None:
        git(self.repo, "rm", "-q", "--cached", "deploy/00-infra/demo/tests/verify.ps1")
        self.assert_rejects("package test entry point not tracked: deploy/00-infra/demo/tests/verify.ps1")

    def test_rejects_missing_package_directory(self) -> None:
        manifest = json.loads(FILES["tests/layout-manifest.json"])
        manifest["packages"].append({"path": "deploy/03-observability", "test": "tests/verify.py"})
        self.write("tests/layout-manifest.json", json.dumps(manifest))
        self.assert_rejects("package directory missing: deploy/03-observability")

    def test_rejects_package_test_not_run_by_ci(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace("      - run: bash tests/verify-layer1.sh\n", "      - run: echo skipped\n"))
        self.assert_rejects("package test not invoked by CI: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_rejects_root_check_not_run_by_ci(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace("      - run: python tests/verify-layout.py\n", ""))
        self.assert_rejects("root check not invoked by CI: tests/verify-layout.py")

    def test_rejects_broken_readme_test_reference(self) -> None:
        self.write("deploy/README.md", "| demo | `tests/verify-layer3.py` |\n")
        self.commit()
        self.assert_rejects("deploy/README.md references a test script that does not exist: tests/verify-layer3.py")

    # --- CI invocation is parsed, not substring-matched ----------------------
    ENGINE_STEP = "      - run: bash tests/verify-layer1.sh\n"

    def test_step_working_directory_override_passes(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            self.ENGINE_STEP,
            "      - run: echo job default ignored\n"
            "      - working-directory: deploy/01-engine\n"
            "        run: |\n"
            "          bash demo/tests/verify-layer1.sh \\\n"
            "            --verbose\n"))
        self.assert_passes()

    def test_rejects_command_only_in_yaml_comment(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            self.ENGINE_STEP, "      - run: echo skipped\n#      - run: bash tests/verify-layer1.sh\n"))
        self.assert_rejects("package test not invoked by CI: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_rejects_command_only_in_shell_comment(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            self.ENGINE_STEP,
            "      - run: |\n"
            "          # bash tests/verify-layer1.sh\n"
            "          echo skipped  # bash tests/verify-layer1.sh\n"))
        self.assert_rejects("package test not invoked by CI: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_rejects_command_only_in_step_name(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            self.ENGINE_STEP, "      - name: bash tests/verify-layer1.sh\n        run: echo skipped\n"))
        self.assert_rejects("package test not invoked by CI: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_rejects_package_and_test_name_in_unrelated_jobs(self) -> None:
        workflow = WORKFLOW.replace(self.ENGINE_STEP, "      - run: ls -la\n") + (
            "  other:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: ls deploy/01-engine/demo\n"
            "      - run: bash tests/verify-layer1.sh\n")
        self.write(".github/workflows/ci.yml", workflow)
        self.assert_rejects("package test not invoked by CI: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_rejects_command_from_wrong_working_directory(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            self.ENGINE_STEP,
            "      - working-directory: deploy/00-infra/demo\n"
            "        run: bash tests/verify-layer1.sh\n"))
        self.assert_rejects("package test not invoked by CI: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_rejects_command_in_disabled_job(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            "  engine:\n    runs-on: ubuntu-latest\n",
            "  engine:\n    if: false\n    runs-on: ubuntu-latest\n"))
        self.assert_rejects("package test not invoked by CI: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_rejects_test_path_as_echo_argument(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            self.ENGINE_STEP, "      - run: echo tests/verify-layer1.sh\n"))
        self.assert_rejects("package test not invoked by CI: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_rejects_test_path_as_redirection_target(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            self.ENGINE_STEP, "      - run: echo ready > tests/verify-layer1.sh\n"))
        self.assert_rejects("package test not invoked by CI: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_rejects_script_argument_to_python_inline_code(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            self.ENGINE_STEP, "      - run: python -c pass tests/verify-layer1.sh\n"))
        self.assert_rejects("package test not invoked by CI: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_rejects_bash_syntax_only_check_as_execution(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            self.ENGINE_STEP, "      - run: bash -n tests/verify-layer1.sh\n"))
        self.assert_rejects("package test not invoked by CI: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_rejects_ambiguous_shell_quoting(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            self.ENGINE_STEP, '      - run: |\n          bash "tests/verify-layer1.sh\n'))
        self.assert_rejects("package test not invoked by CI: deploy/01-engine/demo/tests/verify-layer1.sh")

    def test_powershell_file_invocation_passes(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            "      - run: ./deploy/00-infra/demo/tests/verify.ps1\n",
            "      - run: powershell -NoProfile -ExecutionPolicy Bypass -File ./deploy/00-infra/demo/tests/verify.ps1\n"))
        self.assert_passes()

    def test_chained_script_invocation_and_python_flags_pass(self) -> None:
        self.write(".github/workflows/ci.yml", WORKFLOW.replace(
            self.ENGINE_STEP, "      - run: echo ready; bash tests/verify-layer1.sh\n").replace(
            "python tests/verify-layout.py", "python3 -B tests/verify-layout.py"))
        self.assert_passes()

    # --- README references resolve exactly --------------------------------
    def test_documented_placeholder_reference_passes(self) -> None:
        self.write("deploy/01-engine/demo/README.md", "Copy it, then run `bash /path/to/demo/tests/verify-layer1.sh`.\n")
        self.commit()
        self.assert_passes()

    def test_rejects_broken_relative_readme_path(self) -> None:
        self.write("deploy/01-engine/demo/README.md", "Run `bash ../wrong/tests/verify-layer1.sh`.\n")
        self.commit()
        self.assert_rejects("deploy/01-engine/demo/README.md references a test script that does not exist: "
                            "../wrong/tests/verify-layer1.sh")

    def test_rejects_placeholder_naming_another_test(self) -> None:
        self.write("deploy/01-engine/demo/README.md", "Run `bash /path/to/demo/tests/verify-layer9.sh`.\n")
        self.commit()
        self.assert_rejects("references a test script that does not exist: /path/to/demo/tests/verify-layer9.sh")

    def test_rejects_shorthand_outside_package_context(self) -> None:
        self.write("docs/guide.md", "Run `tests/verify.ps1`.\n")
        self.commit()
        self.assert_rejects("docs/guide.md references a test script that does not exist: tests/verify.ps1")

    def test_rejects_bare_shorthand_in_root_readme(self) -> None:
        self.write("README.md", "Run `python tests/verify-layout.py`, then `tests/verify.ps1`.\n")
        self.commit()
        self.assert_rejects("README.md references a test script that does not exist: tests/verify.ps1")

    def test_rejects_broken_root_readme_path(self) -> None:
        self.write("README.md", "Run `deploy/00-infra/other/tests/verify.ps1`.\n")
        self.commit()
        self.assert_rejects("README.md references a test script that does not exist: deploy/00-infra/other/tests/verify.ps1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
