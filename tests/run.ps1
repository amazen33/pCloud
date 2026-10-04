#Requires -Version 5.1
<#
.SYNOPSIS
  pCloud test dispatcher. Runs the existing repository and package checks by
  mode; it adds no test logic of its own. Guide: tests/README.md.

.DESCRIPTION
  Modes (default Static):
    Static  No access to live infrastructure. -Extended adds checks that may
            download pinned public artifacts (OpenTofu providers, Kubernetes
            schemas), still without contacting any managed host or cluster.
    Live    Read-only checks against an existing environment, using each
            package's git-ignored inputs.
    Smoke   Checks that change isolated cluster resources or telemetry. They
            run only with -AllowClusterChanges.
    All     Static (with -Extended), Live and Smoke. Smoke still needs
            -AllowClusterChanges; without it nothing is changed and the run
            is incomplete.

  Each check ends PASS, FAIL, SKIP (with the reason) or NOT IMPLEMENTED.
  Exit code: 0 PASS; 1 FAIL (a check failed); 2 usage or configuration
  error; 3 INCOMPLETE (a required check was skipped or is not implemented).

  Results (one log per check and summary.json) go to a new folder under the
  system temporary directory, or to -ResultsDir, which must be outside the
  repository. Historical evidence in evidence/ folders is never written.
  Nothing here provisions, repairs or removes infrastructure.

  Layer 1 (Bash, Ansible) runs natively on Linux; on Windows it runs only
  through WSL (wsl.exe) with ansible-core installed inside the distribution.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tests/run.ps1
  pwsh -NoProfile -File tests/run.ps1 -Mode Static -Extended -Package deploy/02-cluster-addons
  pwsh -NoProfile -File tests/run.ps1 -Mode Live -Package deploy/01-k8s-engine/rke2-ansible
  pwsh -NoProfile -File tests/run.ps1 -List
