#!/usr/bin/env bash
# The one test command for this package. Offline: it contacts no node and no
# cluster, formats and mounts nothing, and writes only to a temporary folder.
#
#   bash tests/verify-local-pv.sh            # static, decision and refusal checks
#   bash tests/verify-local-pv.sh --render   # also kubectl kustomize + kubeconform -strict
#                                            # (needs both on PATH; kubeconform downloads schemas)
#
# Needs Bash and ansible-core (Linux, or WSL on Windows). Runs from any directory.
set -euo pipefail
cd "$(dirname "$0")/.."
export ANSIBLE_CONFIG="$PWD/ansible.cfg"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export LOCAL_PV_RENDER_DIR="$WORK/render" LOCAL_PV_REPORT_DIR="$WORK/report" LOCAL_PV_REQUIRE_STAMPS=false
EXAMPLE=inventory/hosts.example.yml
DO_RENDER=false
[ "${1:-}" = "--render" ] && DO_RENDER=true

fail() { echo "FAIL: $*" >&2; exit 1; }

# ---- 1. hygiene ----------------------------------------------------------------
if command -v git >/dev/null && git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
   && [ -n "$(git ls-files inventory/hosts.yml)" ]; then
  fail "the real inventory/hosts.yml must not be tracked"
fi
grep -q '^host_key_checking = True' ansible.cfg || fail "ansible.cfg must keep host-key checking on"
if grep -rEn 'host_key_checking *= *[Ff]alse|StrictHostKeyChecking[= ]no|UserKnownHostsFile=/dev/null' . \
     --include='*.yml' --include='*.cfg' --include='*.j2' --include='*.sh' --exclude=verify-local-pv.sh; then
  fail "host-key verification must never be disabled"
fi
if grep -rhoE '^\s+-? ?[a-z_]+\.[a-z_]+\.[a-z_]+:' . --include='*.yml' | grep -v 'ansible\.builtin\.'; then
  fail "non-builtin Ansible module detected"
fi
# Every mutating command lives in tasks/apply-storage.yml, and only there.
others="$(grep -lE 'mkfs|chattr|lineinfile|umount|(^|[^a-z_])mount +--' -- *.yml tasks/*.yml | grep -v '^tasks/apply-storage.yml$' || true)"
[ -z "$others" ] || fail "mutating commands outside tasks/apply-storage.yml: $others"
# wipefs is only ever run to list (--no-act). Any invocation with options that lacks
# --no-act, and any erasing option anywhere, is refused; mentions of the name are fine.
if grep -rnE 'wipefs +-' . --include='*.yml' --include='*.j2' | grep -v -- '--no-act' | grep -v '^\./tests/'; then
  fail "wipefs may only be invoked with --no-act"
fi
if grep -rnE 'wipefs[^|]* (-a|--all|-o|--offset|-f|--force)( |$)' . --include='*.yml' --include='*.j2' | grep -v '^\./tests/'; then
  fail "wipefs must never be run with an erasing option"
fi
users="$(grep -ln 'apply-storage' -- *.yml tasks/*.yml | tr '\n' ' ')"
[ "$users" = "tasks/process-disk.yml " ] || fail "apply-storage.yml must be reached only from tasks/process-disk.yml (found: $users)"

# ---- 2. syntax -----------------------------------------------------------------
bash -n tests/verify-local-pv.sh
for playbook in inspect-disks prepare-disks adopt-disks check-storage render-pvs; do
  ansible-playbook --syntax-check -i "$EXAMPLE" "$playbook.yml" >/dev/null
done
# scenarios.yml overrides YAML-merged keys on purpose, which Ansible reports as
# duplicate keys; only the two commands that load it ignore that.
SCENARIO_ENV="ANSIBLE_DUPLICATE_YAML_DICT_KEY=ignore"
env "$SCENARIO_ENV" ansible-playbook --syntax-check -i "$EXAMPLE" tests/check-logic.yml >/dev/null
echo "ok   syntax, hygiene, and mutating commands confined to tasks/apply-storage.yml"

# ---- 3. rendering and decision logic ---------------------------------------------
ansible-playbook -i "$EXAMPLE" render-pvs.yml >/dev/null
summary="$(env "$SCENARIO_ENV" ansible-playbook -i "$EXAMPLE" tests/check-logic.yml | grep -o 'local-PV logic checks passed[^"]*')" \
  || fail "tests/check-logic.yml failed (run it without redirection to see which scenario)"
echo "ok   $summary"

