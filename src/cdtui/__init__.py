"""CD-TUI: a terminal directory bookmarking and navigation tool.

The package provides two front ends over a single JSON bookmark store:

* :mod:`cdtui.tui` -- a full-screen Textual interface for picking a directory.
* :mod:`cdtui.gui` -- a Tkinter (or CustomTkinter) panel for managing bookmarks.

Because a child process cannot change the working directory of the shell that
launched it, the TUI writes the selected path to a small handoff file
(``~/.cd_tui_target``).  The ``cdtui`` shell wrapper function installed by
``install.sh`` / ``install.ps1`` reads that file, clears the screen and performs
the real ``cd`` back in the user's interactive shell.
"""

from __future__ import annotations

__all__ = ["APP_NAME", "TARGET_FILE_NAME", "__version__"]

#: Distribution version.  Kept in sync with ``pyproject.toml`` by hand.
__version__ = "1.0.1"

#: Human readable application name used in titles and messages.
APP_NAME = "CD-TUI"

#: Name of the handoff file written to the user's home directory on selection.
TARGET_FILE_NAME = ".cd_tui_target"
