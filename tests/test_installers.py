"""Checks for the two installer scripts.

Two layers:

* Source invariants.  Every one of these encodes a bug that was actually shipped
  and then fixed, so they run everywhere and need no PowerShell.
* The real PowerShell runtime suite in ``tests/powershell/runtime_checks.ps1``,
  which is skipped when ``pwsh`` is not on PATH.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "install.sh"
INSTALL_PS1 = ROOT / "install.ps1"
PS_CHECKS = Path(__file__).resolve().parent / "powershell" / "runtime_checks.ps1"


def _pwsh() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


PWSH = _pwsh()
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")


@pytest.fixture(scope="module")
def sh_script() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ps_script() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# install.ps1
# ---------------------------------------------------------------------------


def test_ps_script_declares_both_supported_hosts(ps_script: str) -> None:
    assert "Windows PowerShell 5.1" in ps_script
    assert "irm https://raw.githubusercontent.com" in ps_script


def test_ps_wrapper_block_is_delimited_and_balanced(ps_script: str) -> None:
    assert "$BeginMarker = '# >>> cd-tui wrapper >>>'" in ps_script
    assert "$EndMarker = '# <<< cd-tui wrapper <<<'" in ps_script
    block = re.search(r"\$block = \(Get-CdtuiWrapperBlock\).*?^}", ps_script, re.M | re.S)
    assert block, "the wrapper block is never assigned"
    # The removal regex must anchor both markers or re-installs would duplicate.
    assert "[regex]::Escape($BeginMarker)" in ps_script
    assert "[regex]::Escape($EndMarker)" in ps_script


def test_ps_wrapper_does_not_rely_on_a_bare_python(ps_script: str) -> None:
    """`python` does not exist on POSIX, and may not be the verified interpreter."""
    wrapper = ps_script[ps_script.index("function Get-CdtuiWrapperBlock") :]
    wrapper = wrapper[: wrapper.index("\n}")]
    assert "python -m cdtui.app" not in wrapper
    assert "-m cdtui.app" in wrapper, "the wrapper must still launch the app"
    assert "& $exe" in wrapper, "the wrapper must call the resolved interpreter"


def test_ps_pins_the_resolved_interpreter_path(ps_script: str) -> None:
    finder = ps_script[ps_script.index("function Find-CdtuiPython") :]
    finder = finder[: finder.index("\n}")]
    assert "$command.Source" in finder, "the bare alias is not resolved to a path"
    assert "Test-Path -LiteralPath $resolved" in finder


def test_ps_captures_pip_stderr_outside_the_argument_splat(ps_script: str) -> None:
    """`2>&1` inside `@(...)` is parsed as arguments, so pip's stderr is lost.

    Every failure message below the pip call depends on this, which is why the
    bug was invisible until the installer was actually run.
    """
    pip_call = next(
        line for line in ps_script.splitlines() if "'pip', 'install'" in line and "2>&1" in line
    )
    redirect = pip_call.index("2>&1")
    assert redirect > pip_call.rindex(")"), (
        "the 2>&1 must come after the argument splat closes, "
        f"otherwise it is passed to pip as an argument: {pip_call.strip()}"
    )


def test_ps_detects_a_venv_without_relying_on_the_env_var(ps_script: str) -> None:
    """$env:VIRTUAL_ENV is unset when a venv python is called by absolute path."""
    assert "sys.prefix != sys.base_prefix" in ps_script
    assert "--user" in ps_script


def test_ps_normalises_the_python_floor_from_the_environment(ps_script: str) -> None:
    """An environment variable is a string, so `@(3, 10)` arrives as `"3 10"`."""
    assert "-replace ',', '.'" in ps_script
    assert "-split '[.\\s]+'" in ps_script


def test_ps_restores_the_callers_error_action_preference(ps_script: str) -> None:
    """`irm | iex` runs in the caller's scope; a leaked 'Stop' kills their shell."""
    assert "$CdtuiCallerErrorActionPreference = $ErrorActionPreference" in ps_script
    restore = ps_script[ps_script.index("function Restore-CdtuiCallerPreference") :]
    restore = restore[: restore.index("\n}")]
    # A plain assignment inside a function is function-local and vanishes.
    assert "$global:ErrorActionPreference" in restore

    stop = ps_script[ps_script.index("function Stop-WithError") :]
    stop = stop[: stop.index("\n}")]
    assert "Restore-CdtuiCallerPreference" in stop