# ---- 4. rendering: the REAL render-pvs.yml against real stamps ------------------------
expect_fail() { # <description> <expected text> <command...>
  local description="$1" expected="$2"; shift 2
  local output
  if output="$("$@" 2>&1)"; then fail "$description: the command succeeded but must be refused"; fi
  grep -qF -- "$expected" <<<"$output" || { echo "$output" | tail -15 >&2; fail "$description: expected message '$expected'"; }
}
make_inventory() { # <name> <sed args...>: a modified copy of the example, with the group_vars beside it
  local name="$1" dir; shift
  dir="$WORK/inv-$name"; mkdir -p "$dir"; cp -r inventory/group_vars "$dir/group_vars"
  sed "$@" "$EXAMPLE" > "$dir/hosts.yml"
  echo "$dir/hosts.yml"
}
write_stamps() { # <inventory> <dir> <age in hours>: stamps built by the same templates the real check uses
  LOCAL_PV_TEST_STAMPS_DIR="$2" LOCAL_PV_TEST_STAMP_AGE_HOURS="$3" env "$SCENARIO_ENV" \
    ansible-playbook -i "$1" tests/check-logic.yml --tags stamps >/dev/null || fail "could not write test stamps"
}
render_with() { # <inventory> <stamp dir> [VAR=value ...]; stamps are required unless a VAR says otherwise
  local inventory="$1" dir="$2"; shift 2
  env LOCAL_PV_REQUIRE_STAMPS=true LOCAL_PV_REPORT_DIR="$dir" LOCAL_PV_RENDER_DIR="$WORK/render-stamped" "$@" \
    ansible-playbook -i "$inventory" render-pvs.yml
}
write_stamps "$EXAMPLE" "$WORK/stamps-fresh" 0
write_stamps "$EXAMPLE" "$WORK/stamps-old" 30
write_stamps "$EXAMPLE" "$WORK/stamps-future" -2
mkdir -p "$WORK/stamps-none"
render_with "$EXAMPLE" "$WORK/stamps-fresh" >/dev/null || fail "render with fresh, matching stamps must succeed"
expect_fail "render without stamps" "no verification stamp" render_with "$EXAMPLE" "$WORK/stamps-none"
expect_fail "render with expired stamps" "has expired" render_with "$EXAMPLE" "$WORK/stamps-old"
expect_fail "render with future-dated stamps" "dated in the future" render_with "$EXAMPLE" "$WORK/stamps-future"
cp -r "$WORK/stamps-fresh" "$WORK/stamps-failed"
sed -i 's/"passed": true/"passed": false/' "$WORK/stamps-failed/verified-rke2-worker-01-data-01.json"
expect_fail "render with a failed stamp" "records a failed verification" render_with "$EXAMPLE" "$WORK/stamps-failed"
# Any change to what the check covered invalidates the stamps made before it.
for change in \
  "mode|0,/mode: \"0750\"/s//mode: \"0700\"/" \
  "owner|0,/owner_uid: 10001/s//owner_uid: 10009/" \
  "subdir|0,/subdir: metrics/s//subdir: metrics2/" \
  "identity|s/scsi-SYNTHETIC-worker-01-data/scsi-SYNTHETIC-worker-01-other/" \
  "size|0,/size_bytes: 21474836480/s//size_bytes: 21474836481/" \
  "uuid|s/555555555551/555555555559/"; do
  inventory="$(make_inventory "${change%%|*}" -e "${change#*|}")"
  expect_fail "render after a changed ${change%%|*}" "for a different configuration" render_with "$inventory" "$WORK/stamps-fresh"
done
inventory="$(make_inventory capacity -e '0,/capacity_gib: 8/s//capacity_gib: 9/')"
render_with "$inventory" "$WORK/stamps-fresh" >/dev/null || fail "a changed capacity is not verified on the node and must not invalidate a stamp"
# A real inventory (no synthetic declaration, real-looking addresses) cannot bypass the stamps.
real="$(make_inventory real -e 's/local_pv_synthetic: true/local_pv_placeholder: 0/' -e 's/192\.0\.2\./10.20.0./' -e 's/SYNTHETIC-worker-01-data/REAL-worker-data/')"
write_stamps "$real" "$WORK/stamps-real" 0
expect_fail "a real inventory requests a bypass with no stamps" "cannot be bypassed" render_with "$real" "$WORK/stamps-none" LOCAL_PV_REQUIRE_STAMPS=false
expect_fail "a real inventory requests a bypass despite stamps" "cannot be bypassed" render_with "$real" "$WORK/stamps-real" LOCAL_PV_REQUIRE_STAMPS=false
expect_fail "a real inventory without stamps" "no verification stamp" render_with "$real" "$WORK/stamps-none"
render_with "$real" "$WORK/stamps-real" LOCAL_PV_RENDER_DIR="$WORK/render-real" >/dev/null || fail "a real inventory with fresh matching stamps must render"
[ -e "$WORK/render-real/pv-rke2-worker-01.yaml" ] || fail "a real inventory must not be named as a synthetic render"
claim="$(make_inventory claim -e 's/192\.0\.2\./10.20.0./')"
expect_fail "an inventory that claims to be synthetic but has real addresses" "may be real" render_with "$claim" "$WORK/stamps-none" LOCAL_PV_REQUIRE_STAMPS=false
echo "ok   rendering: stamps bound to the configuration and age (fresh, expired, future, failed, six kinds of change), no bypass for a real inventory"

# ---- 5. the real playbooks refuse before touching anything ---------------------------
for playbook in prepare-disks adopt-disks; do
  expect_fail "$playbook without --limit" "exactly one node" ansible-playbook -i "$EXAMPLE" "$playbook.yml"
  expect_fail "$playbook with two nodes" "exactly one node" ansible-playbook -i "$EXAMPLE" "$playbook.yml" --limit rke2-worker-01,rke2-worker-02
  expect_fail "$playbook on the synthetic example" "documentation address" ansible-playbook -i "$EXAMPLE" "$playbook.yml" --limit rke2-worker-01
done
expect_fail "check-storage on the synthetic example" "documentation address" ansible-playbook -i "$EXAMPLE" check-storage.yml
expect_fail "inspect-disks on the synthetic example" "no real address" ansible-playbook -i "$EXAMPLE" inspect-disks.yml

# A "node" that is this machine, declaring a disk that does not exist here. The
# guards run for real (read-only facts from this machine) and must refuse; the
# machine's /etc/fstab and mount root must be unchanged, in normal and check mode.
mkdir -p "$WORK/localinv"
cp -r inventory/group_vars "$WORK/localinv/group_vars"
cat > "$WORK/localinv/hosts.yml" <<'EOF'
all:
  children:
    local_pv_nodes:
      vars:
        ansible_connection: local
        ansible_become: false
      hosts:
        rke2-worker-01: &node
          ansible_host: 127.0.0.1
          local_pv:
            disks:
              - name: data-01
                size_bytes: 21474836480
                filesystem_uuid: ""
                identity: {by_id: /dev/disk/by-id/scsi-NOT-PRESENT-ON-THIS-MACHINE}
                volumes: [{name: lpv-a, subdir: a, capacity_gib: 1, owner_uid: 10001, owner_gid: 10001, mode: "0750"}]
        rke2-worker-02:
          ansible_host: 127.0.0.1
          local_pv:
            disks:
              - name: data-01
                size_bytes: 21474836480
                filesystem_uuid: "11111111-2222-4333-8444-555555555551"
                identity: {by_id: /dev/disk/by-id/scsi-NOT-PRESENT-ON-THIS-MACHINE}
                volumes: [{name: lpv-b, subdir: b, capacity_gib: 1, owner_uid: 10001, owner_gid: 10001, mode: "0750"}]
EOF
LOCAL="$WORK/localinv/hosts.yml"
fstab_before="$(sha256sum /etc/fstab | cut -d' ' -f1)"
tree_before="$(ls -d /var/lib/pcloud 2>/dev/null || echo none)"
for mode in "--check" ""; do
  expect_fail "prepare-disks $mode with an unmatched identity" "no device on this node matches the declared identity" \
    ansible-playbook -i "$LOCAL" prepare-disks.yml --limit rke2-worker-01 $mode </dev/null
done
expect_fail "adopt-disks --check with an unmatched identity" "no device on this node matches the declared identity" \
  ansible-playbook -i "$LOCAL" adopt-disks.yml --limit rke2-worker-02 --check </dev/null
expect_fail "adopt-disks with an unmatched identity" "no device on this node matches the declared identity" \
  ansible-playbook -i "$LOCAL" adopt-disks.yml --limit rke2-worker-02 </dev/null
expect_fail "check-storage with an unmatched identity" "no device on this node matches the declared identity" \
  ansible-playbook -i "$LOCAL" check-storage.yml --limit rke2-worker-02 </dev/null
# The real probe script against a real kernel (this machine, read-only). Whether every
# source is readable here depends on the machine, and failing closed is then correct, so
# only the STRUCTURE is asserted: every source and disk must be reported and the output
# must reach its end marker. The report is written before any such failure is raised.
ansible-playbook -i "$LOCAL" inspect-disks.yml --limit rke2-worker-01 -e local_pv_report_dir="$WORK/inspect" >/dev/null 2>&1 || true
report="$WORK/inspect/inspect-rke2-worker-01.json"
[ -f "$report" ] || fail "inspect-disks.yml wrote no report: the real probe script did not run"
if grep -E 'did not report|were not reported|is incomplete|no end marker' "$report"; then
  fail "the real probe output is structurally incomplete on this machine"
fi
for tool in lsblk findmnt wipefs blkid; do
  grep -q "\"$tool\": " "$report" || fail "the real probe did not report the tool $tool"
done
[ "$(grep -c '"path": "/' "$report")" -ge 6 ] || fail "the real probe did not inspect all six protected paths"
# The failure branches of the real probe script only run when something fails, so make
# one tool fail (a stand-in that exits 2, first in PATH, for one run) and require the
# report to say so instead of showing an empty, safe-looking result.
shim_probe() { # <tool> <phrase the report must contain>
  local tool="$1" expected="$2" dir="$WORK/shim-$1" out="$WORK/inspect-shim-$1"
  mkdir -p "$dir"; printf '#!/bin/sh\nexit 2\n' > "$dir/$tool"; chmod +x "$dir/$tool"
  PATH="$dir:$PATH" ansible-playbook -i "$LOCAL" inspect-disks.yml --limit rke2-worker-01 \
    -e local_pv_report_dir="$out" >/dev/null 2>&1 || true
  [ -f "$out/inspect-rke2-worker-01.json" ] || fail "with $tool failing, the real probe wrote no report"
  grep -qF -- "$expected" "$out/inspect-rke2-worker-01.json" \
    || fail "with $tool failing, the real probe did not report '$expected' (an unreadable source must not look empty)"
}
shim_probe wipefs "signature probe failed"
shim_probe ls "holders could not be read"
shim_probe grep "the fstab probe failed"
shim_probe findmnt "findmnt failed"
if [ -d /dev/disk/by-id ]; then shim_probe find "the links probe failed"; fi
# "Absent" must mean the parent is searchable and the path is not there. With dirname failing,
# the parent of a missing protected path cannot be confirmed, so it must be an error, not "absent".
if [ ! -e /var/lib/rancher ] || [ ! -e /var/lib/kubelet ] || [ ! -e /var/lib/containerd ] || [ ! -e /boot/efi ]; then
  shim_probe dirname "parent directory is not searchable"
fi
[ "$(sha256sum /etc/fstab | cut -d' ' -f1)" = "$fstab_before" ] || fail "/etc/fstab changed during the refusal tests"
[ "$(ls -d /var/lib/pcloud 2>/dev/null || echo none)" = "$tree_before" ] || fail "/var/lib/pcloud was created during the refusal tests"
echo "ok   playbooks refuse before any change (no --limit, several nodes, synthetic values, unmatched identity); the real probe reports a failing wipefs, ls, grep, findmnt or find instead of an empty result"

# ---- 6. optional: render and schema-check the Kubernetes objects -----------------------
render_check() { # explicit checks: set -e does not apply inside an `if`
  local rendered f
  rendered="$(kubectl kustomize kubernetes)" || return 1
  printf '%s\n' "$rendered" | kubeconform -kubernetes-version 1.35.0 -strict -summary - || return 1
  for f in "$LOCAL_PV_RENDER_DIR"/pv-*.yaml; do
    kubeconform -kubernetes-version 1.35.0 -strict -summary "$f" || return 1
  done
}
fake_case() { # <kubectl exit> <kubeconform exit> <expect kubeconform to run: yes|no> <expect success: yes|no>
  local d ran=no rc=0
  d="$(mktemp -d -p "$WORK")"
  printf '#!/bin/sh\necho "apiVersion: v1"\nexit %s\n' "$1" > "$d/kubectl"
  printf '#!/bin/sh\ntouch "%s/ran"\nexit %s\n' "$d" "$2" > "$d/kubeconform"
  chmod +x "$d/kubectl" "$d/kubeconform"
  (PATH="$d:$PATH"; render_check) >/dev/null 2>&1 || rc=$?
  [ -e "$d/ran" ] && ran=yes
  [ "$ran" = "$3" ] || fail "fake tools (kubectl $1, kubeconform $2): kubeconform ran=$ran, expected $3"
  if [ "$4" = yes ] && [ "$rc" -ne 0 ]; then fail "fake tools that succeed must pass the render check"; fi
  if [ "$4" = no ] && [ "$rc" -eq 0 ]; then fail "fake tools (kubectl $1, kubeconform $2) must fail the render check"; fi
}
fake_case 0 0 yes yes
fake_case 1 0 no  no
fake_case 0 1 yes no
echo "ok   render check fails when kubectl or kubeconform fails (controlled fake tools)"
if $DO_RENDER; then
  command -v kubectl >/dev/null && command -v kubeconform >/dev/null || fail "--render needs kubectl and kubeconform on PATH"
  render_check || fail "kubectl kustomize / kubeconform rejected the Kubernetes objects"
  echo "ok   StorageClass and rendered PersistentVolumes pass kubeconform -strict"
fi
echo "Local PV offline checks passed."
