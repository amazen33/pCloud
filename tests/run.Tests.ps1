#Requires -Version 5.1
<#
.SYNOPSIS
  Tests for tests/run.ps1: selection, exit codes and safety. Plain PowerShell
  (no Pester); runs on Windows PowerShell 5.1 and PowerShell 7 on any OS.

    powershell -NoProfile -ExecutionPolicy Bypass -File tests/run.Tests.ps1   # Windows
    pwsh -NoProfile -File tests/run.Tests.ps1                                  # Linux/macOS

  Runs the dispatcher as a child process against a throwaway repository of
  stub checks under the system temporary directory; the real catalog is only
  listed (-List), never executed. Touches no host or cluster.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'run.ps1'
$realRoot = Split-Path -Parent $PSScriptRoot
$onWindows = $env:OS -eq 'Windows_NT'
$hostExe = (Get-Process -Id $PID).Path
$hostArgs = @('-NoProfile')
if ($onWindows) { $hostArgs += @('-ExecutionPolicy', 'Bypass') }

$script:failed = 0
$script:passed = 0
function Assert([bool] $Condition, [string] $Name) {
    if ($Condition) { $script:passed++; Write-Host "ok   $Name" }
    else { $script:failed++; Write-Host "FAIL $Name" -ForegroundColor Red }
}

