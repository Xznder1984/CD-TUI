<#
.SYNOPSIS
    CD-TUI installer for Windows PowerShell 5.1 and PowerShell 7+.

.DESCRIPTION
    Run it with the one-liner:

        irm https://raw.githubusercontent.com/Xznder1984/CD-TUI/main/install.ps1 | iex

    Properties this script guarantees:
      * No administrator rights, no machine-wide writes. Everything lands in
        your user profile and your per-user Python site-packages.
      * Idempotent: re-running refreshes the package and rewrites (never
        duplicates) the PowerShell wrapper block in $PROFILE.
      * HTTPS only, and it talks to exactly two hosts: PyPI and GitHub.
      * No telemetry of any kind.

    The default install source is the git repository, because CD-TUI is not
    published to PyPI yet, so `irm | iex` works straight from the repository.
    Set $CdtuiPypiSpec below to install the published distribution instead.

    If you would rather not pipe a remote script into your shell, download this
    file, read it, and follow the "Manual installation" section of the README.
#>

Set-StrictMode -Version Latest

# The documented invocation is `irm <url> | iex`, which runs this text in the
# *caller's* scope. Anything we set here would survive in their interactive
# session, and a leaked $ErrorActionPreference of 'Stop' turns an ordinary typo
# into a terminated shell. Remember what the caller had and put it back before
# every exit path.
$CdtuiCallerErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Stop'

# Set only when this file is run with `pwsh -File`. Under `irm | iex` the text is
# pasted into the caller's session, where `exit` would close their shell and
# take the error message with it, so failures throw there instead.
$CdtuiRunningAsFile = [bool]$PSCommandPath

function Restore-CdtuiCallerPreference {
    # $global:, because under `iex` the caller's session *is* the global scope.
    # A plain assignment here would create a function-local variable that
    # vanishes on return, leaving the shell stuck in 'Stop'.
    $global:ErrorActionPreference = $CdtuiCallerErrorActionPreference
}

# Native commands signal failure through $LASTEXITCODE, which we inspect
# explicitly so the error messages can be specific. On PowerShell 7.4+ a native
# command writing to stderr would otherwise become a terminating error and hide
# pip's output, which is the thing most likely to explain a failure.
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Where to install from. Exactly one of these two variables is used.
#
#   $CdtuiPypiSpec  a PyPI requirement; '' means "install from git instead"
#   $CdtuiGitUrl    the git remote consulted when $CdtuiPypiSpec is empty
#
# The default is the git path because CD-TUI is not on PyPI yet. Once it is
# published, set $CdtuiPypiSpec = 'cd-tui' to install that distribution.
#
# Every tunable below can also be overridden from the environment, which is what
# makes it reachable from the documented one-liner -- you cannot edit a script
# that is piped straight into iex:
#
#   $env:CdtuiPypiSpec = 'cd-tui'
#   irm https://raw.githubusercontent.com/.../install.ps1 | iex
#
# An explicit $env:CdtuiPypiSpec = '' still selects the git path.
$env:CdtuiPypiSpec = if ($null -eq $env:CdtuiPypiSpec) { '' } else { $env:CdtuiPypiSpec }
$CdtuiPypiSpec = $env:CdtuiPypiSpec

# Git source used when $CdtuiPypiSpec is empty. Only https:// is accepted.
# Pin $CdtuiGitRef to a tag (for example 'v1.0.0') to install a known-good
# revision; 'main' tracks the newest commit.
$CdtuiGitUrl = if ($env:CdtuiGitUrl) { $env:CdtuiGitUrl } else { 'https://github.com/Xznder1984/CD-TUI.git' }
$CdtuiGitRef = if ($env:CdtuiGitRef) { $env:CdtuiGitRef } else { 'main' }

