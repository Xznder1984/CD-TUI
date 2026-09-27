<#
.SYNOPSIS
    Runtime verification for install.ps1. Requires PowerShell 7+ (pwsh).

.DESCRIPTION
    Every check here exists because it caught a real bug that a syntax check
    could not: pip stderr never being captured, a leaked $ErrorActionPreference
    that killed the caller's shell, a wrapper pinned to a bare `python`, and so
    on. Run it through tests/test_installers.py, or directly:

        pwsh -NoProfile -File tests/powershell/runtime_checks.ps1 `
            -InstallScript ./install.ps1 -Wheel ./dist/cd_tui-1.0.0-py3-none-any.whl

    Exits non-zero if any check fails. Output lines are `PASS <name>`,
    `FAIL <name>` or `SKIP <name>`, which the Python wrapper parses.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallScript,

    # A locally built wheel. Without one the install/wrapper checks are skipped,
    # because they would otherwise have to reach PyPI for a package that is not
    # published yet.
    [string]$Wheel = '',

    # Scratch directory. A fresh one is used when not supplied.
    [string]$Sandbox = ''
)

$ErrorActionPreference = 'Continue'

$InstallScript = (Resolve-Path -LiteralPath $InstallScript).Path
$script:haveWheel = $false
if ($Wheel) {
    $Wheel = (Resolve-Path -LiteralPath $Wheel).Path
    $script:haveWheel = $true
}

if (-not $Sandbox) {
    $Sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("cdtui-ps1-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
}
New-Item -ItemType Directory -Path $Sandbox -Force | Out-Null
# The fake home is wiped before every install; the shadow package must live
# outside it or it disappears and the real interactive TUI starts, hanging the
# whole run on a terminal that will never arrive.
$profile0 = Join-Path $Sandbox 'home'
$shadow = Join-Path $Sandbox 'shadow'
$probeLog = Join-Path $Sandbox 'probe.log'
New-Item -ItemType Directory -Path $profile0 -Force | Out-Null

# A stand-in for the TUI. The wrapper runs `python -m cdtui.app`; a shadow
# package earlier on PYTHONPATH records the working directory and exits, so the
# wrapper's handoff can be checked without launching an interactive app.
New-Item -ItemType Directory -Path (Join-Path $shadow 'cdtui') -Force | Out-Null
Set-Content -Path (Join-Path $shadow 'cdtui/__init__.py') -Value 'version = "0.0.0-test-shadow"'
Set-Content -Path (Join-Path $shadow 'cdtui/app.py') -Value @'
import os
import sys

if "--settings" in sys.argv:
    sys.exit(0)
with open(os.environ["CDTUI_PROBE_OUT"], "a") as fh:
    fh.write("PWD=" + os.getcwd() + "\n")
sys.exit(0)
'@

$script:pass = 0
$script:fail = 0
$script:skip = 0

function Check($name, $condition, $detail = '') {
    if ($condition) {
        Write-Host "PASS $name"
        $script:pass++
    }
    else {
        Write-Host "FAIL $name"
        if ($detail) { Write-Host "     $detail" }
        $script:fail++
    }
}

function Skip($name, $why) {
    Write-Host "SKIP $name ($why)"
    $script:skip++
}

# Wipe the fake home and return the profile path PowerShell will really use.
# $PROFILE is resolved when the engine starts, so it has to be asked for in a
# child process that inherits the sandboxed HOME.
function Reset-Sandbox {
    if (Test-Path $profile0) { Remove-Item $profile0 -Recurse -Force }
    if (Test-Path $probeLog) { Remove-Item $probeLog -Force }
    New-Item -ItemType Directory -Path $profile0 -Force | Out-Null
    $env:HOME = $profile0
    $env:USERPROFILE = $profile0
    $env:XDG_CONFIG_HOME = Join-Path $profile0 '.config'
    Remove-Item -Path env:APPDATA -ErrorAction SilentlyContinue
    $profile = ([string](& pwsh -NoProfile -Command '$PROFILE' 2>$null)).Trim()
    New-Item -ItemType Directory -Path (Split-Path -Parent $profile) -Force | Out-Null
    Set-Content -Path $profile -Value @'
# pre-existing user content that must survive
$env:PS1_SENTINEL = "kept"
'@
    return $profile
}

function Install {
    param([hashtable]$Overrides = @{})
    $profile = Reset-Sandbox
    $env:CdtuiPypiSpec = $Wheel
    foreach ($k in $Overrides.Keys) { Set-Item -Path "env:$k" -Value $Overrides[$k] }
    $out = & pwsh -NoProfile -File $InstallScript 2>&1 | Out-String
    $code = $LASTEXITCODE
    Remove-Item -Path env:CdtuiPypiSpec -ErrorAction SilentlyContinue
    foreach ($k in $Overrides.Keys) { Remove-Item -Path "env:$k" -ErrorAction SilentlyContinue }
    return [pscustomobject]@{ Code = $code; Out = $out; Profile = $profile }
}

function WrapperCount([string]$Profile) {
    (Select-String -Path $Profile -Pattern '>>> cd-tui wrapper >>>' -AllMatches).Matches.Count
}

# ---------------------------------------------------------------------------

if (-not $script:haveWheel) {
    Skip 'install and wrapper checks' 'no wheel supplied'
}
else {
    Write-Host "== install and wrapper =="

    $r = Install
    Check 'installer honours $env:CdtuiPypiSpec and succeeds' ($r.Code -eq 0) $r.Out
    Check 'wrapper block is written exactly once' ((WrapperCount $r.Profile) -eq 1) $r.Out
    Check 'pre-existing profile content survives' ((Get-Content $r.Profile -Raw) -match 'PS1_SENTINEL')
    Check 'wrapper runs the module' ((Get-Content $r.Profile -Raw) -match 'cdtui\.app')
    Check 'wrapper is pinned to an interpreter path' ((Get-Content $r.Profile -Raw) -match "& '[^']*python")
    Check 'success message reports the real config path' ($r.Out -match 'Bookmarks live in: .*cd-tui')

    Write-Host "== idempotency =="
    $hashes = @(); $codes = @()
    for ($i = 0; $i -lt 3; $i++) {
        $again = Install
        $codes += $again.Code
        $hashes += (Get-FileHash $again.Profile -Algorithm MD5).Hash
    }
    Check 'every re-install succeeds' (($codes | Where-Object { $_ -ne 0 }).Count -eq 0) ($codes -join ',')
    Check 'profile is byte-identical across 3 installs' (($hashes | Select-Object -Unique).Count -eq 1)
    $final = Install
    Check 'still exactly one wrapper block' ((WrapperCount $final.Profile) -eq 1)

    Write-Host "== the wrapper hands the directory over =="
    $profile = $final.Profile
    $target = Join-Path $profile0 'chosen folder'
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    $probe = @"
`$env:HOME = '$profile0'
`$env:USERPROFILE = '$profile0'
Set-Content -Path (Join-Path `$env:HOME '.cd_tui_target') -Value '$target'
. '$profile'
cdtui
Write-Output ('PWD=' + (Get-Location).Path)
Write-Output ('TARGET_LEFT=' + (Test-Path (Join-Path `$env:HOME '.cd_tui_target')))
"@
    Set-Content -Path $probeLog -Value ''
    $probeOut = & pwsh -NoProfile -Command "`$env:PYTHONPATH='$shadow'; `$env:CDTUI_PROBE_OUT='$probeLog'; $probe" 2>&1 | Out-String
    $seen = @((Get-Content $probeLog) | Where-Object { $_ -ne '' })
    Check 'the app runs exactly once' ($seen.Count -eq 1) "count=$($seen.Count) [$($seen -join ' | ')]"
    # The app necessarily runs in the pre-cd directory: it writes the target and
    # exits, and only then does the wrapper Set-Location. So the assertion is on
    # the shell's cwd after cdtui returns, not on the cwd the app saw.
    Check 'the shell lands in the target' ($probeOut -match "PWD=$([regex]::Escape($target))") $probeOut.Trim()
    Check 'the target file is consumed' ($probeOut -match 'TARGET_LEFT=False') $probeOut.Trim()

    Write-Host "== no target means no directory change =="
    $probe2 = @"
`$env:HOME = '$profile0'
. '$profile'
Set-Location ([System.IO.Path]::GetTempPath())
cdtui
Write-Output ('PWD=' + (Get-Location).Path)
"@
    Set-Content -Path $probeLog -Value ''
    $probe2Out = & pwsh -NoProfile -Command "`$env:PYTHONPATH='$shadow'; `$env:CDTUI_PROBE_OUT='$probeLog'; $probe2" 2>&1 | Out-String
    $seen2 = @((Get-Content $probeLog) | Where-Object { $_ -ne '' })
    Check 'the app runs exactly once' ($seen2.Count -eq 1) "count=$($seen2.Count) [$($seen2 -join ' | ')]"
    Check 'the shell stays put' ($seen2 -notcontains "PWD=$target") "seen=[$($seen2 -join ' | ')] $($probe2Out.Trim())"
}

Write-Host "== guards =="

Reset-Sandbox | Out-Null
$tooOld = & pwsh -NoProfile -Command "`$env:CdtuiMinPython='99.0'; & '$InstallScript'" 2>&1 | Out-String
$tooOldCode = $LASTEXITCODE
Check 'an unsatisfiable python floor is reported' ($tooOld -match '99\.0') $tooOld.Trim()
Check 'an unsatisfiable python floor exits non-zero' ($tooOldCode -ne 0) "exit=$tooOldCode"

Reset-Sandbox | Out-Null
$insecure = & pwsh -NoProfile -Command "`$env:CdtuiPypiSpec=''; `$env:CdtuiGitUrl='http://example.com/x.git'; & '$InstallScript'" 2>&1 | Out-String
$insecureCode = $LASTEXITCODE
Check 'a non-https git url is refused' ($insecure -match 'https') $insecure.Trim()
Check 'a non-https git url exits non-zero' ($insecureCode -ne 0) "exit=$insecureCode"

Write-Host "== irm | iex must not wreck the caller's session =="
# `iex` of the file text is the same code path as `irm | iex`: it runs in the
# caller's scope, where a leaked $ErrorActionPreference of 'Stop' turns an
# ordinary typo into a dead shell, and where `exit` closes their window.

if (-not $script:haveWheel) {
    Skip 'iex session checks' 'no wheel supplied'
}
else {
    Reset-Sandbox | Out-Null
    $q = $InstallScript.Replace("'", "''")
    $okSession = & pwsh -NoProfile -Command "`$env:CdtuiPypiSpec='$Wheel'; `$ErrorActionPreference='Continue'; Invoke-Expression (Get-Content -LiteralPath '$q' -Raw); Write-Output ('EAP=' + `$ErrorActionPreference)" 2>&1 | Out-String
    Check 'iex success restores the caller preference' ($okSession -match 'EAP=Continue') $okSession.Trim()

    Reset-Sandbox | Out-Null
    $badSession = & pwsh -NoProfile -Command "`$env:CdtuiPypiSpec='cd-tui'; `$env:CdtuiMinPython='99.0'; `$ErrorActionPreference='Continue'; try { Invoke-Expression (Get-Content -LiteralPath '$q' -Raw) } catch { }; Write-Output ('EAP=' + `$ErrorActionPreference)" 2>&1 | Out-String
    Check 'iex failure restores the caller preference' ($badSession -match 'EAP=Continue') $badSession.Trim()
    Check 'iex failure does not close the shell' ($badSession -match 'too old') $badSession.Trim()
}

# ---------------------------------------------------------------------------

Write-Host "SUMMARY passed=$($script:pass) failed=$($script:fail) skipped=$($script:skip)"
if (Test-Path $Sandbox) { Remove-Item $Sandbox -Recurse -Force -ErrorAction SilentlyContinue }
if ($script:fail) { exit 1 }
exit 0
