"""``python -m cdtui.app`` entry point.

The shell wrappers installed by ``install.sh`` and ``install.ps1`` invoke the
TUI through this module, so that the console-script shim on ``PATH`` is not
required:

    cdtui() { python3 -m cdtui.app "$@"; ... }

This module simply forwards to :func:`cdtui.main.main`.
"""

from __future__ import annotations

from cdtui.main import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