def test_ps_does_not_exit_the_caller_under_iex(ps_script: str) -> None:
    """`exit` inside `iex` closes the user's window and takes the error with it."""
    assert "$CdtuiRunningAsFile = [bool]$PSCommandPath" in ps_script
    stop = ps_script[ps_script.index("function Stop-WithError") :]
    stop = stop[: stop.index("\n}")]
    assert "if ($CdtuiRunningAsFile) { exit 1 }" in stop
    assert "throw" in stop


def test_ps_reports_the_real_config_path(ps_script: str) -> None:
    """A hardcoded %APPDATA% guess contradicts platformdirs on macOS and Linux."""
    report = ps_script[ps_script.index("function Show-CdtuiSuccess") :]
    report = report[: report.index("\n}")]
    assert "from cdtui.config import config_path" in report
    assert "Bookmarks live in: $configPath" in report


def test_ps_pep668_hint_uses_platform_paths(ps_script: str) -> None:
    assert "externally-managed-environment" in ps_script
    assert "$IsWindows" in ps_script, "the venv hint must not assume Windows"


def test_both_installers_provision_a_venv_instead_of_only_complaining(
    sh_script: str, ps_script: str
) -> None:
    """PEP 668 must be handled, not just reported.

    A stock Homebrew python, the python.org builds and Debian's system python
    are all "externally managed", so the documented one-liner died at the first
    pip call on a very common setup. Both installers now build a virtualenv and
    retry against it, and the advice printed as a last resort must not send
    people to `pip install cd-tui`, a PyPI package that does not exist yet.
    """
    for name, script, call, retry in (
        ("install.sh", sh_script, "if provision_venv; then", 'run_pip "$1"'),
        (
            "install.ps1",
            ps_script,
            "if (Initialize-CdtuiVenv)",
            "'-m', 'pip', 'install', '--upgrade', $Spec",
        ),
    ):
        pep668 = script.index("externally-managed-environment")
        provision = script.index(call)
        # PEP 668 is recognised first, and the venv is built before giving up.
        assert pep668 < provision, f"{name}: the venv is attempted before PEP 668 is even noticed"
        # The install is then retried against the venv, not abandoned.
        assert script.index(retry, provision) > provision, f"{name}: no retry against the venv"

    for name, script in (("install.sh", sh_script), ("install.ps1", ps_script)):
        assert "pip install --upgrade cd-tui" not in script, (
            f"{name}: still recommends installing the not-yet-published cd-tui from PyPI"
        )
        assert "--break-system-packages cd-tui" not in script, (
            f"{name}: still recommends a PyPI install of a package that 404s"
        )


def test_ps_refuses_insecure_git_urls(ps_script: str) -> None:
    assert "-notlike 'https://*'" in ps_script
    assert "must be an https:// URL" in ps_script
    # The default must be https, and the guard must run before any clone.
    assert "'https://github.com/Xznder1984/CD-TUI.git'" in ps_script
    assert ps_script.index("-notlike 'https://*'") < ps_script.index("clone --quiet")


# ---------------------------------------------------------------------------
# install.sh
# ---------------------------------------------------------------------------


def test_sh_refuses_insecure_git_urls(sh_script: str) -> None:
    assert re.search(r'case "\$CDTUI_GIT_URL" in\s*\n\s*https://\*\)', sh_script)
    assert "must be an https:// URL" in sh_script
    assert sh_script.index("https://*)") < sh_script.index("git clone")
    assert 'CDTUI_GIT_URL="${CDTUI_GIT_URL:-https://github.com/Xznder1984/CD-TUI.git}"' in sh_script


def test_sh_honours_an_explicitly_empty_pypi_spec(sh_script: str) -> None:
    """`${VAR-}` not `${VAR:-}`: an empty value must still mean "use git"."""
    assert "CDTUI_PYPI_SPEC" in sh_script
    assert re.search(r"\$\{CDTUI_PYPI_SPEC-", sh_script)


def test_sh_detects_a_venv_without_relying_on_the_env_var(sh_script: str) -> None:
    assert "sys.prefix != sys.base_prefix" in sh_script
    assert "VIRTUAL_ENV" in sh_script


