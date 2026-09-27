"""Command line entry point for CD-TUI.

Two modes:

* ``cdtui`` -- run the Textual bookmark picker (the default).
* ``cdtui --settings`` -- open the Tkinter bookmark management panel.

``cdtui --help`` and ``cdtui --version`` round out the interface.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from cdtui import APP_NAME, __version__
from cdtui.config import describe_config_location

__all__ = ["build_parser", "main"]

_EPILOG = """\
examples:
  cdtui                 pick a bookmarked folder and cd into it
  cdtui --settings      add, rename, reorder or delete bookmarks

CD-TUI cannot change your shell's working directory by itself: a child process
has no effect on its parent. It writes the folder you choose to
~/.cd_tui_target and exits, and the `cdtui` shell wrapper installed by
install.sh / install.ps1 performs the actual cd. Run the wrapper, not
`python -m cdtui.app`, for navigation to work.
"""


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``cdtui`` command."""
    parser = argparse.ArgumentParser(
        prog="cdtui",
        description=f"{APP_NAME}: a terminal directory bookmarking and navigation tool.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="open the graphical bookmark settings panel instead of the TUI",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="open the Textual interface explicitly (this is already the default)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "use an alternative config.json instead of the default location "
            f"({describe_config_location(None).split(': ', 1)[1]})"
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"{APP_NAME} {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run CD-TUI.

    Args:
        argv: Command line arguments, excluding the program name.  Defaults to
            :data:`sys.argv` when omitted.

    Returns:
        A process exit code suitable for ``sys.exit``.
    """
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    if args.settings and args.tui:
        print("cdtui: --settings and --tui are mutually exclusive.", file=sys.stderr)
        return 2

    if args.settings:
        from cdtui.gui import run_settings_panel

        return run_settings_panel(config_file=args.config)

    try:
        from cdtui.tui import run_tui
    except ImportError as exc:
        print(
            f"cdtui: the Textual interface is unavailable ({exc}).\n"
            "Install the dependencies with:\n"
            "    python3 -m pip install --user textual platformdirs",
            file=sys.stderr,
        )
        return 1

    return run_tui(config_file=args.config)


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m cdtui.app`
    raise SystemExit(main())