# Lowest supported CPython, as @('major', 'minor').  An environment override
# can only ever be a string, so normalise "3.10", "3,10" and "3 10" too --
# otherwise $CdtuiMinPython[1] silently becomes $null and every interpreter
# looks too old.
$CdtuiMinPython = if ($env:CdtuiMinPython) {
    , @(($env:CdtuiMinPython -replace ',', '.') -split '[.\s]+' |
        Where-Object { $_ -ne '' } | ForEach-Object { [int]$_ })
}
else {
    @(3, 10)
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

$BeginMarker = '# >>> cd-tui wrapper >>>'
$EndMarker = '# <<< cd-tui wrapper <<<'
$RepoUrl = 'https://github.com/Xznder1984/CD-TUI'

# Resolved by Find-CdtuiPython.
$script:PythonExe = $null
$script:PythonPrefix = @()

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

function Write-Info {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host " ok $Message" -ForegroundColor Green
}

function Write-Note {
    param([string]$Message)
    Write-Host "warn $Message" -ForegroundColor Yellow
}

function Stop-WithError {
    param([string]$Message)
    Write-Host "error $Message" -ForegroundColor Red
    Restore-CdtuiCallerPreference
    if ($CdtuiRunningAsFile) { exit 1 }
    # `irm | iex`: abort the installer but keep the user's shell open so they
    # can read the message above.
    throw 'CD-TUI installation failed (see the message above).'
}

function Get-CdtuiPythonHint {
    return @'
CD-TUI needs Python 3.10 or newer, which this script will not install for you.

  * Download the 64-bit installer from https://www.python.org/downloads/windows/
  * Tick "Add python.exe to PATH" on the first screen.
  * Reopen PowerShell afterwards so it picks up the new PATH.

Check what you have with:  python --version
'@
}

# ---------------------------------------------------------------------------
# Step 1 - find and verify Python
# ---------------------------------------------------------------------------

function Invoke-CdtuiPython {
    param([string[]]$PyArgs = @())
    & $script:PythonExe @($script:PythonPrefix + $PyArgs)
}

function Find-CdtuiPython {
    # 'python' first, as documented; the others are common on Windows.
    $candidates = @(
        @{ Exe = 'python'; Prefix = @() },
        @{ Exe = 'python3'; Prefix = @() },
        @{ Exe = 'py'; Prefix = @('-3') }
    )

    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Exe -ErrorAction SilentlyContinue
        if ($null -eq $command) { continue }

        $script:PythonExe = $candidate.Exe
        $script:PythonPrefix = $candidate.Prefix

        # Reject a Windows Store stub, which exits non-zero with no output.
        $probe = (& $script:PythonExe @($script:PythonPrefix + @('-c', 'import sys; print("%d.%d" % sys.version_info[:2])')) 2>&1)
        if ($LASTEXITCODE -ne 0) { continue }
        $version = ([string]($probe | Select-Object -Last 1)).Trim()
        if ($version -notmatch '^\d+\.\d+$') { continue }

        # Pin the resolved absolute path, not the bare alias: the wrapper in
        # $PROFILE runs in every later shell, where a bare `python` may point
        # somewhere else or not exist at all (POSIX only has `python3`).
        $resolved = [string]$command.Source
        if (-not $resolved) { $resolved = [string]$command.Definition }
        if ($resolved -and (Test-Path -LiteralPath $resolved)) {
            $script:PythonExe = $resolved
        }

        $parts = $version -split '\.' | ForEach-Object { [int]$_ }
        $tooOld = ($parts[0] -lt $CdtuiMinPython[0]) -or
                  (($parts[0] -eq $CdtuiMinPython[0]) -and ($parts[1] -lt $CdtuiMinPython[1]))
        if ($tooOld) {
            Stop-WithError ("python $version is too old; {0}.{1}+ is required." -f $CdtuiMinPython[0], $CdtuiMinPython[1])
        }

        Write-Ok "found python $version at $((Get-Command $script:PythonExe).Source)"
        return
    }

    Write-Note 'no suitable Python 3.10+ interpreter was found on your PATH.'
    Write-Host ''
    Write-Host (Get-CdtuiPythonHint)
    exit 1
}

# ---------------------------------------------------------------------------
# Step 2 - install the package
# ---------------------------------------------------------------------------

