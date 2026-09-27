#!/usr/bin/env bash
#
# CD-TUI installer for macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/Xznder1984/CD-TUI/main/install.sh | bash
#
# Properties this script guarantees:
#   * No root, no sudo, no system-wide writes. Everything lands in $HOME.
#   * Idempotent: re-running refreshes the package and rewrites (never
#     duplicates) the shell wrapper.
#   * HTTPS only, and it talks to exactly two hosts: PyPI and GitHub.
#   * No telemetry of any kind.
#
# The default install source is the git repository, because CD-TUI is not on
# PyPI yet. See the Tunables section below to switch to the published
# distribution once it exists.
#
# If you would rather not pipe a remote script into your shell, clone the
# repository and follow the "Manual installation" section of the README instead.

set -euo pipefail

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

# Where to install from. Exactly one of these two variables is used.
#
#   CDTUI_PYPI_SPEC  a PyPI requirement; empty means "install from git instead"
#   CDTUI_GIT_URL    the git remote consulted when CDTUI_PYPI_SPEC is empty
#
# The default is the git path because CD-TUI is not published to PyPI yet, so
# `curl | bash` works straight from the repository. Once the package is on PyPI,
# set CDTUI_PYPI_SPEC="cd-tui" to install the published distribution instead.
#
# Every tunable below can be overridden from the environment, which matters
# because the documented one-liner pipes this file straight into bash:
#
#   curl -fsSL https://raw.githubusercontent.com/.../install.sh | \
#     CDTUI_PYPI_SPEC=cd-tui bash
#
# An explicit CDTUI_PYPI_SPEC="" still selects the git path.
CDTUI_PYPI_SPEC="${CDTUI_PYPI_SPEC-}"

# Git source used when CDTUI_PYPI_SPEC is empty. Only https:// is accepted.
# Pin CDTUI_GIT_REF to a tag (for example "v1.0.0") to install a known-good
# revision; "main" tracks the newest commit.
CDTUI_GIT_URL="${CDTUI_GIT_URL:-https://github.com/Xznder1984/CD-TUI.git}"
CDTUI_GIT_REF="${CDTUI_GIT_REF:-main}"

# Lowest supported CPython, as "major minor".
CDTUI_MIN_PYTHON="${CDTUI_MIN_PYTHON:-3.10}"

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

BEGIN_MARKER="# >>> cd-tui wrapper >>>"
END_MARKER="# <<< cd-tui wrapper <<<"
REPO_URL="https://github.com/Xznder1984/CD-TUI"

# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

if [ -t 1 ]; then
    C_INFO=$'\033[1;34m'; C_OK=$'\033[1;32m'; C_WARN=$'\033[1;33m'
    C_ERR=$'\033[1;31m'; C_OFF=$'\033[0m'
else
    C_INFO=""; C_OK=""; C_WARN=""; C_ERR=""; C_OFF=""
fi