function Invoke-Runner([string[]] $Arguments) {
    $ErrorActionPreference = 'Continue'
    $argv = $hostArgs + @('-File', $runner) + $Arguments
    $output = @(& $hostExe @argv 2>&1 | ForEach-Object { "$_" })
    return [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($output -join "`n") }
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) ('pcloud-run-tests-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $work 'repo'
$markers = Join-Path $work 'markers'
$results = Join-Path $work 'results'
function Remove-TestFolder([string] $Path, [string] $Parent) {
    $target = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $boundary = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $target.StartsWith($boundary, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Test cleanup target escapes its temporary parent' }
    if (Test-Path -LiteralPath $target) { Remove-Item -Recurse -Force -LiteralPath $target }
}
try {
    foreach ($dir in @($markers, $results, (Join-Path $repo 'tests'), (Join-Path $repo 'pkg/a/tests'), (Join-Path $repo 'pkg/b/tests'))) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    # Stub checks record that they ran, then exit with a fixed code.
    $stubs = @{ 'tests/root-ok.ps1' = 0; 'pkg/a/tests/a.ps1' = 0; 'pkg/a/tests/a-ext.ps1' = 0; 'pkg/a/tests/a-live.ps1' = 0
        'pkg/a/tests/a-needs.ps1' = 0; 'pkg/a/tests/a-smoke.ps1' = 0; 'pkg/b/tests/b.ps1' = 0; 'pkg/b/tests/b-live.ps1' = 1 }
    foreach ($stub in $stubs.Keys) {
        $name = [System.IO.Path]::GetFileNameWithoutExtension($stub)
        $marker = Join-Path $markers $name
        Set-Content -LiteralPath (Join-Path $repo $stub) -Encoding ascii -Value @(
            "New-Item -ItemType File -Force -Path '$marker' | Out-Null"
            "Write-Host 'stub $name ran'"
            "exit $($stubs[$stub])")
    }
    # A file that exists as an executable but cannot be started (launch failure).
    $broken = if ($onWindows) { 'tests/broken.exe' } else { 'tests/broken' }
    Set-Content -LiteralPath (Join-Path $repo "pkg/b/$broken") -Encoding ascii -Value 'not a program'
    if (-not $onWindows) { & chmod +x (Join-Path $repo "pkg/b/$broken") }
    $validProgram = if ($onWindows) { 'tests/valid program.exe' } else { 'tests/valid program' }
    if ($onWindows) {
        Copy-Item -LiteralPath (Join-Path $env:SystemRoot 'System32/findstr.exe') -Destination (Join-Path $repo "pkg/b/$validProgram")
        Set-Content -LiteralPath (Join-Path $repo 'pkg/b/data with spaces.txt') -Encoding ascii -Value 'space value'
        $validArgs = "@('/L', '/C:space value', 'data with spaces.txt')"
    }
    else {
        Set-Content -LiteralPath (Join-Path $repo "pkg/b/$validProgram") -Encoding ascii -Value "#!/bin/sh`nprintf '%s\n' `"`$1`"`n"
        & chmod +x (Join-Path $repo "pkg/b/$validProgram")
        $validArgs = "@('space value')"
    }
    Set-Content -LiteralPath (Join-Path $repo 'tests/layout-manifest.json') -Encoding ascii -Value `
        '{"root_checks": ["tests/root-ok.ps1"], "packages": [{"path": "pkg/a", "test": "tests/a.ps1"}, {"path": "pkg/b", "test": "tests/b.ps1"}]}'
    $catalog = Join-Path $work 'catalog.psd1'
    Set-Content -LiteralPath $catalog -Encoding ascii -Value @'
@{ Checks = @(
    @{ Id = 'root-ok'; Package = 'root'; Mode = 'Static'; Entry = $true; Runner = 'powershell'; Script = 'tests/root-ok.ps1' }
    @{ Id = 'a-static'; Package = 'pkg/a'; Mode = 'Static'; Entry = $true; Runner = 'powershell'; Script = 'tests/a.ps1' }
    @{ Id = 'a-extended'; Package = 'pkg/a'; Mode = 'Static'; Extended = $true; Runner = 'powershell'; Script = 'tests/a-ext.ps1'; Needs = @('pcloud-missing-tool-7f3a') }
    @{ Id = 'a-live'; Package = 'pkg/a'; Mode = 'Live'; Runner = 'powershell'; Script = 'tests/a-live.ps1' }
    @{ Id = 'a-live-needs-file'; Package = 'pkg/a'; Mode = 'Live'; Runner = 'powershell'; Script = 'tests/a-needs.ps1'; Needs = @('file:inventory/missing.ini') }
    @{ Id = 'a-live-needs-env'; Package = 'pkg/a'; Mode = 'Live'; Runner = 'powershell'; Script = 'tests/a-needs.ps1'; Needs = @('env:PCLOUD_TEST_TOKEN_8C902F') }
    @{ Id = 'a-smoke'; Package = 'pkg/a'; Mode = 'Smoke'; Runner = 'powershell'; Script = 'tests/a-smoke.ps1' }
    @{ Id = 'b-static'; Package = 'pkg/b'; Mode = 'Static'; Entry = $true; Runner = 'powershell'; Script = 'tests/b.ps1' }
    @{ Id = 'b-live-fail'; Package = 'pkg/b'; Mode = 'Live'; Runner = 'powershell'; Script = 'tests/b-live.ps1' }
    @{ Id = 'b-live-planned'; Package = 'pkg/b'; Mode = 'Live'; Planned = 'not written yet' }
    @{ Id = 'b-live-broken'; Package = 'pkg/b'; Mode = 'Live'; Runner = 'program'; Script = 'BROKEN' }
    @{ Id = 'b-live-missing-program'; Package = 'pkg/b'; Mode = 'Live'; Runner = 'program'; Script = 'tests/no-such-program' }
    @{ Id = 'b-live-valid-program'; Package = 'pkg/b'; Mode = 'Live'; Runner = 'program'; Script = 'VALIDPROGRAM'; Args = VALIDARGS }
    @{ Id = 'c-static'; Package = 'pkg/c'; Mode = 'Static'; PlannedPackage = $true; Planned = 'package not created yet' }
    @{ Id = 'c-live'; Package = 'pkg/c'; Mode = 'Live'; PlannedPackage = $true; Planned = 'package not created yet' }
) }
'@.Replace('BROKEN', $broken).Replace('VALIDPROGRAM', $validProgram).Replace('VALIDARGS', $validArgs)
    # Only a planned-package check is available in Live mode here.
    $onlyPlanned = Join-Path $work 'only-planned.psd1'
    Set-Content -LiteralPath $onlyPlanned -Encoding ascii -Value @'
@{ Checks = @(
    @{ Id = 'root-ok'; Package = 'root'; Mode = 'Static'; Entry = $true; Runner = 'powershell'; Script = 'tests/root-ok.ps1' }
    @{ Id = 'a-static'; Package = 'pkg/a'; Mode = 'Static'; Entry = $true; Runner = 'powershell'; Script = 'tests/a.ps1' }
    @{ Id = 'b-static'; Package = 'pkg/b'; Mode = 'Static'; Entry = $true; Runner = 'powershell'; Script = 'tests/b.ps1' }
    @{ Id = 'c-live'; Package = 'pkg/c'; Mode = 'Live'; PlannedPackage = $true; Planned = 'package not created yet' }
) }
'@
    $base = @('-RepoRoot', $repo, '-CatalogPath', $catalog)
    $script:run = 0
    function Invoke-Stub([string[]] $Arguments) {
        Get-ChildItem -LiteralPath $markers | Remove-Item -Force
        $script:run++
        return Invoke-Runner ($base + @('-ResultsDir', (Join-Path $results "run$script:run")) + $Arguments)
    }
    function Get-Ran { return , @(Get-ChildItem -LiteralPath $markers | ForEach-Object { $_.Name } | Sort-Object) }

    # --- selection ------------------------------------------------------------------------------
    $r = Invoke-Stub @()
    Assert ($r.Code -eq 0) 'default: Static checks pass, exit 0'
    Assert ((Get-Ran) -join ',' -eq 'a,b,root-ok') 'default: only Static stubs ran (no Live, Smoke or unselected extended)'
    Assert ($r.Text -match 'SKIP\s+a-extended \(optional\)\s+extended check') 'default: extended check reported SKIP as optional'
    Assert ($r.Text -match 'NOT IMPLEMENTED\s+c-static \(optional\)') 'default: planned-package check stays visible but optional'

    $r = Invoke-Stub @('-Package', 'pkg/b')
    Assert ($r.Code -eq 0 -and (Get-Ran) -join ',' -eq 'b') '-Package selects exactly that package'
    $r = Invoke-Stub @('-Check', 'a-live')
    Assert ($r.Code -eq 0 -and (Get-Ran) -join ',' -eq 'a-live') '-Check selects exactly that check'
    Assert ((Invoke-Stub @('-Package', 'pkg/zzz')).Code -eq 2) 'unknown package: exit 2'
    Assert ((Invoke-Stub @('-Check', 'no-such-check')).Code -eq 2) 'unknown check: exit 2'
    Assert ((Invoke-Stub @('-Mode', 'Everything')).Code -eq 2) 'unknown mode: exit 2'

    $r = Invoke-Stub @('-List', '-Mode', 'All')
    Assert ($r.Code -eq 0 -and (Get-Ran).Count -eq 0) '-List runs nothing and exits 0'
    Assert (-not (Test-Path -LiteralPath (Join-Path $results "run$script:run"))) '-List creates no results folder'

    # --- planned packages and empty selections ----------------------------------------------------
    $r = Invoke-Stub @('-Package', 'pkg/c')
    Assert ($r.Code -eq 3 -and $r.Text -match 'NOT IMPLEMENTED\s+c-static\s' -and $r.Text -notmatch 'c-static \(optional\)') 'planned package named by -Package: required, INCOMPLETE, exit 3'
    $r = Invoke-Stub @('-Check', 'c-live')
    Assert ($r.Code -eq 3 -and $r.Text -match 'Result: INCOMPLETE') 'planned-package check named by -Check: INCOMPLETE, exit 3'
    $r = Invoke-Stub @('-Mode', 'Smoke', '-Package', 'pkg/b')
    Assert ($r.Code -eq 3 -and $r.Text -match 'No checks match this selection' -and $r.Text -match 'Result: INCOMPLETE') 'valid package and mode without checks: INCOMPLETE, exit 3 (not a usage error)'
    $r = Invoke-Stub @('-List', '-Mode', 'Smoke', '-Package', 'pkg/b')
    Assert ($r.Code -eq 0 -and $r.Text -match 'No checks match this selection') '-List of an empty selection says so and exits 0'
    $r = Invoke-Runner @('-RepoRoot', $repo, '-CatalogPath', $onlyPlanned, '-Mode', 'Live', '-ResultsDir', (Join-Path $results 'only-planned'))
    Assert ($r.Code -eq 3 -and $r.Text -match 'No check was executed' -and $r.Text -notmatch 'Result: PASS') 'only optional checks, none executed: INCOMPLETE, never PASS'

    # --- launch failures ---------------------------------------------------------------------------------
    $r = Invoke-Stub @('-Check', 'b-static,b-live-broken')
    $summary = Get-Content -LiteralPath (Join-Path $results "run$script:run/summary.json") -Raw | ConvertFrom-Json
    $launch = @($summary.checks | Where-Object { $_.id -eq 'b-live-broken' })
    Assert ($r.Code -eq 1 -and $launch.Count -eq 1 -and $launch[0].status -eq 'FAIL' -and $launch[0].reason -like 'could not start:*' -and $null -eq $launch[0].exitCode) 'executable that cannot start after a passing check: FAIL, no stale exit code'
    $r = Invoke-Stub @('-Check', 'b-static,b-live-missing-program')
    Assert ($r.Code -eq 1 -and $r.Text -match 'FAIL\s+b-live-missing-program\s+could not start: program not found') 'missing program: FAIL, exit 1'
    $r = Invoke-Stub @('-Check', 'b-live-valid-program')
    Assert ($r.Code -eq 0 -and $r.Text -match 'space value') 'native program with spaces in path and arguments: executes without shell fallback'

    # --- statuses and exit codes -------------------------------------------------------------------
    $r = Invoke-Stub @('-Mode', 'Live', '-Package', 'pkg/b')
    Assert ($r.Code -eq 1 -and $r.Text -match 'Result: FAIL') 'a failing check: exit 1, even with a NOT IMPLEMENTED check selected'
    $r = Invoke-Stub @('-Check', 'b-live-planned,a-live')
    Assert ($r.Code -eq 3 -and $r.Text -match 'NOT IMPLEMENTED\s+b-live-planned' -and $r.Text -match 'Result: INCOMPLETE') 'required NOT IMPLEMENTED with others passing: INCOMPLETE, exit 3'
    $r = Invoke-Stub @('-Check', 'a-live-needs-file')
    Assert ($r.Code -eq 3 -and (Get-Ran).Count -eq 0 -and $r.Text -match 'input missing: inventory/missing.ini') 'missing required input: SKIP with reason, exit 3, not run'
    $oldTestToken = [Environment]::GetEnvironmentVariable('PCLOUD_TEST_TOKEN_8C902F')
    try {
        [Environment]::SetEnvironmentVariable('PCLOUD_TEST_TOKEN_8C902F', $null)
        $r = Invoke-Stub @('-Check', 'a-live-needs-env')
        Assert ($r.Code -eq 3 -and (Get-Ran).Count -eq 0 -and $r.Text -match 'input missing: PCLOUD_TEST_TOKEN_8C902F') 'missing environment credential: INCOMPLETE and no execution'
        [Environment]::SetEnvironmentVariable('PCLOUD_TEST_TOKEN_8C902F', 'synthetic-do-not-log-8c902f')
        $r = Invoke-Stub @('-Check', 'a-live-needs-env')
        Assert ($r.Code -eq 0 -and $r.Text -notmatch 'synthetic-do-not-log-8c902f') 'environment prerequisite passes without logging its value'
    }
    finally { [Environment]::SetEnvironmentVariable('PCLOUD_TEST_TOKEN_8C902F', $oldTestToken) }
    $r = Invoke-Stub @('-Extended')
    Assert ($r.Code -eq 3 -and $r.Text -match 'prerequisite missing: pcloud-missing-tool-7f3a') '-Extended makes a missing extended tool INCOMPLETE, exit 3'

    # --- consent for cluster changes ---------------------------------------------------------------
    $r = Invoke-Stub @('-Mode', 'Smoke')
    Assert ($r.Code -eq 3 -and (Get-Ran).Count -eq 0 -and $r.Text -match 'consent missing') 'Smoke without consent: nothing runs, consent reported, exit 3'
    $r = Invoke-Stub @('-Mode', 'All', '-Package', 'pkg/a')
    $ran = Get-Ran
    Assert ($r.Code -eq 3 -and $ran -contains 'a-live' -and $ran -notcontains 'a-smoke' -and $r.Text -match 'need -AllowClusterChanges') 'All without consent: Static/Live run, Smoke does not, exit 3'
    $r = Invoke-Stub @('-Mode', 'Smoke', '-AllowClusterChanges')
    Assert ($r.Code -eq 0 -and (Get-Ran) -join ',' -eq 'a-smoke') 'Smoke with -AllowClusterChanges runs the smoke check'

    # --- results location ------------------------------------------------------------------------------
    $r = Invoke-Runner ($base + @('-ResultsDir', (Join-Path $repo 'results')))
    Assert ($r.Code -eq 2 -and -not (Test-Path -LiteralPath (Join-Path $repo 'results'))) '-ResultsDir inside the repository: exit 2, nothing written'
    $r = Invoke-Runner $base
    $default = if ($r.Text -match 'Results: (.+)') { $Matches[1].Trim() } else { '' }
    $temp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    Assert ($default -and $default.StartsWith($temp, [System.StringComparison]::OrdinalIgnoreCase) -and -not $default.StartsWith($repo, [System.StringComparison]::OrdinalIgnoreCase)) 'default results folder is under the system temp directory, outside the repository'
    $summary = Get-Content -LiteralPath (Join-Path $default 'summary.json') -Raw | ConvertFrom-Json
    Assert ($summary.verdict -eq 'PASS' -and $summary.exitCode -eq 0 -and @($summary.checks).Count -eq 5) 'summary.json records the verdict and every selected check'
    if ($default) { Remove-TestFolder $default (Join-Path $temp 'pcloud-test-results') }

    # --- catalog must match the manifest -----------------------------------------------------------------
    $extra = Join-Path $work 'extra-package.json'
    Set-Content -LiteralPath $extra -Encoding ascii -Value '{"root_checks": ["tests/root-ok.ps1"], "packages": [{"path": "pkg/a", "test": "tests/a.ps1"}, {"path": "pkg/b", "test": "tests/b.ps1"}, {"path": "pkg/c", "test": "tests/c.ps1"}]}'
    Assert ((Invoke-Stub @('-ManifestPath', $extra, '-List')).Code -eq 2) 'manifest package without a catalog entry: exit 2'
    $changed = Join-Path $work 'changed-test.json'
    Set-Content -LiteralPath $changed -Encoding ascii -Value '{"root_checks": ["tests/root-ok.ps1"], "packages": [{"path": "pkg/a", "test": "tests/other.ps1"}, {"path": "pkg/b", "test": "tests/b.ps1"}]}'
    Assert ((Invoke-Stub @('-ManifestPath', $changed, '-List')).Code -eq 2) 'catalog entry differing from the manifest test: exit 2'

    # --- the real catalog (listed only, never executed) ---------------------------------------------------
    $r = Invoke-Runner @('-List', '-Json', '-Mode', 'All')
    Assert ($r.Code -eq 0) 'real catalog matches tests/layout-manifest.json'
    $plan = @(); if ($r.Code -eq 0) { $plan = @($r.Text | ConvertFrom-Json | ForEach-Object { $_ }) }
    $forbidden = '(?i)\bapply\b|\bdestroy\b|\bdelete\b|-AllowDestroy|site-rke2\.yml|heal\.yml|deploy-layer0\.ps1|prep-hyperv-host\.ps1|\bdrain\b|\bcordon\b|\breplace\b'
    $unsafe = @($plan | Where-Object { $_.Mode -ne 'Smoke' -and -not $_.Planned -and ((@($_.Script) + @($_.Args)) -join ' ') -match $forbidden })
    Assert ($plan.Count -gt 0 -and $unsafe.Count -eq 0) 'no Static or Live command provisions, repairs or removes infrastructure'
    $live = @($plan | Where-Object { $_.Mode -eq 'Live' -and -not $_.Planned } | ForEach-Object { $_.Id } | Sort-Object)
    Assert ($live -join ',' -eq 'l1-live-guard-cni,l1-live-health,l1-live-inventory') 'only the reviewed read-only Live checks are registered'
    $smoke = @($plan | Where-Object { $_.Mode -eq 'Smoke' -and -not $_.Planned })
    Assert ($smoke.Count -eq 0 -and (@($smoke | ForEach-Object { $_.Id } | Sort-Object) -join ',') -eq '') 'only reviewed package Smoke checks are implemented'
    $l3 = @($plan | Where-Object { $_.Package -eq 'deploy/03-observability' })
    Assert ($l3.Count -gt 0 -and @($l3 | Where-Object { -not $_.Planned }).Count -eq 0) 'every Layer 3 entry is NOT IMPLEMENTED'
    Assert (@($l3 | Where-Object { $_.Required }).Count -eq 0) 'Layer 3 is visible but optional in a default -Mode All selection'
    $r3 = Invoke-Runner @('-List', '-Json', '-Mode', 'All', '-Package', 'deploy/03-observability')
    $l3Named = @(); if ($r3.Code -eq 0) { $l3Named = @($r3.Text | ConvertFrom-Json | ForEach-Object { $_ }) }
    Assert ($l3Named.Count -eq $l3.Count -and @($l3Named | Where-Object { -not $_.Required }).Count -eq 0) 'Layer 3 named by -Package: every check required'
    $l1 = @($plan | Where-Object { $_.Runner -eq 'bash' -or $_.Runner -eq 'ansible' })
    if ($onWindows) { Assert ($l1.Count -gt 0 -and @($l1 | Where-Object { $_.Execution -notlike 'wsl.exe: *' }).Count -eq 0) 'Windows: Bash and Ansible checks run only through wsl.exe' }
    else { Assert ($l1.Count -gt 0 -and @($l1 | Where-Object { $_.Execution -like 'wsl.exe*' }).Count -eq 0) 'Linux: Bash and Ansible checks run natively' }
    $health = @($plan | Where-Object { $_.Id -eq 'l1-live-health' })
    Assert ($health.Count -eq 1 -and (@($health[0].Args) -join ' ') -match 'report_dir=\{results\}') 'health report goes to the results folder, not the package'
}
finally { Remove-TestFolder $work ([System.IO.Path]::GetTempPath()) }

Write-Host ''
Write-Host "$script:passed passed, $script:failed failed"
if ($script:failed -gt 0) { exit 1 }