function Install-CdtuiWithPip {
    param([string]$Spec)

    # Inside a virtual environment pip rejects --user, so skip it there.
    # $env:VIRTUAL_ENV only exists after `activate`, so also ask the
    # interpreter: the PEP 668 hint below points at a venv python invoked by
    # absolute path, which never sets that variable.
    $userFlag = @('--user')
    $inVenv = $env:VIRTUAL_ENV
    if (-not $inVenv) {
        $probe = & $script:PythonExe @($script:PythonPrefix + @(
                '-c',
                'import sys; print(1 if sys.prefix != sys.base_prefix else 0)'
            ) 2>$null)
        $inVenv = (([string]($probe | Select-Object -Last 1)).Trim() -eq '1')
    }
    if ($inVenv) {
        $userFlag = @()
        Write-Info 'virtual environment detected, installing without --user'
    }

    # The 2>&1 must sit *outside* the @(...) splat. Inside it, PowerShell parses
    # it as part of the argument list, so pip's stderr never reached $output and
    # every failure below this line matched nothing.
    $output = & $script:PythonExe @($script:PythonPrefix + @('-m', 'pip', 'install') + $userFlag + @('--upgrade', $Spec)) 2>&1
    if ($LASTEXITCODE -eq 0) { return }

    $text = ($output | ForEach-Object { [string]$_ }) -join "`n"

    if ($text -match 'No module named pip') {
        Stop-WithError @'
python cannot find pip.

      Bootstrap it with:  python -m ensurepip --upgrade
      then re-run this installer.
'@
    }
    if ($text -match 'externally-managed-environment') {
        # Use the interpreter we actually found: a bare `python` does not exist
        # on macOS or Linux, where the command is `python3`.  Build the venv paths
        # from the platform too, so the hint is copy-pasteable wherever it runs.
        $hintExe = "'" + ($script:PythonExe -replace "'", "''") + "'"
        $hintExe = $hintExe + ((($script:PythonPrefix | ForEach-Object { " '" + ($_ -replace "'", "''") + "'" }) -join ''))
        $venvRoot = Join-Path $HOME 'venvs/cdtui'
        $venvDir = $venvRoot
        $venvExe = Join-Path $venvRoot 'Scripts/python.exe'
        $venvRun = Join-Path $venvRoot 'Scripts/cdtui.exe'
        if (-not $IsWindows) {
            $venvExe = Join-Path $venvRoot 'bin/python'
            $venvRun = Join-Path $venvRoot 'bin/cdtui'
        }
        Stop-WithError @"
your Python installation is "externally managed" (PEP 668), so pip refuses to
install anything into it.

This script will not override that protection for you. Pick one:

  a) Install into a virtual environment (recommended):
         & $hintExe -m venv $venvDir
         & $venvExe -m pip install --upgrade cd-tui
         & $venvRun

  b) Allow user installs for just this one command:
         & $hintExe -m pip install --user --break-system-packages cd-tui
"@
    }

    Write-Host "error pip failed to install cd-tui. Full output:" -ForegroundColor Red
    Write-Host ''
    Write-Host $text
    exit 1
}

function Install-CdtuiPackage {
    if ($CdtuiPypiSpec) {
        Write-Info "installing $CdtuiPypiSpec from PyPI"
        Install-CdtuiWithPip -Spec $CdtuiPypiSpec
    }
    else {
        if ($CdtuiGitUrl -notlike 'https://*') {
            Stop-WithError "`$CdtuiGitUrl must be an https:// URL, refusing to clone $CdtuiGitUrl."
        }
        $git = Get-Command git -ErrorAction SilentlyContinue
        if ($null -eq $git) {
            Stop-WithError 'git is required for a source install. Install git, or set $CdtuiPypiSpec.'
        }

        Write-Info "installing from $CdtuiGitUrl (ref: $CdtuiGitRef)"
        $checkout = Join-Path ([IO.Path]::GetTempPath()) ("cdtui-" + [Guid]::NewGuid().ToString('N'))
        try {
            & $git.Source clone --quiet --depth 1 --branch $CdtuiGitRef $CdtuiGitUrl $checkout 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Stop-WithError "could not clone $CdtuiGitUrl at ref $CdtuiGitRef."
            }
            Install-CdtuiWithPip -Spec $checkout
        }
        finally {
            if (Test-Path $checkout) { Remove-Item -Recurse -Force $checkout -ErrorAction SilentlyContinue }
        }
    }

    & $script:PythonExe @($script:PythonPrefix + @('-c', 'import cdtui')) 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "cd-tui installed but 'import cdtui' still fails. Check that python -m pip points at the interpreter you expect."
    }
    Write-Ok 'cd-tui is importable by your python'
}

# ---------------------------------------------------------------------------
# Step 3 - install the PowerShell wrapper
# ---------------------------------------------------------------------------

