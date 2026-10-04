#!/usr/bin/env bash
# Static Linux CI dependencies. No kubeconfig or cluster operations.
set -euo pipefail
task_tools="$(mktemp -d)"
trap 'rm -rf -- "$task_tools"' EXIT
cd "$task_tools"
curl --fail --silent --show-error --location -o kubectl https://dl.k8s.io/release/v1.35.0/bin/linux/amd64/kubectl
curl --fail --silent --show-error --location -o kubectl.sha256 https://dl.k8s.io/release/v1.35.0/bin/linux/amd64/kubectl.sha256
printf '%s  kubectl\n' "$(cat kubectl.sha256)" | sha256sum --check -
curl --fail --silent --show-error --location -o kubeconform-linux-amd64.tar.gz https://github.com/yannh/kubeconform/releases/download/v0.7.0/kubeconform-linux-amd64.tar.gz
curl --fail --silent --show-error --location -o CHECKSUMS https://github.com/yannh/kubeconform/releases/download/v0.7.0/CHECKSUMS
awk '$2 == "kubeconform-linux-amd64.tar.gz" { print }' CHECKSUMS > selected-checksum
test -s selected-checksum
sha256sum --check selected-checksum
tar xzf kubeconform-linux-amd64.tar.gz kubeconform
chmod 0755 kubectl kubeconform
mkdir -p "$HOME/.local/bin"
mv kubectl kubeconform "$HOME/.local/bin/"
if test -n "${GITHUB_PATH:-}"; then printf '%s\n' "$HOME/.local/bin" >> "$GITHUB_PATH"; fi