def test_sh_wrapper_block_is_delimited(sh_script: str) -> None:
    assert "cd-tui wrapper >>>" in sh_script
    assert "cd-tui wrapper <<<" in sh_script


def test_sh_is_syntactically_valid() -> None:
    if os.name == "nt":
        # Windows runners put the Windows-Store "bash" stub on PATH: it exists
        # for `which`, but running it prints "To install..." and exits 1. There
        # is no shell here for this check to meaningfully run.
        pytest.skip("bash on a Windows runner is the Store stub, not a shell")
    if shutil.which("bash") is None:  # pragma: no cover - bash is everywhere
        pytest.skip("bash is not installed")
    proc = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


def test_sh_passes_shellcheck() -> None:
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck is not installed")
    proc = subprocess.run(
        ["shellcheck", "-S", "style", str(INSTALL_SH)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# PowerShell runtime suite
# ---------------------------------------------------------------------------


@needs_pwsh
def test_ps_script_parses(tmp_path: Path) -> None:
    """`Parser` is the authority; a syntax check that cannot run is worthless."""
    probe = (
        "$e = $null\n"
        "$null = [System.Management.Automation.Language.Parser]::ParseFile("
        "$args[0], [ref]$null, [ref]$e)\n"
        "if ($e) { $e[0].Message; exit 1 }\n"
        "'parses clean'\n"
    )
    script = tmp_path / "parse.ps1"
    script.write_text(probe, encoding="utf-8")
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), str(INSTALL_PS1)],
        capture_output=True,
        text=True,
        timeout=300,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "parses clean" in proc.stdout


@pytest.fixture(scope="module")
def venv_python(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A throwaway interpreter that can import textual, for the real install.

    The suite deliberately installs into its own venv: pointing the installer at
    the developer's working environment would replace their editable checkout.
    """
    override = os.environ.get("CDTUI_TEST_PYTHON")
    if override:
        return override

    venv = tmp_path_factory.mktemp("psvenv")
    created = subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if created.returncode != 0:
        pytest.skip(f"could not create a venv: {created.stderr.strip()}")

    bindir = "Scripts" if sys.platform == "win32" else "bin"
    exe = venv / bindir / ("python.exe" if sys.platform == "win32" else "python")

    # Pre-install the runtime dependencies so that an offline machine skips
    # instead of reporting the installer as broken. install.ps1 would fetch
    # these itself; doing it here keeps "no network" distinguishable from
    # "the installer does not work".
    seeded = subprocess.run(
        [str(exe), "-m", "pip", "install", "--quiet", "textual", "platformdirs"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if seeded.returncode != 0:
        pytest.skip(f"could not seed the venv (no package index?): {seeded.stderr[-500:]}")

    return str(exe)


@needs_pwsh
def test_ps_runtime_suite(venv_python: str, tmp_path: Path) -> None:
    """Install for real, in a fake home, and check the wrapper end to end."""
    wheels = sorted((ROOT / "dist").glob("*.whl"))
    wheel = wheels[-1] if wheels else ""

    env = dict(os.environ)
    env["PATH"] = str(Path(venv_python).parent) + os.pathsep + env.get("PATH", "")

    try:
        proc = subprocess.run(
            [
                PWSH,
                "-NoProfile",
                "-File",
                str(PS_CHECKS),
                "-InstallScript",
                str(INSTALL_PS1),
                "-Wheel",
                wheel,
                "-Sandbox",
                str(tmp_path / "pshome"),
            ],
            capture_output=True,
            text=True,
            # Generous, but bounded: the suite runs a dozen real installs, and
            # a mistake in the fake-home layout once made it start the real
            # interactive TUI, which blocked forever on a terminal that will
            # never arrive.  A hang must fail the run, not wedge it.
            timeout=900,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            "install.ps1 runtime checks did not finish in 15 minutes; they most "
            f"likely started the interactive TUI. Partial output:\n"
            f"{(exc.stdout or b'').decode('utf-8', 'replace')[-4000:]}"
        )

    output = proc.stdout + proc.stderr
    failures = [line for line in output.splitlines() if line.startswith("FAIL ")]
    assert not failures, "install.ps1 runtime checks failed:\n" + "\n".join(failures)
    assert proc.returncode == 0, output
    assert "SUMMARY" in output, output
    if wheel:
        assert "SKIP" not in output, (
            "the suite was supposed to run in full with a wheel:\n" + output
        )