info() { printf '%s==>%s %s\n' "$C_INFO" "$C_OFF" "$*"; }
ok()   { printf '%s ok %s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%swarn%s %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }
die()  { printf '%serror%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }

# --------------------------------------------------------------------------
# Step 1 - verify Python
# --------------------------------------------------------------------------

python_install_hint() {
    cat <<'HINT'
CD-TUI needs Python 3.10 or newer, which this script will not install for you.

Install it with whichever command matches your system, then re-run this
installer:

  Debian / Ubuntu / Mint      sudo apt update && sudo apt install python3 python3-pip python3-venv
  Fedora / RHEL / CentOS      sudo dnf install python3 python3-pip
  Arch / Manjaro              sudo pacman -S python python-pip
  openSUSE / SLES             sudo zypper install python3 python3-pip
  macOS (Homebrew)            brew install python
  macOS (no Homebrew)         download the installer from https://www.python.org/downloads/macos/
  Windows (PowerShell)        download the installer from https://www.python.org/downloads/windows/
                               and tick "Add python.exe to PATH"

Check what you have with:  python3 --version
HINT
}

check_python() {
    if ! command -v python3 >/dev/null 2>&1; then
        warn "python3 was not found on your PATH."
        printf '\n'
        python_install_hint
        exit 1
    fi

    local found
    found="$(python3 --version 2>&1 | awk '{print $2}')"

    if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= tuple(int(p) for p in '${CDTUI_MIN_PYTHON}'.split('.')) else 1)" 2>/dev/null; then
        warn "python3 ${found} is too old; ${CDTUI_MIN_PYTHON}+ is required."
        printf '\n'
        python_install_hint
        exit 1
    fi

    ok "found python3 ${found} at $(command -v python3)"
}

# --------------------------------------------------------------------------
# Step 2 - install the package
# --------------------------------------------------------------------------

pip_install() {
    # In an active virtualenv, --user is rejected by pip; skip it there.
    # VIRTUAL_ENV/CONDA_PREFIX only exist after `activate`, so also ask the
    # interpreter itself: the PEP 668 message below tells users to run
    # ~/.venvs/cdtui/bin/python, which never sets those variables.
    local user_flag="--user"
    if [ -n "${VIRTUAL_ENV:-}" ] || [ -n "${CONDA_PREFIX:-}" ] \
        || python3 -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)' 2>/dev/null; then
        user_flag=""
        info "virtual environment detected, installing without --user"
    fi

    local log
    log="$(mktemp)"
    # shellcheck disable=SC2086  # user_flag is intentionally unquoted (may be empty)
    if python3 -m pip install $user_flag --upgrade "$1" >"$log" 2>&1; then
        rm -f "$log"
        return 0
    fi

    if grep -qi "externally-managed-environment" "$log"; then
        rm -f "$log"
        cat >&2 <<'MSG'
error your Python installation is "externally managed" (PEP 668), so pip
      refuses to install anything into it.

This script will not override that protection for you. Pick one:

  a) Install into a virtual environment (recommended):
         python3 -m venv ~/.venvs/cdtui
         ~/.venvs/cdtui/bin/python -m pip install --upgrade cd-tui
         ~/.venvs/cdtui/bin/cdtui

  b) Allow user installs for just this one command:
         python3 -m pip install --user --break-system-packages cd-tui

  c) Your distribution may ship an older cd-tui package that shadows it.
MSG
        return 1
    fi

    if grep -qi "No module named pip" "$log"; then
        rm -f "$log"
        cat >&2 <<'MSG'
error python3 cannot find pip.

      Bootstrap it with:  python3 -m ensurepip --upgrade
      then re-run this installer.
MSG
        return 1
    fi

    printf '%s' "$C_ERR" >&2
    printf 'error pip failed to install cd-tui. Full output:\n\n' >&2
    cat "$log" >&2
    rm -f "$log"
    return 1
}

install_package() {
    if [ -n "$CDTUI_PYPI_SPEC" ]; then
        info "installing ${CDTUI_PYPI_SPEC} from PyPI"
        pip_install "$CDTUI_PYPI_SPEC" || die "installation failed."
    else
        case "$CDTUI_GIT_URL" in
            https://*) ;;
            *) die "CDTUI_GIT_URL must be an https:// URL, refusing to clone ${CDTUI_GIT_URL}." ;;
        esac
        info "installing from ${CDTUI_GIT_URL} (ref: ${CDTUI_GIT_REF})"
        command -v git >/dev/null 2>&1 || die "git is required for a source install. Install git, or restore CDTUI_PYPI_SPEC in install.sh."
        local checkout
        checkout="$(mktemp -d)"
        # shellcheck disable=SC2064  # expand $checkout now, on purpose
        trap "rm -rf '$checkout'" RETURN
        git clone --quiet --depth 1 --branch "$CDTUI_GIT_REF" "$CDTUI_GIT_URL" "$checkout" \
            || die "could not clone ${CDTUI_GIT_URL} at ref ${CDTUI_GIT_REF}."
        pip_install "$checkout" || die "installation failed."
    fi

    python3 -c "import cdtui" >/dev/null 2>&1 \
        || die "cd-tui installed but 'import cdtui' still fails. Check that python3 -m pip points at the interpreter you expect."
    ok "cd-tui is importable by $(command -v python3)"
}

# --------------------------------------------------------------------------
# Step 3 - install the shell wrapper
# --------------------------------------------------------------------------

