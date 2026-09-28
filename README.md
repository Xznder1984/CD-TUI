# CD-TUI

**A terminal directory bookmarking and navigation tool, with a companion settings GUI.**

`cd` is one of the few commands a child process cannot run for you. Shell functions and
aliases can, because they run inside your interactive shell. `cdtui` is a Textual TUI that
picks a folder, hands it back to your shell, and gets out of the way:

```
$ cdtui
```

Pick a bookmark with the arrow keys or `j`/`k`, press <kbd>Enter</kbd>, and your shell
ends up in that directory. Press <kbd>q</kbd> and nothing happens at all.

Bookmarks are managed in a real settings window (`a` inside the TUI, or
`python -m cdtui.app --settings`) with a native folder picker, drag-free reordering, and
inline save status. Everything is written to disk the moment you change it.

---

## Table of contents

- [Requirements](#requirements)
- [Install](#install)
  - [macOS and Linux](#macos-and-linux)
  - [Windows](#windows)
  - [Manual installation](#manual-installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [How the shell integration works](#how-the-shell-integration-works)
- [Requirements and dependencies](#requirements-and-dependencies)
- [Uninstalling](#uninstalling)
- [Development](#development)
- [License](#license)

---

## Requirements

- Python **3.10** or newer, on your `PATH`.
- Bash or Zsh (macOS and Linux), or PowerShell 5.1+ (Windows).
- No compiler, no Node.js, no other non-Python binaries.

---

## Install

### macOS and Linux

```bash
curl -fsSL https://raw.githubusercontent.com/Xznder1984/CD-TUI/main/install.sh | bash
```

### Windows

```powershell
irm https://raw.githubusercontent.com/Xznder1984/CD-TUI/main/install.ps1 | iex
```

Then **restart your terminal** (or `source ~/.zshrc` / `. $PROFILE`) and run:

```
cdtui
```

Both installers are idempotent: re-running refreshes the package and rewrites the shell
wrapper in place instead of adding a second copy. Neither needs `sudo`, administrator
rights, or any machine-wide change.

<details>
<summary>Tuning the one-liner without editing the script</summary>

Because the one-liner pipes the installer straight into a shell, you cannot edit it
first. Every tunable is therefore also read from the environment:

```bash
# Install the published wheel instead of tracking the git default.
curl -fsSL https://raw.githubusercontent.com/Xznder1984/CD-TUI/main/install.sh | \
  CDTUI_PYPI_SPEC=cd-tui bash

# Pin a known-good revision.
curl -fsSL https://raw.githubusercontent.com/Xznder1984/CD-TUI/main/install.sh | \
  CDTUI_GIT_REF=v1.0.0 bash
```

`CDTUI_PYPI_SPEC`, `CDTUI_GIT_URL`, `CDTUI_GIT_REF`, `CDTUI_MIN_PYTHON` and
`CDTUI_PY` are all accepted. Only `https://` git URLs are allowed.

Running the installer inside a virtual environment is fine: it detects the venv and
installs without `--user`, which is the one thing `pip` rejects in that situation.

If your `python3` is *externally managed* (PEP 668 — the default for Homebrew
python, the python.org builds, and Debian/Ubuntu system python) then `pip` refuses
to install into it at all. The installer does not override that protection;
instead it creates `~/.venvs/cdtui`, installs there, and points the wrapper at
that interpreter:

```
==> python is externally managed, so cd-tui goes into its own virtualenv
 ok cd-tui is importable by /Users/you/.venvs/cdtui/bin/python
Installed into: /Users/you/.venvs/cdtui/bin/python
```

To install into a virtualenv you already have, name it with `CDTUI_PY`:

```bash
CDTUI_PY=~/.venvs/mine/bin/python bash <(curl -fsSL https://raw.githubusercontent.com/Xznder1984/CD-TUI/main/install.sh)
```

Re-running the installer is safe: it reuses `~/.venvs/cdtui` rather than rebuilding
it, and rewrites the wrapper block in place instead of appending a second copy.

</details>

<details>
<summary>Tuning the PowerShell one-liner</summary>

`install.ps1` reads the same tunables from the environment, under
`$env:Cdtui*` names. Because a PowerShell environment variable is always a
string, `CdtuiMinPython` accepts `3.10`, `3,10` or `3 10`:

```powershell
# Install the published wheel instead of tracking the git default.
irm https://raw.githubusercontent.com/Xznder1984/CD-TUI/main/install.ps1 | iex

# Pin a known-good revision.
$env:CdtuiGitRef = 'v1.0.0'
irm https://raw.githubusercontent.com/Xznder1984/CD-TUI/main/install.ps1 | iex
```

The wrapper written to your `$PROFILE` calls the exact interpreter the installer
verified, pinned to an absolute path, so it does not depend on a bare `python`
being on `PATH` in later shells.

As with the bash installer, an externally managed (PEP 668) interpreter is handled
rather than merely reported: `install.ps1` creates `~/venvs/cdtui`, installs into
it, and bakes that interpreter into the wrapper. Pass `-PythonExe` to install into
a virtualenv you already have.

</details>

<details>
<summary>If you would rather not pipe a remote script into your shell</summary>

That is a reasonable instinct. Read the installer first, or install from a clone:

```bash
git clone https://github.com/Xznder1984/CD-TUI.git
cd CD-TUI
python3 -m pip install --user .
```

Then add the wrapper yourself — see
[How the shell integration works](#how-the-shell-integration-works) — and you are done.

</details>

---

## Usage

### The TUI

| Key | Action |
| --- | --- |
| <kbd>↑</kbd> / <kbd>k</kbd> | Move selection up |
| <kbd>↓</kbd> / <kbd>j</kbd> | Move selection down |
| <kbd>Home</kbd> / <kbd>End</kbd> | Jump to first / last bookmark |
| <kbd>Enter</kbd> | `cd` into the selected folder and exit |
| <kbd>a</kbd> | Open the settings window |
| <kbd>r</kbd> | Reload bookmarks from disk |
| <kbd>q</kbd> / <kbd>Ctrl</kbd>+<kbd>C</kbd> | Quit without changing directory |

The selected row is marked with a `▸` glyph and an accent border, so it stays obvious in
both light and dark terminal themes. Every foreground/background pair in the palette is
audited against **WCAG AA** contrast ratios (4.5:1 for text, 3:1 for UI borders) — there is
a test that enforces it, so the colours cannot silently regress.

### The settings window

```bash
python -m cdtui.app --settings
```

- **Add Folder** — a required alias field plus a native OS folder picker
  (`Browse…`). Both fields have real `<Label>` widgets bound to them, and
  <kbd>Tab</kbd> order runs top-to-bottom: the form, then each row, then **Done**.
- **Rename**, **Change…**, **▲ Up**, **▼ Down**, **Delete** per row.
- Changes are saved to disk immediately after every action, and each one reports success
  or the exact reason it failed in an inline status line. There is no unsaved state and
  no silent failure.
- <kbd>Ctrl</kbd>+<kbd>Enter</kbd> (or <kbd>Enter</kbd> in a field) adds; <kbd>Esc</kbd> closes.

The window is built with plain `tkinter`, and transparently upgrades its appearance when
[CustomTkinter](https://github.com/tomerfiliba-org/customtkinter) happens to be installed.

### Command line

```
cdtui                  # start the TUI (the usual entry point)
cdtui --settings       # start the settings window directly
cdtui --tui            # force the TUI even if a default changed
cdtui --config PATH    # use an alternative bookmarks file
cdtui --version        # print the version
cdtui --help           # full usage
```

---

## Configuration

Bookmarks live in a single human-readable JSON file:

| Platform | Path |
| --- | --- |
| macOS / Linux | `~/.config/cd-tui/config.json` (honours `XDG_CONFIG_HOME`) |
| Windows | `%APPDATA%\cd-tui\config.json` |

The directory is created automatically on first run. The file looks like this:

```json
{
  "version": 1,
  "bookmarks": [
    { "alias": "work", "path": "/Users/you/src/work" },
    { "alias": "dotfiles", "path": "/Users/you/.config" }
  ]
}
```

Order is meaningful — it is the order the TUI lists bookmarks in. Writes are atomic (a
temporary file plus `os.replace`), so an interrupted write can never leave you with a
half-written file. Malformed content is reported by name instead of being silently
discarded, and duplicate aliases are rejected.

---

## How the shell integration works

A program cannot change its parent shell's working directory, so `cdtui` splits the job in
two:

1. The TUI writes the folder you chose to `~/.cd_tui_target` and exits.
2. A small shell function — installed into `~/.zshrc`, `~/.bashrc`, or your PowerShell
   `$PROFILE` — reads that file, deletes it, and performs the real `cd`.

That is the whole trick, and it is why the wrapper matters. Running `python -m cdtui.app`
directly shows you the picker but cannot change directory; run `cdtui`.

The block is fenced by markers, so re-running the installer replaces it rather than
appending a duplicate, and you can remove it by hand at any time:

```bash
# ~/.zshrc or ~/.bashrc
# >>> cd-tui wrapper >>>
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
    }
}
# <<< cd-tui wrapper <<<
```

```powershell
# $PROFILE
# >>> cd-tui wrapper >>>
# Added by CD-TUI (https://github.com/Xznder1984/CD-TUI).
# A child process cannot change this shell's working directory, so cdtui writes
# the folder you pick to ~\.cd_tui_target and exits; this function then performs
# the real Set-Location. Remove this block to uninstall the wrapper.
function cdtui {
    python -m cdtui.app @args
    $target = Join-Path $HOME '.cd_tui_target'
    if (Test-Path $target) {
        $folder = (Get-Content -LiteralPath $target -Raw).Trim()
        Remove-Item -LiteralPath $target -Force
        Clear-Host
        Set-Location -LiteralPath $folder
    }
}
# <<< cd-tui wrapper <<<
```

The target file is created with owner-only permissions (`0600`), and it is deleted as soon
as the wrapper consumes it — including when you quit without choosing anything, so a stale
path can never hijack a later <kbd>Enter</kbd>.

---

## Requirements and dependencies

Two runtime dependencies, both pure-Python and cross-platform:

| Package | Why |
| --- | --- |
| [`textual`](https://github.com/Textualize/textual) | The terminal UI |
| [`platformdirs`](https://github.com/PlatformDirs/platformdirs) | Correct config location on every OS |

`tkinter` ships with CPython. [`customtkinter`](https://github.com/tomerfiliba-org/customtkinter)
is optional and purely cosmetic:

```bash
python3 -m pip install --user "cd-tui[gui]"
```

The installers never touch the network except PyPI and GitHub, and never fetch anything
over plain HTTP.

---

## Uninstalling

```bash
python3 -m pip uninstall cd-tui
```

Then delete the marker block from your shell config, and optionally
`rm -rf ~/.config/cd-tui`.

---

## Development

```bash
git clone https://github.com/Xznder1984/CD-TUI.git
cd CD-TUI
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

ruff check .
ruff format --check .
mypy
pytest
```

The test suite runs headless and needs no terminal or display:

| File | Covers |
| --- | --- |
| `tests/test_config.py` | schema, atomic writes, permissions, the target file, cross-platform paths |
| `tests/test_contrast.py` | every foreground/background pair in both palettes against WCAG AA |
| `tests/test_tui.py` | the Textual app through its `run_test` pilot: keys, empty state, error banner, reload, settings launcher |
| `tests/test_gui.py` | the settings panel against both toolkits: add/rename/repoint/reorder/delete, focus traversal, dialogs |
| `tests/test_installers.py` | the two installer scripts: source invariants, plus a real PowerShell install-and-wrapper run |

### The installer tests

`tests/test_installers.py` guards both installers. Most of it is plain source
inspection that needs nothing installed, because it pins down specific bugs that
shipped once and were caught only by running the installers for real: pip's
stderr being swallowed by a misplaced `2>&1`, a leaked `$ErrorActionPreference`
that killed the caller's shell, a wrapper pinned to a bare `python`, a hardcoded
`%APPDATA%` path, and so on.

When `pwsh` is on `PATH` it also runs
`tests/powershell/runtime_checks.ps1`, which installs the built wheel into a
throwaway venv inside a fake `$HOME`, then checks the wrapper block is written
once, that repeat installs are byte-identical, that `cdtui` hands the directory
over and consumes the target file, and that `irm | iex` leaves the caller's
session alone. It skips rather than fails when `pwsh` is missing, when no wheel
has been built, or when there is no package index to seed the venv from. To run
it against an interpreter you already trust:

```bash
CDTUI_TEST_PYTHON="$PWD/.venv/bin/python" pytest tests/test_installers.py
```

The layout is small on purpose:

```
src/cdtui/
    __init__.py   version and shared constants
    config.py     paths, schema, atomic read/write, the target file
    tui.py        the Textual application and its contrast-audited palette
    gui.py        the settings window, on tkinter or CustomTkinter
    main.py       argument parsing and process exit codes
    app.py        the `python -m cdtui.app` entry point the wrappers call

tests/
    test_config.py, test_contrast.py, test_tui.py, test_gui.py
```

`config.py` holds all of the logic that has no user interface, so it can be tested
without a terminal or a display.

---

## License

[MIT](LICENSE)
