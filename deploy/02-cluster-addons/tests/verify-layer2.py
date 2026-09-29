#!/usr/bin/env python3
"""Offline Layer 2 check; run from any directory, including a copied package.

    python tests/verify-layer2.py            # static checks, no cluster, no network
    python tests/verify-layer2.py --render   # also kubectl kustomize + kubeconform -strict
                                              # (needs both on PATH; kubeconform downloads schemas)

The static run also proves, with controlled fake kubectl and kubeconform in a
temporary folder, that a failed render or validation fails the --render check.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_SHA256 = "fac6c080d0916b8e7692a6c94a80fd1d7fbda07db8dc12dc187fd984d3c6ca9d"
# Schemas for the lab's Kubernetes minor (RKE2 v1.35.x).
KUBERNETES_VERSION = "1.35.0"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def local_resources(kustomization: Path) -> list[Path]:
    document = yaml.safe_load(kustomization.read_text(encoding="utf-8"))
    require(document.get("kind") == "Kustomization", f"not a Kustomization: {kustomization}")
    files: list[Path] = []
    for name in document.get("resources", []):
        require("://" not in name, f"remote Kustomize resource: {name}")
        target = (kustomization.parent / name).resolve()
        require(target.is_relative_to(ROOT), f"resource escapes Layer 2: {name}")
        require(target.exists(), f"missing resource: {target}")
        if target.is_dir():
            files.extend(local_resources(target / "kustomization.yaml"))
        else:
            files.append(target)
    return files


def main() -> None:
    files = local_resources(ROOT / "kustomization.yaml")
    require(len(files) == 4, f"expected four local manifest files; found {len(files)}")
    vendored = ROOT / "kube-vip" / "kube-vip-cloud-controller.yaml"
    require(hashlib.sha256(vendored.read_bytes()).hexdigest() == UPSTREAM_SHA256,
            "vendored cloud-controller manifest differs from pinned upstream bytes")

    resources = {}
    for path in files:
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if doc is None:
                continue
            metadata = doc.get("metadata", {})
            key = (doc.get("kind"), metadata.get("namespace", ""), metadata.get("name"))
            require(key not in resources, f"duplicate resource: {key}")
            resources[key] = doc

    provider = resources[("Deployment", "kube-system", "kube-vip-cloud-provider")]
    provider_image = provider["spec"]["template"]["spec"]["containers"][0]["image"]
    require(provider_image == "ghcr.io/kube-vip/kube-vip-cloud-provider:v0.0.12",
            "cloud-controller image pin changed without review")
    daemon = resources[("DaemonSet", "kube-system", "kube-vip-ds")]
    pod = daemon["spec"]["template"]["spec"]
    require(pod["hostNetwork"] is True, "ARP daemon needs host networking")
    container = pod["containers"][0]
    require(container["image"] == "ghcr.io/kube-vip/kube-vip:v1.2.4",
            "kube-vip image pin changed without review")
    env = {entry["name"]: entry["value"] for entry in container["env"]}
    require(env.get("cp_enable") == "false" and env.get("svc_enable") == "true",
            "Layer 2 must manage service VIPs only, never the API VIP")
    require(env.get("vip_arp") == "true" and env.get("vip_interface") == "eth0",
            "review the ARP mode and lab interface before changing them")

    pool = resources[("ConfigMap", "kube-system", "kubevip")]["data"]["range-global"]
    start, end = map(ipaddress.ip_address, pool.split("-", maxsplit=1))
    require(start.version == end.version == 4 and start <= end, "invalid IPv4 LoadBalancer pool")
    require(start in ipaddress.ip_network("10.20.0.0/24")
            and end in ipaddress.ip_network("10.20.0.0/24"),
            "lab LoadBalancer pool must remain within its documented network")
    smoke = [doc for doc in yaml.safe_load_all(
        (ROOT / "tests" / "smoke.yaml").read_text(encoding="utf-8")) if doc]
    require({doc["kind"] for doc in smoke} == {"Namespace", "Deployment", "Service"},
            "smoke test must be an isolated namespace, workload and LoadBalancer Service")
    smoke_namespace = next(doc for doc in smoke if doc["kind"] == "Namespace")
    require(smoke_namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted",
            "smoke workload must run under restricted Pod Security")
    smoke_pod = next(doc for doc in smoke if doc["kind"] == "Deployment")["spec"]["template"]["spec"]
    require(not smoke_pod.get("hostNetwork", False), "smoke workload must not use host networking")
    for smoke_container in smoke_pod["containers"]:
        security = smoke_container.get("securityContext", {})
        require(security.get("runAsNonRoot") is True
                and security.get("allowPrivilegeEscalation") is False
                and security.get("capabilities", {}).get("drop") == ["ALL"]
                and not security.get("capabilities", {}).get("add")
                and security.get("seccompProfile", {}).get("type") == "RuntimeDefault",
                f"smoke container {smoke_container['name']} must meet restricted Pod Security")
        require(smoke_container["image"].split(":")[-1] not in ("", "latest")
                and ":" in smoke_container["image"], "smoke image must be pinned to a tag")
    smoke_service = next(doc for doc in smoke if doc["kind"] == "Service")
    require(smoke_service["spec"]["type"] == "LoadBalancer", "smoke must exercise kube-vip")
    print(f"Layer 2 offline checks passed: {len(files)} local files, {len(resources)} resources.")


def render_and_validate(search_path: str | None = None, report: bool = True) -> None:
    """Render the package with Kustomize and validate it and the smoke manifest.

    search_path replaces PATH when looking up kubectl and kubeconform (tests only).
    """
    tools = {name: shutil.which(name, path=search_path) for name in ("kubectl", "kubeconform")}
    for name, path in tools.items():
        require(path is not None, f"--render needs {name} on PATH")
    rendered = subprocess.run([tools["kubectl"], "kustomize", str(ROOT)],
                              capture_output=True, text=True)
    require(rendered.returncode == 0, f"kubectl kustomize failed:\n{rendered.stderr}")
    validate = [tools["kubeconform"], "-kubernetes-version", KUBERNETES_VERSION, "-strict", "-summary"]
    for label, arguments, stdin in (
        ("rendered package", ["-"], rendered.stdout),
        ("smoke manifest", [str(ROOT / "tests" / "smoke.yaml")], None),
    ):
        result = subprocess.run(validate + arguments, input=stdin, capture_output=True, text=True)
        if report:
            print(result.stdout.strip())
        require(result.returncode == 0, f"kubeconform rejected the {label}:\n{result.stdout}{result.stderr}")
    if report:
        print(f"Layer 2 render checks passed: Kustomize render and smoke manifest valid for Kubernetes {KUBERNETES_VERSION}.")


def write_fake_tool(folder: Path, name: str, exit_code: int, output: list[str], marker: Path | None) -> None:
    """A stand-in for a real tool: prints fixed lines, optionally records that it ran."""
    if os.name == "nt":
        lines = ["@echo off", *(f"echo {line}" for line in output)]
        if marker is not None:
            lines.append(f'type nul > "{marker}"')
        lines.append(f"exit /b {exit_code}")
        (folder / f"{name}.cmd").write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    else:
        lines = ["#!/bin/sh", *(f"echo '{line}'" for line in output)]
        if marker is not None:
            lines.append(f": > '{marker}'")
        lines.append(f"exit {exit_code}")
        tool = folder / name
        tool.write_text("\n".join(lines) + "\n", encoding="ascii")
        tool.chmod(0o755)


def check_render_failure_handling() -> None:
    """--render must fail when kubectl or kubeconform fails, using controlled fakes."""
    cases = (
        # kubectl exit, kubeconform exit, expected failure, kubeconform must have run
        (1, 0, "kubectl kustomize failed", False),
        (0, 1, "kubeconform rejected the rendered package", True),
        (0, 0, None, True),
    )
    for kubectl_exit, kubeconform_exit, expected, validator_runs in cases:
        with tempfile.TemporaryDirectory(prefix="layer2-fake-tools-") as name:
            folder = Path(name)
            marker = folder / "kubeconform-ran"
            write_fake_tool(folder, "kubectl", kubectl_exit,
                            ["apiVersion: v1", "kind: Namespace", "metadata: {name: fake}"], None)
            write_fake_tool(folder, "kubeconform", kubeconform_exit, [], marker)
            try:
                render_and_validate(search_path=str(folder), report=False)
                failure = None
            except AssertionError as exc:
                failure = str(exc)
            if expected is None:
                require(failure is None, f"fake tools that succeed must pass --render: {failure}")
            else:
                require(failure is not None and expected in failure,
                        f"kubectl exit {kubectl_exit} / kubeconform exit {kubeconform_exit} must fail with "
                        f"'{expected}'; got {failure!r}")
            require(marker.exists() == validator_runs,
                    f"kubeconform {'must' if validator_runs else 'must not'} run when kubectl exits {kubectl_exit}")
    print(f"Layer 2 render failure handling verified with fake tools: {len(cases)} cases.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Offline Layer 2 package check.")
    parser.add_argument("--render", action="store_true",
                        help="also render with kubectl kustomize and validate with kubeconform -strict")
    options = parser.parse_args()
    main()
    check_render_failure_handling()
    if options.render:
        render_and_validate()