function Get-CdtuiWrapperBlock {
    # Call the interpreter this installer verified rather than a bare
    # `python`, which does not exist on POSIX (only `python3`) and would
    # otherwise silently pick a different interpreter from PATH.
    $exe = "'" + (($script:PythonExe -replace "'", "''")) + "'"
    $prefix = (($script:PythonPrefix | ForEach-Object { " '" + ($_ -replace "'", "''") + "'" }) -join '')

    # A here-string so nothing in the wrapper is expanded by this installer.
    # `$target` and `$HOME` must reach the user's profile intact.
    return @"
$BeginMarker
# Added by CD-TUI ($RepoUrl).
# A child process cannot change this shell's working directory, so cdtui writes
# the folder you pick to ~\.cd_tui_target and exits; this function then performs
# the real Set-Location. Remove this block to uninstall the wrapper.
function cdtui {
    & $exe$prefix -m cdtui.app @args
    `$target = Join-Path `$HOME '.cd_tui_target'
    if (Test-Path `$target) {
        `$folder = (Get-Content -LiteralPath `$target -Raw).Trim()
        Remove-Item -LiteralPath `$target -Force
        Clear-Host
        Set-Location -LiteralPath `$folder
    }
}
$EndMarker
"@
}

function Install-CdtuiWrapper {
    $profile = $PROFILE

    $directory = Split-Path -Parent $profile
    if (-not (Test-Path $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $existing = ''
    $newline = "`r`n"
    if (Test-Path $profile) {
        $existing = [IO.File]::ReadAllText($profile)
        if ($existing -match "`r`n") { $newline = "`r`n" } else { $newline = "`n" }
    }

    # Remove any previous block. The trailing newline is part of the pattern so
    # repeated runs converge on byte-identical output instead of stacking blanks.
    $pattern = '(?ms)^' + [regex]::Escape($BeginMarker) + '.*?^' + [regex]::Escape($EndMarker) + '\r?\n?'
    $existing = [regex]::Replace($existing, $pattern, '')
    $existing = $existing -replace '(\r?\n)+\z', ''

    $block = (Get-CdtuiWrapperBlock) -replace "`r?`n", $newline
    $block = $block -replace '(\r?\n)+\z', ''

    $content = if ($existing.Length -gt 0) { $existing + $newline + $newline + $block + $newline }
               else { $block + $newline }

    [IO.File]::WriteAllText($profile, $content, (New-Object Text.UTF8Encoding $false))
    Write-Ok "wrapper installed in $profile"
}

# ---------------------------------------------------------------------------
# Step 4 - report
# ---------------------------------------------------------------------------

function Show-CdtuiSuccess {
    $shell = Split-Path -Leaf $PROFILE
    # Ask the installed package where it will really read and write.  Guessing
    # a path here used to print a Windows-only %APPDATA% location on macOS and
    # Linux, which contradicts what cdtui.config actually uses (platformdirs).
    $configPath = & $script:PythonExe @($script:PythonPrefix + @(
            '-c',
            'from cdtui.config import config_path; print(config_path())'
        ) 2>$null)
    $configPath = ([string]($configPath | Select-Object -Last 1)).Trim()
    if (-not $configPath) {
        $configPath = Join-Path $HOME 'cd-tui/config.json'
    }

    Write-Host ''
    Write-Host 'CD-TUI installed.' -ForegroundColor Green
    Write-Host ''
    Write-Host "Bookmarks live in: $configPath"
    Write-Host 'Add your first folder:  python -m cdtui.app --settings'
    Write-Host ''
    Write-Host 'Reload your profile, or just open a new PowerShell window:'
    Write-Host "    . `"$PROFILE`""
    Write-Host ''
    Write-Host 'Then run:'
    Write-Host ''
    Write-Host '    cdtui'
    Write-Host ''
    Write-Host 'Use the arrow keys or j/k to pick a folder and press Enter.'
    Write-Host 'Press a inside the TUI to manage bookmarks.'
    Write-Host 'Press q to quit without changing directory.'
    Write-Host ''
    Write-Host "Docs: $RepoUrl"
    Write-Host ''

    if (Get-ExecutionPolicy -Scope CurrentUser -ErrorAction SilentlyContinue) {
        $policy = Get-ExecutionPolicy -Scope CurrentUser
        if ($policy -in @('Restricted', 'AllSigned')) {
            Write-Note "your CurrentUser execution policy is '$policy', so PowerShell may ignore $shell."
            Write-Note "If 'cdtui' is not recognised, run:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"
        }
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host "CD-TUI installer  ($RepoUrl)" -ForegroundColor Cyan
Write-Host ''

Find-CdtuiPython
Install-CdtuiPackage
Install-CdtuiWrapper
Show-CdtuiSuccess

# Success path: hand the caller back their own error preference.
Restore-CdtuiCallerPreference