#>
[CmdletBinding()]
param(
    [string] $Mode = 'Static',
    [string[]] $Package = @(),
    [string[]] $Check = @(),
    [switch] $Extended,
    [switch] $AllowClusterChanges,
    [switch] $List,
    [switch] $Json,
    [string] $ResultsDir = '',
    # Test hooks for tests/run.Tests.ps1: another repository root, manifest or catalog (.psd1).
    [string] $RepoRoot = '',
    [string] $ManifestPath = '',
    [string] $CatalogPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$env:GIT_OPTIONAL_LOCKS = '0'
$onWindows = $env:OS -eq 'Windows_NT'

# The check catalog. Script and Args are relative to the package directory
# ('root' is the repository root), exactly as a copied package runs them.
# Entry marks the one Static command that tests/layout-manifest.json declares
# for a package (its `test`) or as a root check; CI calls those same entry
# points directly. Planned entries report NOT IMPLEMENTED with their reason.
# PlannedPackage marks checks of a package that does not exist yet: they stay
# visible, but are required only when -Package or -Check names them.
# A Live check is registered only after all of its tasks were confirmed
# read-only on the hosts it contacts.
$BuiltInCatalog = @(
    @{ Id = 'root-layout'; Package = 'root'; Mode = 'Static'; Entry = $true; Runner = 'python'
        Script = 'tests/verify-layout.py'; Needs = @('python', 'pyyaml', 'git')
        Description = 'Repository layout and governance invariants' }
    @{ Id = 'root-layout-regressions'; Package = 'root'; Mode = 'Static'; Entry = $true; Runner = 'python'
        Script = 'tests/test_verify_layout.py'; Needs = @('python', 'pyyaml', 'git')
        Description = 'Each layout rejection, in throwaway repositories' }

    @{ Id = 'l0-static'; Package = 'deploy/00-infra/private-hyperv'; Mode = 'Static'; Entry = $true; Runner = 'powershell'
        Script = 'tests/verify.ps1'
        Description = 'Layer 0: scripts parse, capacity rules, self-containment, helper unit tests' }
    @{ Id = 'l0-static-tofu'; Package = 'deploy/00-infra/private-hyperv'; Mode = 'Static'; Extended = $true; Runner = 'powershell'
        Script = 'tests/verify.ps1'; Args = @('-RunTofu'); Needs = @('tofu')
        Description = 'Layer 0 plus tofu fmt, locked init and validate in a temporary copy (downloads pinned providers)' }
    @{ Id = 'l0-live-check'; Package = 'deploy/00-infra/private-hyperv'; Mode = 'Live'
        Planned = 'scripts/check-layer0.ps1 is read-only on the host but writes out/ and may create .terraform inside the package; it needs a report-location option first'
        Description = 'Layer 0: VM state, checkpoints, disks, heartbeat, SSH port, free space' }

    @{ Id = 'l1-static'; Package = 'deploy/01-k8s-engine/rke2-ansible'; Mode = 'Static'; Entry = $true; Runner = 'bash'
        Script = 'tests/verify-layer1.sh'; Needs = @('linux-bash', 'linux-ansible')
        Description = 'Layer 1: playbook syntax, inventory rules, negative inventories, defaults, builtin modules only' }
    @{ Id = 'l1-live-inventory'; Package = 'deploy/01-k8s-engine/rke2-ansible'; Mode = 'Live'; Runner = 'ansible'
        Script = 'tests/validate-inventory-live.yml'; Args = @('-i', 'inventory/hosts.ini')
        Needs = @('linux-ansible', 'file:inventory/hosts.ini')
        Description = 'Layer 1: the real inventory against the live-run rules (contacts no host)' }
    @{ Id = 'l1-live-guard-cni'; Package = 'deploy/01-k8s-engine/rke2-ansible'; Mode = 'Live'; Runner = 'ansible'
        Script = 'guard-cni.yml'; Args = @('-i', 'inventory/hosts.ini')
        Needs = @('linux-ansible', 'file:inventory/hosts.ini')
        Description = 'Layer 1: installed CNI and ingress match the reviewed choice (assert, stat, read-only commands)' }
    @{ Id = 'l1-live-health'; Package = 'deploy/01-k8s-engine/rke2-ansible'; Mode = 'Live'; Runner = 'ansible'
        Script = 'health.yml'; Args = @('-i', 'inventory/hosts.ini', '-e', 'report_dir={results}')
        Needs = @('linux-ansible', 'file:inventory/hosts.ini')
        Description = 'Layer 1: node services, Ready, disk and memory, API readiness; report written to the results folder' }
    @{ Id = 'l1-smoke-psa-probe'; Package = 'deploy/01-k8s-engine/rke2-ansible'; Mode = 'Smoke'
        Planned = 'the privileged-pod rejection is a manual README procedure; no script exists'
        Description = 'Layer 1: restricted Pod Security rejects a privileged pod' }

    @{ Id = 'l2-static'; Package = 'deploy/02-cluster-addons'; Mode = 'Static'; Entry = $true; Runner = 'python'
        Script = 'tests/verify-layer2.py'; Needs = @('python', 'pyyaml')
        Description = 'Layer 2: local resources, pinned upstream bytes and images, services-only mode, pool, smoke manifest' }
    @{ Id = 'l2-static-render'; Package = 'deploy/02-cluster-addons'; Mode = 'Static'; Extended = $true; Runner = 'python'
        Script = 'tests/verify-layer2.py'; Args = @('--render'); Needs = @('python', 'pyyaml', 'kubectl', 'kubeconform')
        Description = 'Layer 2 plus kubectl kustomize and kubeconform -strict (downloads schemas)' }
    @{ Id = 'l2-live-preflight'; Package = 'deploy/02-cluster-addons'; Mode = 'Live'
        Planned = 'the read-only kubectl preflight is a manual README procedure; no script exists'
        Description = 'Layer 2: nodes, controllers, services and server-side dry run match the review' }
    @{ Id = 'l2-smoke-loadbalancer'; Package = 'deploy/02-cluster-addons'; Mode = 'Smoke'
        Planned = 'tests/smoke.yaml is applied, checked and removed manually; no script exists'
        Description = 'Layer 2: a LoadBalancer Service receives a pool address and answers layer2-ok' }

    # Storage phase. prepare-disks.yml and adopt-disks.yml change nodes (they format
    # and mount) and are deliberately NOT registered here: only a person runs them.
    @{ Id = 's-static'; Package = 'deploy/02-storage/local-pv'; Mode = 'Static'; Entry = $true; Runner = 'bash'
        Script = 'tests/verify-local-pv.sh'; Needs = @('linux-bash', 'linux-ansible')
        Description = 'Local PV storage: decisions, refusals, rendered volumes, mutating commands confined to one file' }
    @{ Id = 's-live-inspect'; Package = 'deploy/02-storage/local-pv'; Mode = 'Live'; Runner = 'ansible'
        Script = 'inspect-disks.yml'; Args = @('-i', 'inventory/hosts.yml', '-e', 'local_pv_report_dir={results}')
        Needs = @('linux-ansible', 'file:inventory/hosts.yml')
        Description = 'Local PV storage: read the disks of the nodes (read-only on nodes); report written to the results folder' }
    @{ Id = 's-live-check'; Package = 'deploy/02-storage/local-pv'; Mode = 'Live'; Runner = 'ansible'
        Script = 'check-storage.yml'; Args = @('-i', 'inventory/hosts.yml', '-e', 'local_pv_report_dir={results}')
        Needs = @('linux-ansible', 'file:inventory/hosts.yml')
        Description = 'Local PV storage: data mounts, filesystem UUIDs, markers, volume directories and free space (read-only on nodes; fails closed)' }
    @{ Id = 's-smoke-bind'; Package = 'deploy/02-storage/local-pv'; Mode = 'Smoke'
        Planned = 'binding a temporary claim, restarting its pod and reading the data back on a disposable disk is not implemented'
        Description = 'Local PV storage: a claim binds on the right node, data survives a pod restart, an unmounted disk fails closed' }

    @{ Id = 'storage-static'; Package = 'deploy/02-cluster-addons/storage/local-path'; Mode = 'Static'; Entry = $true; Runner = 'python'
        Script = 'tests/verify-storage.py'; Needs = @('python', 'pyyaml', 'jsonschema')
        Description = 'Storage: schema, lifecycle/path/security guards, source/image locks and copied-package independence' }
    @{ Id = 'storage-static-render'; Package = 'deploy/02-cluster-addons/storage/local-path'; Mode = 'Static'; Extended = $true; Runner = 'python'
        Script = 'tests/verify-storage.py'; Args = @('--render'); Needs = @('python', 'pyyaml', 'jsonschema', 'kubectl', 'kubeconform')
        Description = 'Storage: real local render, generated helper and restricted smoke Kubernetes schemas' }
    @{ Id = 'storage-live'; Package = 'deploy/02-cluster-addons/storage/local-path'; Mode = 'Live'; Runner = 'python'
        Script = 'storage.py'; Args = @('live', '--site', 'site.json', '--output', '{results}/storage-live.json')
        Needs = @('python', 'pyyaml', 'jsonschema', 'kubectl', 'file:site.json')
        Description = 'Storage: read-only explicit-context API checks and trusted SSH mount/capacity inspection' }
    @{ Id = 'storage-smoke'; Package = 'deploy/02-cluster-addons/storage/local-path'; Mode = 'Smoke'; Runner = 'python'
        Script = 'storage.py'; Args = @('smoke', '--site', 'site.json', '--review', 'review.json', '--allow-cluster-changes', '--allow-controller-restart', '--output', '{results}/storage-smoke.json')
        Needs = @('python', 'pyyaml', 'jsonschema', 'kubectl', 'file:site.json', 'file:review.json')
        Description = 'Storage: isolated run-owned persistence, Retain/rebind, affinity, controller stop/restore and helper capture; operator review required' }

    @{ Id = 'secrets-static'; Package = 'deploy/02-cluster-addons/secrets/openbao'; Mode = 'Static'; Entry = $true; Runner = 'python'
        Script = 'tests/verify-secrets.py'; Needs = @('python', 'pyyaml', 'jsonschema')
        Description = 'Secrets: schema, source/image locks, credential/ownership guards, simulated lifecycle and ephemeral local TLS' }
    @{ Id = 'secrets-static-render'; Package = 'deploy/02-cluster-addons/secrets/openbao'; Mode = 'Static'; Extended = $true; Runner = 'python'
        Script = 'tests/verify-secrets.py'; Args = @('--render'); Needs = @('python', 'pyyaml', 'jsonschema', 'kubectl', 'kubeconform')
        Description = 'Secrets: real Kustomize and Kubernetes 1.35.0 strict schemas for installed platform and recovery profiles' }
    @{ Id = 'secrets-live'; Package = 'deploy/02-cluster-addons/secrets/openbao'; Mode = 'Live'; Runner = 'python'
        Script = 'bao.py'; Args = @('live', '--site', 'site.json', '--output', '{results}/secrets-live.json')
        Needs = @('python', 'pyyaml', 'jsonschema', 'kubectl', 'file:site.json', 'env:PCLOUD_BAO_TOKEN')
        Description = 'Secrets: read-only explicit-context installation, active TLS endpoint, KV v2, Kubernetes auth and HMAC audit checks' }
    @{ Id = 'secrets-smoke'; Package = 'deploy/02-cluster-addons/secrets/openbao'; Mode = 'Smoke'; Runner = 'python'
        Script = 'bao.py'; Args = @('smoke', '--site', 'site.json', '--review', 'review.json', '--allow-cluster-changes', '--output', '{results}/secrets-smoke.json')
        Needs = @('python', 'pyyaml', 'jsonschema', 'kubectl', 'file:site.json', 'file:review.json', 'env:PCLOUD_BAO_TOKEN')
        Description = 'Secrets: run-owned KV rotation, scoped JWT/token denial and audit checks; remains INCOMPLETE until operator restart/restore evidence' }

    @{ Id = 'backend-static'; Package = 'deploy/02-cluster-addons/storage/observability-filesystem'; Mode = 'Static'; Entry = $true; Runner = 'python'
        Script = 'tests/verify-backend.py'; Needs = @('python', 'pyyaml', 'jsonschema')
        Description = 'Backend: schemas, supported fragments/locks, copied package and simulated lifecycle; Linux additionally runs real POSIX fixtures' }
    @{ Id = 'backend-static-render'; Package = 'deploy/02-cluster-addons/storage/observability-filesystem'; Mode = 'Static'; Extended = $true; Runner = 'python'
        Script = 'tests/verify-backend.py'; Args = @('--render'); Needs = @('python', 'pyyaml', 'jsonschema', 'kubectl', 'kubeconform')
        Description = 'Backend: real Kustomize/strict Kubernetes 1.35.0 schemas for claims and every restricted probe action; Windows reports POSIX skips' }
    @{ Id = 'backend-live'; Package = 'deploy/02-cluster-addons/storage/observability-filesystem'; Mode = 'Live'; Runner = 'python'
        Script = 'backend.py'; Args = @('live', '--site', 'site.json', '--output', '{results}/backend-live.json')
        Needs = @('python', 'pyyaml', 'jsonschema', 'kubectl', 'file:site.json')
        Description = 'Backend: read-only class, worker, namespace/claim/volume identity and affinity checks; INCOMPLETE until POSIX/M4 acceptance' }
    @{ Id = 'backend-smoke'; Package = 'deploy/02-cluster-addons/storage/observability-filesystem'; Mode = 'Smoke'; Runner = 'python'
        Script = 'backend.py'; Args = @('smoke', '--site', 'site.json', '--review', 'review.json', '--allow-cluster-changes', '--output', '{results}/backend-smoke.json')
        Needs = @('python', 'pyyaml', 'jsonschema', 'kubectl', 'file:site.json', 'file:review.json')
        Description = 'Backend: isolated POSIX write/remount/UID denial, scoped cleanup and retained test PV inventory; operator disposition and M4 still required' }

    @{ Id = 'l3-static-observability'; Package = 'deploy/03-observability'; Mode = 'Static'; Entry = $true; Runner = 'python'
        Script = 'tests/verify-observability.py'; Needs = @('python', 'pyyaml', 'jsonschema')
        Description = 'Layer 3: schemas, runtime contracts, access routes, copied package and simulated signal/graph/alert acceptance' }
    @{ Id = 'l3-static-render'; Package = 'deploy/03-observability'; Mode = 'Static'; Extended = $true; Runner = 'python'
        Script = 'tests/verify-observability.py'; Args = @('--render'); Needs = @('python', 'pyyaml', 'jsonschema', 'kubectl', 'kubeconform')
        Description = 'Layer 3: real Kustomize and strict Kubernetes 1.35.0 schemas for all lab runtime resources' }
    @{ Id = 'l3-live-observability'; Package = 'deploy/03-observability'; Mode = 'Live'; Runner = 'python'
        Script = 'observability.py'; Args = @('live', '--site', 'site.json', '--backend', 'backend-capability.json', '--fragments', 'backend-fragments.json', '--output', '{results}/observability-live.json')
        Needs = @('python', 'pyyaml', 'jsonschema', 'kubectl', 'file:site.json', 'file:backend-capability.json', 'file:backend-fragments.json', 'env:PCLOUD_OBSERVE_INGEST', 'env:PCLOUD_OBSERVE_QUERY', 'env:PCLOUD_OBSERVE_ADMIN')
        Description = 'Layer 3: read-only runtime drift/health, trusted TLS and query role denials; full acceptance remains INCOMPLETE' }
    @{ Id = 'l3-smoke-observability'; Package = 'deploy/03-observability'; Mode = 'Smoke'; Runner = 'python'
        Script = 'observability.py'; Args = @('smoke', '--site', 'site.json', '--backend', 'backend-capability.json', '--fragments', 'backend-fragments.json', '--review', 'review.json', '--allow-cluster-changes', '--output', '{results}/observability-smoke.json')
        Needs = @('python', 'pyyaml', 'jsonschema', 'kubectl', 'file:site.json', 'file:backend-capability.json', 'file:backend-fragments.json', 'file:review.json', 'env:PCLOUD_OBSERVE_INGEST', 'env:PCLOUD_OBSERVE_QUERY', 'env:PCLOUD_OBSERVE_ADMIN')
        Description = 'Layer 3: isolated reviewed test-stack OTLP ingest/query, correlation, service graph and firing alert; receiver/retention/recovery/soak still required' }

)

function Exit-Usage([string] $Message) {
    if ($Json) { [Console]::Error.WriteLine("USAGE ERROR: $Message") } else { Write-Host "USAGE ERROR: $Message" }
    exit 2
}

function Get-Value($Item, [string] $Key, $Default) {
    if ($Item.ContainsKey($Key) -and $null -ne $Item[$Key]) { return $Item[$Key] }
    return $Default
}

function ConvertTo-PackageName([string] $Name) {
    $name = ($Name -replace '\\', '/').Trim()
    while ($name.StartsWith('./')) { $name = $name.Substring(2) }
    $name = $name.TrimEnd('/')
    if ($name -eq '' -or $name -eq '.') { return 'root' }
    return $name
}

function Split-List([string[]] $Values) {
    return @($Values | ForEach-Object { "$_" -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Resolve-FullPath([string] $Path) {
    return [System.IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)).TrimEnd('\', '/')
}

# A stderr line arrives as an ErrorRecord; its message is the line itself
# (an empty line would otherwise print as the exception type name).
function ConvertTo-Line($Item) {
    if ($Item -is [System.Management.Automation.ErrorRecord]) { return $Item.Exception.Message }
    return "$Item"
}

# Runs a native command with its output captured; stderr never becomes a
# terminating error (Windows PowerShell 5.1 would otherwise stop on it).
function Invoke-Native([string] $Exe, [string[]] $Arguments) {
    $ErrorActionPreference = 'Continue'
    $output = @(& $Exe @Arguments 2>&1 | ForEach-Object { ConvertTo-Line $_ })
    return [pscustomobject]@{ Code = $LASTEXITCODE; Output = $output }
}

# Program checks must use CreateProcess, never Windows' application-chooser
# fallback for invalid executables. Both output streams drain concurrently.
function Invoke-Program([string] $Exe, [string[]] $Arguments) {
    if ($onWindows) {
        # Reject plain text disguised as .exe before Windows error handling
        # can invoke an interactive file handler. Program means a PE binary;
        # PowerShell and Bash scripts use their dedicated catalog runners.
        $stream = [System.IO.File]::OpenRead($Exe)
        $reader = New-Object System.IO.BinaryReader($stream)
        try {
            if ($stream.Length -lt 64 -or $reader.ReadUInt16() -ne 0x5A4D) { throw 'invalid native executable header' }
            $stream.Position = 0x3C
            $offset = $reader.ReadInt32()
            if ($offset -lt 64 -or $offset -gt $stream.Length - 4) { throw 'invalid native executable header' }
            $stream.Position = $offset
            if ($reader.ReadUInt32() -ne 0x4550) { throw 'invalid native executable header' }
        }
        finally { $reader.Dispose(); $stream.Dispose() }
    }
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $Exe
    $start.WorkingDirectory = (Get-Location).Path
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    if ($start.PSObject.Properties['ArgumentList']) {
        foreach ($word in $Arguments) { $start.ArgumentList.Add($word) }
    }
    else {
        # Windows CommandLineToArgvW rules for .NET Framework / PowerShell 5.1.
        $start.Arguments = (@($Arguments | ForEach-Object {
            if ($_ -ne '' -and $_ -notmatch '[\s"]') { $_ }
            else { '"' + ([regex]::Replace([regex]::Replace($_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1')) + '"' }
        }) -join ' ')
    }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    try {
        [void] $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $lines = @(($stdout.Result + "`n" + $stderr.Result) -split '\r?\n' | Where-Object { $_ -ne '' })
        return [pscustomobject]@{ Code = $process.ExitCode; Output = $lines }
    }
    finally { $process.Dispose() }
}

# --- repository, manifest and catalog ---------------------------------------------------
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent $PSScriptRoot }
$RepoRoot = Resolve-FullPath $RepoRoot
if (-not $ManifestPath) { $ManifestPath = Join-Path (Join-Path $RepoRoot 'tests') 'layout-manifest.json' }

$modes = @('Static', 'Live', 'Smoke')
if (@($modes + 'All') -notcontains $Mode) { Exit-Usage "-Mode must be Static, Live, Smoke or All (got '$Mode')." }
$Mode = @($modes + 'All') | Where-Object { $_ -eq $Mode } | Select-Object -First 1

if ($CatalogPath) {
    try { $catalog = @((Import-PowerShellDataFile -LiteralPath $CatalogPath).Checks) }
    catch { Exit-Usage "cannot read catalog ${CatalogPath}: $($_.Exception.Message)" }
}
else { $catalog = $BuiltInCatalog }

$ids = @{}
foreach ($c in $catalog) {
    foreach ($key in 'Id', 'Package', 'Mode') {
        if (-not (Get-Value $c $key '')) { Exit-Usage "catalog entry without $key." }
    }
    $c.Package = ConvertTo-PackageName $c.Package
    if ($ids.ContainsKey($c.Id)) { Exit-Usage "duplicate catalog id $($c.Id)." }
    $ids[$c.Id] = $true
    if ($modes -notcontains $c.Mode) { Exit-Usage "catalog entry $($c.Id) has unknown mode $($c.Mode)." }
    if (-not (Get-Value $c 'Planned' '')) {
        if (@('powershell', 'python', 'bash', 'ansible', 'program') -notcontains (Get-Value $c 'Runner' '') -or -not (Get-Value $c 'Script' '')) {
            Exit-Usage "catalog entry $($c.Id) needs a Runner (powershell, python, bash, ansible, program) and a Script, or a Planned reason."
        }
    }
}

# The catalog must dispatch to exactly the entry points the manifest declares.
try { $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json }
catch { Exit-Usage "cannot read manifest ${ManifestPath}: $($_.Exception.Message)" }
$declared = @{}
foreach ($rootCheck in @($manifest.root_checks)) { $declared["root|$rootCheck"] = $true }
foreach ($pkg in @($manifest.packages)) { $declared["$(ConvertTo-PackageName $pkg.path)|$($pkg.test)"] = $true }
$matched = @{}
foreach ($c in @($catalog | Where-Object { Get-Value $_ 'Entry' $false })) {
    $key = "$($c.Package)|$(Get-Value $c 'Script' '')"
    if (-not $declared.ContainsKey($key)) {
        Exit-Usage "catalog entry $($c.Id) ($key) is not a root check or package test in $ManifestPath."
    }
    if ($c.Mode -ne 'Static' -or (Get-Value $c 'Extended' $false) -or @(Get-Value $c 'Args' @()).Count -gt 0) {
        Exit-Usage "catalog entry $($c.Id) must run the declared entry point unchanged in Static mode."
    }
    $matched[$key] = $true
}
foreach ($key in $declared.Keys) {
    if (-not $matched.ContainsKey($key)) { Exit-Usage "$key from $ManifestPath has no Static entry in the dispatcher catalog." }
}

# --- selection ----------------------------------------------------------------------------------------
$packageFilter = @(Split-List $Package | ForEach-Object { ConvertTo-PackageName $_ })
$checkFilter = @(Split-List $Check)
$knownPackages = @($catalog | ForEach-Object { $_.Package } | Sort-Object -Unique)
foreach ($name in $packageFilter) {
    if ($knownPackages -notcontains $name) { Exit-Usage "unknown package '$name'. Known: $($knownPackages -join ', ')." }
}
foreach ($id in $checkFilter) {
    if (-not $ids.ContainsKey($id)) { Exit-Usage "unknown check '$id'. Use -List to see the catalog." }
}

$selectedModes = if ($Mode -eq 'All') { $modes } else { @($Mode) }
$extendedOn = [bool]$Extended -or $Mode -eq 'All'
$selected = @($catalog | Where-Object {
        ($checkFilter.Count -gt 0 -and $checkFilter -contains $_.Id) -or
        ($checkFilter.Count -eq 0 -and $selectedModes -contains $_.Mode)
    } | Where-Object { $packageFilter.Count -eq 0 -or $packageFilter -contains $_.Package })
# Unknown names were rejected above; a valid selection without checks is
# reported as INCOMPLETE below, never as PASS.

function Test-Required($C) {
    $named = ($checkFilter -contains $C.Id) -or ($packageFilter -contains $C.Package)
    if ((Get-Value $C 'PlannedPackage' $false) -and -not $named) { return $false }
    return (-not (Get-Value $C 'Extended' $false)) -or $extendedOn -or ($checkFilter -contains $C.Id)
}

function Get-Display($C) {
    if (Get-Value $C 'Planned' '') { return 'not implemented' }
    $words = @(Get-Value $C 'Args' @())
    switch ($C.Runner) {
        'powershell' { $text = "PowerShell -File $($C.Script) $($words -join ' ')" }
        'python' { $text = "python $($C.Script) $($words -join ' ')" }
        'bash' { $text = "bash $($C.Script) $($words -join ' ')" }
        'ansible' { $text = "ansible-playbook $($words -join ' ') $($C.Script)" }
        'program' { $text = "$($C.Script) $($words -join ' ')" }
    }
    if ($onWindows -and @('bash', 'ansible') -contains $C.Runner) { $text = "wsl.exe: $text" }
    return $text.Trim()
}

if ($List) {
    $plan = @($selected | ForEach-Object {
            [pscustomobject]@{
                Id = $_.Id; Package = $_.Package; Mode = $_.Mode
                Extended = [bool](Get-Value $_ 'Extended' $false); Required = [bool](Test-Required $_)
                Planned = Get-Value $_ 'Planned' $null; Runner = Get-Value $_ 'Runner' $null
                Script = Get-Value $_ 'Script' $null; Args = @(Get-Value $_ 'Args' @())
                Needs = @(Get-Value $_ 'Needs' @()); Execution = Get-Display $_
                Description = Get-Value $_ 'Description' ''
            }
        })
    if ($Json) { Write-Output (ConvertTo-Json -InputObject $plan -Depth 4) }
    else {
        if ($plan.Count -eq 0) { Write-Host 'No checks match this selection.' }
        foreach ($p in $plan) {
            $flag = if ($p.Planned) { 'planned' } elseif (-not $p.Required) { 'optional' } else { 'required' }
            Write-Host ('{0,-24} {1,-6} {2,-8} {3,-32} {4}' -f $p.Id, $p.Mode, $flag, $p.Package, $p.Execution)
        }
    }
    exit 0
}

# --- results folder (outside the repository) ----------------------------------------------------------------
if (-not $ResultsDir) {
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $ResultsDir = Join-Path (Join-Path ([System.IO.Path]::GetTempPath()) 'pcloud-test-results') "$stamp-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
}
$ResultsDir = Resolve-FullPath $ResultsDir
$comparison = if ($onWindows) { [System.StringComparison]::OrdinalIgnoreCase } else { [System.StringComparison]::Ordinal }
$separator = [System.IO.Path]::DirectorySeparatorChar
if ($ResultsDir.Equals($RepoRoot, $comparison) -or $ResultsDir.StartsWith($RepoRoot + $separator, $comparison)) {
    Exit-Usage "-ResultsDir must be outside the repository ($RepoRoot); results are not product source or evidence."
}
New-Item -ItemType Directory -Force -Path $ResultsDir | Out-Null

# --- prerequisites ---------------------------------------------------------------------------------------------
$probeCache = @{}
function Find-Python {
    if (-not $probeCache.ContainsKey('python')) {
        $probeCache['python'] = $null
        foreach ($name in 'python', 'python3') {
            $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($cmd -and (Invoke-Native $cmd.Source @('-c', 'import sys')).Code -eq 0) { $probeCache['python'] = $cmd.Source; break }
        }
    }
    return $probeCache['python']
}

function Test-WslCommand([string] $Probe) {
    if (-not $probeCache.ContainsKey("wsl|$Probe")) {
        $ok = $false
        if (Get-Command wsl.exe -CommandType Application -ErrorAction SilentlyContinue) {
            $ok = (Invoke-Native 'wsl.exe' @('-e', 'bash', '-lc', $Probe)).Code -eq 0
        }
        $probeCache["wsl|$Probe"] = $ok
    }
    return $probeCache["wsl|$Probe"]
}

function Get-MissingNeed($C, [string] $Cwd) {
    foreach ($need in @(Get-Value $C 'Needs' @())) {
        switch -Wildcard ($need) {
            'python' { if (-not (Find-Python)) { return 'prerequisite missing: Python 3 (python or python3)' } }
            'pyyaml' {
                if (-not $probeCache.ContainsKey('pyyaml')) { $probeCache['pyyaml'] = (Find-Python) -and (Invoke-Native (Find-Python) @('-c', 'import yaml')).Code -eq 0 }
                if (-not $probeCache['pyyaml']) { return 'prerequisite missing: PyYAML (python -m pip install PyYAML==6.0.3)' }
            }
            'jsonschema' {
                if (-not $probeCache.ContainsKey('jsonschema')) { $probeCache['jsonschema'] = (Find-Python) -and (Invoke-Native (Find-Python) @('-c', 'import jsonschema')).Code -eq 0 }
                if (-not $probeCache['jsonschema']) { return 'prerequisite missing: jsonschema (python -m pip install jsonschema==4.25.1)' }
            }
            'linux-bash' {
                if ($onWindows) { if (-not (Test-WslCommand 'true')) { return 'prerequisite missing: Bash checks run on Linux or in WSL; wsl.exe with a Linux distribution was not found' } }
                elseif (-not (Get-Command bash -CommandType Application -ErrorAction SilentlyContinue)) { return 'prerequisite missing: bash' }
            }
            'linux-ansible' {
                if ($onWindows) { if (-not (Test-WslCommand 'command -v ansible-playbook')) { return 'prerequisite missing: Ansible runs on Linux or in WSL; ansible-playbook was not found in the WSL distribution' } }
                elseif (-not (Get-Command ansible-playbook -CommandType Application -ErrorAction SilentlyContinue)) { return 'prerequisite missing: ansible-playbook (python -m pip install ansible-core)' }
            }
            'file:*' {
                $relative = $need.Substring(5)
                if (-not (Test-Path -LiteralPath (Join-Path $Cwd $relative))) { return "input missing: $relative (git-ignored; prepare it as the package README describes)" }
            }
            'env:*' {
                if (-not [Environment]::GetEnvironmentVariable($need.Substring(4))) { return "input missing: $($need.Substring(4)) (operator environment; never a command argument)" }
            }
            default { if (-not (Get-Command $need -CommandType Application -ErrorAction SilentlyContinue)) { return "prerequisite missing: $need" } }
        }
    }
    return $null
}

function ConvertTo-ShellWord([string] $Word) { return "'" + ($Word -replace "'", "'\''") + "'" }

function ConvertTo-WslPath([string] $Path) {
    $result = Invoke-Native 'wsl.exe' @('-e', 'wslpath', '-a', $Path)
    if ($result.Code -ne 0 -or -not $result.Output) { throw "wslpath could not translate $Path" }
    return ($result.Output -join '').Trim()
}

# Returns the executable and arguments; bash and Ansible go through WSL on Windows.
function Get-CheckCommand($C, [string] $Cwd, [string] $CheckResults) {
    $useWsl = $onWindows -and @('bash', 'ansible') -contains $C.Runner
    $resultsArg = if ($useWsl) { ConvertTo-WslPath $CheckResults } else { $CheckResults }
    $words = @(@(Get-Value $C 'Args' @()) | ForEach-Object { $_.Replace('{results}', $resultsArg) })
    switch ($C.Runner) {
        'powershell' {
            $argv = @('-NoProfile')
            if ($onWindows) { $argv += @('-ExecutionPolicy', 'Bypass') }
            return @{ Exe = (Get-Process -Id $PID).Path; Argv = $argv + @('-File', (Join-Path $Cwd $C.Script)) + $words }
        }
        'python' { return @{ Exe = (Find-Python); Argv = @((Join-Path $Cwd $C.Script)) + $words } }
        'program' { return @{ Exe = (Join-Path $Cwd $C.Script); Argv = $words } }
    }
    if ($useWsl) {
        $wslCwd = ConvertTo-WslPath $Cwd
        $quoted = ($words | ForEach-Object { ConvertTo-ShellWord $_ }) -join ' '
        if ($C.Runner -eq 'bash') { $line = "bash $(ConvertTo-ShellWord $C.Script) $quoted" }
        else { $line = "ANSIBLE_CONFIG=$(ConvertTo-ShellWord ($wslCwd + '/ansible.cfg')) ansible-playbook $quoted $(ConvertTo-ShellWord $C.Script)" }
        return @{ Exe = 'wsl.exe'; Argv = @('-e', 'bash', '-lc', "cd $(ConvertTo-ShellWord $wslCwd) && $line") }
    }
    if ($C.Runner -eq 'bash') { return @{ Exe = 'bash'; Argv = @($C.Script) + $words } }
    return @{ Exe = 'ansible-playbook'; Argv = $words + @($C.Script); AnsibleConfig = (Join-Path $Cwd 'ansible.cfg') }
}

# --- run ---------------------------------------------------------------------------------------------------------
$revision = $null; $clean = $null
if (Get-Command git -CommandType Application -ErrorAction SilentlyContinue) {
    $head = Invoke-Native 'git' @('-C', $RepoRoot, 'rev-parse', 'HEAD')
    if ($head.Code -eq 0) {
        $revision = ($head.Output -join '').Trim()
        $clean = @((Invoke-Native 'git' @('-C', $RepoRoot, 'status', '--porcelain')).Output | Where-Object { $_ }).Count -eq 0
    }
}
Write-Host "pCloud checks: mode $Mode$(if ($extendedOn) { ' (extended)' }), $($selected.Count) selected, revision $revision$(if ($clean -eq $false) { ' (uncommitted changes)' })"
$smokeBlocked = -not $AllowClusterChanges -and @($selected | Where-Object { $_.Mode -eq 'Smoke' }).Count -gt 0

$records = New-Object System.Collections.Generic.List[object]
foreach ($c in $selected) {
    $required = [bool](Test-Required $c)
    $cwd = if ($c.Package -eq 'root') { $RepoRoot } else { Join-Path $RepoRoot $c.Package }
    $record = [ordered]@{ id = $c.Id; package = $c.Package; mode = $c.Mode; required = $required
        status = ''; reason = ''; exitCode = $null; seconds = 0; log = $null; execution = (Get-Display $c) }
    Write-Host ''
    Write-Host "== $($c.Id) [$($c.Mode)] $($c.Package)"
    if ($c.Mode -eq 'Smoke' -and -not $AllowClusterChanges) {
        $record.status = 'SKIP'; $record.reason = 'consent missing: Smoke changes cluster resources or telemetry; add -AllowClusterChanges'
    }
    elseif (Get-Value $c 'Planned' '') {
        $record.status = 'NOT IMPLEMENTED'; $record.reason = $c.Planned
    }
    elseif ((Get-Value $c 'Extended' $false) -and -not $required) {
        $record.status = 'SKIP'; $record.reason = 'extended check; add -Extended to run it'
    }
    else {
        $missing = Get-MissingNeed $c $cwd
        if ($missing) { $record.status = 'SKIP'; $record.reason = $missing }
        else {
            $checkResults = Join-Path $ResultsDir $c.Id
            New-Item -ItemType Directory -Force -Path $checkResults | Out-Null
            $log = Join-Path $ResultsDir "$($c.Id).log"
            $record.log = $log
            Write-Host "   $($record.execution)"
            $lines = New-Object System.Collections.Generic.List[string]
            $timer = [System.Diagnostics.Stopwatch]::StartNew()
            $savedConfig = $env:ANSIBLE_CONFIG
            Push-Location -LiteralPath $cwd
            try {
                $command = Get-CheckCommand $c $cwd $checkResults
                if ($command.ContainsKey('AnsibleConfig')) { $env:ANSIBLE_CONFIG = $command.AnsibleConfig }
                $program = Get-Command -Name $command.Exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
                if (-not $program) { throw "program not found: $($command.Exe)" }
                $ErrorActionPreference = 'Continue'
                $argv = @($command.Argv)
                # A launch failure leaves no exit code; never read one left by an earlier check.
                $global:LASTEXITCODE = $null
                if ($c.Runner -eq 'program') {
                    $executed = Invoke-Program $program.Source $argv
                    foreach ($line in $executed.Output) { $lines.Add($line); Write-Host "   | $line" }
                    $exitCode = $executed.Code
                }
                else {
                    & $program.Source @argv 2>&1 | ForEach-Object { $line = ConvertTo-Line $_; $lines.Add($line); Write-Host "   | $line" }
                    $exitCode = $global:LASTEXITCODE
                }
                if ($null -eq $exitCode) { throw "$($command.Exe) did not run to an exit code" }
                $record.exitCode = $exitCode
                $record.status = if ($exitCode -eq 0) { 'PASS' } else { 'FAIL' }
                if ($exitCode -ne 0) { $record.reason = "exit code $exitCode" }
            }
            catch { $record.status = 'FAIL'; $record.reason = "could not start: $($_.Exception.Message)"; $lines.Add($record.reason) }
            finally {
                $ErrorActionPreference = 'Stop'
                $env:ANSIBLE_CONFIG = $savedConfig
                Pop-Location
                $timer.Stop()
            }
            $record.seconds = [math]::Round($timer.Elapsed.TotalSeconds, 1)
            [System.IO.File]::WriteAllLines($log, [string[]]$lines, (New-Object System.Text.UTF8Encoding $false))
        }
    }
    Write-Host ("   {0} {1}{2}" -f $record.status, $c.Id, $(if ($record.reason) { " - $($record.reason)" } else { '' }))
    $records.Add([pscustomobject]$record)
}

# --- verdict -----------------------------------------------------------------------------------------------------
$failed = @($records | Where-Object { $_.status -eq 'FAIL' })
$notRun = @($records | Where-Object { $_.required -and @('SKIP', 'NOT IMPLEMENTED') -contains $_.status })
$executed = @($records | Where-Object { @('PASS', 'FAIL') -contains $_.status })
if ($failed.Count -gt 0) { $verdict = 'FAIL'; $code = 1 }
elseif ($notRun.Count -gt 0 -or $executed.Count -eq 0) { $verdict = 'INCOMPLETE'; $code = 3 }
else { $verdict = 'PASS'; $code = 0 }

$count = { param($s) @($records | Where-Object { $_.status -eq $s }).Count }
Write-Host ''
Write-Host "Summary (mode $Mode):"
foreach ($r in $records) {
    $note = if ($r.reason) { "  $($r.reason)" } else { '' }
    $optional = if ($r.required) { '' } else { ' (optional)' }
    Write-Host ('  {0,-16} {1}{2}{3}' -f $r.status, $r.id, $optional, $note)
}
if ($records.Count -eq 0) { Write-Host 'No checks match this selection; nothing was run.' }
elseif ($executed.Count -eq 0) { Write-Host 'No check was executed.' }
if ($smokeBlocked) { Write-Host 'Smoke checks were not run: they change cluster resources or telemetry and need -AllowClusterChanges. Nothing was changed.' }
Write-Host ("Result: {0} ({1} passed, {2} failed, {3} skipped, {4} not implemented; {5} required check(s) did not run)" -f `
        $verdict, (& $count 'PASS'), (& $count 'FAIL'), (& $count 'SKIP'), (& $count 'NOT IMPLEMENTED'), $notRun.Count)

# ToArray(): Windows PowerShell 5.1 cannot put @(List[object]) into a hashtable literal.
$checks = $records.ToArray()
$summary = [ordered]@{
    mode = $Mode; extended = $extendedOn; allowClusterChanges = [bool]$AllowClusterChanges
    finishedUtc = (Get-Date).ToUniversalTime().ToString('o'); revision = $revision; workingTreeClean = $clean
    host = if ($onWindows) { 'windows' } else { 'linux-or-macos' }; powershell = "$($PSVersionTable.PSVersion)"
    verdict = $verdict; exitCode = $code; checks = $checks
}
$summaryPath = Join-Path $ResultsDir 'summary.json'
[System.IO.File]::WriteAllText($summaryPath, (ConvertTo-Json -InputObject $summary -Depth 5), (New-Object System.Text.UTF8Encoding $false))
Write-Host "Results: $ResultsDir"
exit $code