# Remove a previously installed wrapper block, tolerating a missing file.
strip_wrapper() {
    local rc="$1"
    [ -f "$rc" ] || return 0
    awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
        index($0, begin) == 1 { skip = 1; next }
        index($0, end)   == 1 { skip = 0; trailing = 1; next }
        skip == 1 { next }
        trailing == 1 { trailing = 0; if ($0 ~ /^[[:space:]]*$/) next }
        { print }
    ' "$rc" > "$rc.cdtui.tmp" && mv "$rc.cdtui.tmp" "$rc"
}

write_wrapper() {
    local rc="$1"
    strip_wrapper "$rc"
    {
        printf '%s\n' "$BEGIN_MARKER"
        cat <<'WRAPPER'
# Added by CD-TUI (https://github.com/Xznder1984/CD-TUI).
# A child process cannot change this shell's working directory, so cdtui writes
# the folder you pick to ~/.cd_tui_target and exits; this function then performs
# the real cd. Remove this block to uninstall the wrapper.
cdtui() {
    python3 -m cdtui.app "$@"
    local target_file="$HOME/.cd_tui_target"
    if [ -f "$target_file" ]; then
        local target
        target=$(cat "$target_file")
        rm -f "$target_file"
        clear
        cd "$target" || return 1
    fi
}
WRAPPER
        printf '%s\n\n' "$END_MARKER"
    } >> "$rc"
    ok "wrapper installed in ${rc}"
}

install_wrapper() {
    local shell_name
    shell_name="$(basename "${SHELL:-/bin/sh}")"

    case "$shell_name" in
        fish|nushell|nu|csh|tcsh)
            warn "cd-tui's wrapper is written for bash and zsh, and your shell is '${shell_name}'."
            warn "You can still use the TUI with:  python3 -m cdtui.app"
            warn "but the 'cd' handoff has to be wired up by hand for ${shell_name}."
            printf '\n'
            return 0
            ;;
    esac

    local touched=0
    local rc
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
        if [ -f "$rc" ]; then
            write_wrapper "$rc"
            touched=1
        fi
    done

    if [ "$touched" -eq 0 ]; then
        # Neither file exists yet: create the one matching the login shell.
        case "$shell_name" in
            zsh) write_wrapper "$HOME/.zshrc" ;;
            *)   write_wrapper "$HOME/.bashrc" ;;
        esac
        warn "neither ~/.bashrc nor ~/.zshrc existed, so one was created for you."
    fi
}

# --------------------------------------------------------------------------
# Step 4 - report
# --------------------------------------------------------------------------

print_success() {
    local shell_name
    shell_name="$(basename "${SHELL:-/bin/sh}")"

    printf '\n%s%s installed.%s\n\n' "$C_OK" "CD-TUI" "$C_OFF"
    printf 'Bookmarks live in: %s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/cd-tui/config.json"
    printf 'Add your first folder:  python3 -m cdtui.app --settings\n\n'
    printf 'Reload your shell config, or just open a new terminal:\n'
    # The fallback branch prints $SHELL unexpanded on purpose: the user copies
    # that line into their own terminal, where it must still be a variable.
    # shellcheck disable=SC2016
    case "$shell_name" in
        zsh)  printf '    source ~/.zshrc\n' ;;
        bash) printf '    source ~/.bashrc\n' ;;
        *)    printf '    exec "\$SHELL" -l\n' ;;
    esac
    printf '\nThen run:\n\n    cdtui\n\n'
    printf 'Use the arrow keys or j/k to pick a folder and press Enter.\n'
    printf 'Press %sa%s inside the TUI to manage bookmarks.\n' "$C_INFO" "$C_OFF"
    printf 'Press %sq%s to quit without changing directory.\n\n' "$C_INFO" "$C_OFF"
    printf 'Docs: %s\n\n' "$REPO_URL"
}

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

main() {
    printf '\n%sCD-TUI installer%s  (%s)\n\n' "$C_INFO" "$C_OFF" "$REPO_URL"

    check_python
    install_package
    install_wrapper
    print_success
}

# The installer takes no arguments; everything is configured at the top.
main
