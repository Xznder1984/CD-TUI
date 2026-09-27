"""Textual front end for CD-TUI.

Layout is a fixed header, a full-height scrollable list of bookmarks and a
footer that mirrors the live key bindings.  Every action is reachable from the
keyboard alone.

Colour choices are *not* left to Textual's generated theme.  The palette below
is hand-picked and audited: :data:`AUDITED_PAIRS` lists every foreground /
background combination used by the interface together with the WCAG minimum it
must clear, and :func:`contrast_ratio` recomputes those ratios so
``tests/test_contrast.py`` can prove the audit still holds.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Sequence
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Any, ClassVar, Final

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from cdtui import APP_NAME, __version__
from cdtui.config import (
    Bookmark,
    ConfigError,
    clear_target_file,
    effective_config_path,
    load_bookmarks,
    write_target_file,
)

__all__ = [
    "DARK_TOKENS",
    "LIGHT_TOKENS",
    "BookmarkListView",
    "BookmarkRow",
    "CDTUIApp",
    "build_tokens",
    "contrast_ratio",
    "run_tui",
]

# ---------------------------------------------------------------------------
# Accessible colour palette
# ---------------------------------------------------------------------------

#: Design tokens for dark terminals.  Foreground/background pairs taken from
#: here are listed in :data:`AUDITED_PAIRS`.
DARK_TOKENS: Final[dict[str, str]] = {
    "cdtui-bg": "#12141c",
    "cdtui-surface": "#181b26",
    "cdtui-row": "#181b26",
    "cdtui-row-hover": "#222736",
    "cdtui-text": "#f2f4f8",
    "cdtui-text-dim": "#a9b2c3",
    "cdtui-sel-bg": "#2b5fd9",
    "cdtui-sel-text": "#ffffff",
    "cdtui-sel-dim": "#e6ecff",
    "cdtui-accent": "#7fb2ff",
    "cdtui-border": "#5d6884",
    "cdtui-warn": "#ffb454",
    "cdtui-header-bg": "#1b1f2c",
    "cdtui-header-fg": "#f2f4f8",
    "cdtui-footer-bg": "#1b1f2c",
    "cdtui-footer-fg": "#c3cad9",
    "cdtui-footer-key-bg": "#2b5fd9",
    "cdtui-footer-key-fg": "#ffffff",
    # Textual built-ins that must also clear the audit bar.
    "panel": "#1b1f2c",
    "foreground": "#f2f4f8",
    "background": "#12141c",
    "surface": "#181b26",
    "primary": "#7fb2ff",
    "block-cursor-background": "#2b5fd9",
    "block-cursor-foreground": "#ffffff",
    "block-cursor-blurred-background": "#2a3145",
    "block-cursor-blurred-foreground": "#e6ecff",
    "block-hover-background": "#222736",
    "footer-background": "#1b1f2c",
    "footer-foreground": "#c3cad9",
    "footer-item-background": "#1b1f2c",
    "footer-key-background": "#2b5fd9",
    "footer-key-foreground": "#ffffff",
    "footer-description-background": "#1b1f2c",
    "footer-description-foreground": "#c3cad9",
}

#: Design tokens for light terminals.
LIGHT_TOKENS: Final[dict[str, str]] = {
    "cdtui-bg": "#f4f6fa",
    "cdtui-surface": "#ffffff",
    "cdtui-row": "#ffffff",
    "cdtui-row-hover": "#e8ecf5",
    "cdtui-text": "#141821",
    "cdtui-text-dim": "#4b5364",
    "cdtui-sel-bg": "#1a4fd0",
    "cdtui-sel-text": "#ffffff",
    "cdtui-sel-dim": "#e6ecff",
    "cdtui-accent": "#0b3fa8",
    "cdtui-border": "#6b7488",
    "cdtui-warn": "#7a4a00",
    "cdtui-header-bg": "#e6eaf2",
    "cdtui-header-fg": "#141821",
    "cdtui-footer-bg": "#e6eaf2",
    "cdtui-footer-fg": "#333b4d",
    "cdtui-footer-key-bg": "#1a4fd0",
    "cdtui-footer-key-fg": "#ffffff",
    "panel": "#e6eaf2",
    "foreground": "#141821",
    "background": "#f4f6fa",
    "surface": "#ffffff",
    "primary": "#0b3fa8",
    "block-cursor-background": "#1a4fd0",
    "block-cursor-foreground": "#ffffff",
    "block-cursor-blurred-background": "#c9d4ee",
    "block-cursor-blurred-foreground": "#141821",
    "block-hover-background": "#e8ecf5",
    "footer-background": "#e6eaf2",
    "footer-foreground": "#333b4d",
    "footer-item-background": "#e6eaf2",
    "footer-key-background": "#1a4fd0",
    "footer-key-foreground": "#ffffff",
    "footer-description-background": "#e6eaf2",
    "footer-description-foreground": "#333b4d",
}

#: Every foreground/background pair rendered by the interface, with the WCAG
#: success-criterion minimum it must satisfy.  ``4.5`` is AA for body text;
#: ``3.0`` is the AA minimum for non-text UI components such as borders.
AUDITED_PAIRS: Final[tuple[tuple[str, str, str, float], ...]] = (
    ("body text on row", "cdtui-text", "cdtui-row", 4.5),
    ("dimmed path on row", "cdtui-text-dim", "cdtui-row", 4.5),
    ("body text on hovered row", "cdtui-text", "cdtui-row-hover", 4.5),
    ("dimmed path on hovered row", "cdtui-text-dim", "cdtui-row-hover", 4.5),
    ("selected alias on highlight", "cdtui-sel-text", "cdtui-sel-bg", 4.5),
    ("selected path on highlight", "cdtui-sel-dim", "cdtui-sel-bg", 4.5),
    ("body text on app background", "cdtui-text", "cdtui-bg", 4.5),
    ("dimmed path on app background", "cdtui-text-dim", "cdtui-bg", 4.5),
    ("accent on surface", "cdtui-accent", "cdtui-surface", 4.5),
    ("warning on surface", "cdtui-warn", "cdtui-surface", 4.5),
    ("header text on header background", "cdtui-header-fg", "cdtui-header-bg", 4.5),
    ("footer text on footer background", "cdtui-footer-fg", "cdtui-footer-bg", 4.5),
    (
        "footer key label on key pill",
        "cdtui-footer-key-fg",
        "cdtui-footer-key-bg",
        4.5,
    ),
    ("row border on surface (non-text UI)", "cdtui-border", "cdtui-surface", 3.0),
    ("highlight bar on surface (non-text UI)", "cdtui-accent", "cdtui-surface", 3.0),
)


# Textual declares BINDINGS as a list of Binding | 2-tuple | 3-tuple, and
# lists are invariant, so the annotation has to match it exactly.
_BindingSpec = list[Binding | tuple[str, str] | tuple[str, str, str]]


def _srgb_to_linear(channel: float) -> float:
    """Convert one 0-255 sRGB channel to linear light."""
    value: float = channel / 255.0
    if value <= 0.03928:
        return value / 12.92
    return float(((value + 0.055) / 1.055) ** 2.4)


def _relative_luminance(hex_color: str) -> float:
    """Return the WCAG relative luminance of a ``#rrggbb`` colour."""
    text = hex_color.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"expected a #rrggbb colour, got {hex_color!r}")
    red, green, blue = (int(text[offset : offset + 2], 16) for offset in (0, 2, 4))
    return (
        0.2126 * _srgb_to_linear(red)
        + 0.7152 * _srgb_to_linear(green)
        + 0.0722 * _srgb_to_linear(blue)
    )


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG contrast ratio between two ``#rrggbb`` colours.

    Args:
        foreground: Text colour.
        background: Background colour.

    Returns:
        A ratio in the range ``1.0`` (identical) to ``21.0`` (black on white).
    """
    first = _relative_luminance(foreground)
    second = _relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def build_tokens(dark: bool) -> dict[str, str]:
    """Return the CSS custom properties for a light or dark terminal.

    Args:
        dark: ``True`` for the dark palette, ``False`` for the light one.
    """
    return dict(DARK_TOKENS if dark else LIGHT_TOKENS)


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

#: Glyph shown on the focused row.  Deliberately a *shape* change as well as a
#: colour change, so the focus indicator does not rely on colour alone.
MARKER_SELECTED: Final[str] = "▸"
MARKER_IDLE: Final[str] = " "


class BookmarkRow(ListItem):
    """One line of the list: focus marker, bold alias, dimmed absolute path.

    The class also carries the :class:`~cdtui.config.Bookmark` it represents so
    that the app never has to keep a side table mapping rows back to data.
    """

    def __init__(self, bookmark: Bookmark) -> None:
        super().__init__()
        self.bookmark = bookmark

    def compose(self) -> ComposeResult:
        """Build the marker/alias/path row."""
        with Horizontal(classes="row-line"):
            yield Label(MARKER_IDLE, classes="row-marker")
            yield Label(self.bookmark.alias, classes="row-alias")
            yield Label(self.bookmark.path, classes="row-path")

    def watch_highlighted(self, value: bool) -> None:
        """Swap the focus marker when the cursor moves onto or off this row."""
        super().watch_highlighted(value)
        if not self.is_mounted:
            return
        try:
            marker = self.query_one(".row-marker", Label)
        except NoMatches:  # pragma: no cover - only during teardown
            return
        marker.update(MARKER_SELECTED if value else MARKER_IDLE)


class BookmarkListView(ListView):
    """Scrollable list of :class:`BookmarkRow` items with vim-style navigation."""

    DEFAULT_CSS = """
    BookmarkListView {
        height: 1fr;
        background: $cdtui-bg;
        scrollbar-background: $cdtui-surface;
        scrollbar-color: $cdtui-border;
        scrollbar-color-hover: $cdtui-accent;
        scrollbar-color-active: $cdtui-accent;
    }
    """

    BINDINGS: ClassVar[_BindingSpec] = [
        Binding("j,down", "cursor_down", "Down", key_display="↓/j"),
        Binding("k,up", "cursor_up", "Up", key_display="↑/k"),
        Binding("home", "cursor_top", "Top", key_display="Home", show=False),
        Binding("end", "cursor_bottom", "Bottom", key_display="End", show=False),
        Binding("enter", "select_cursor", "Open", key_display="Enter"),
    ]

    def __init__(self, bookmarks: Sequence[Bookmark], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.bookmarks: list[Bookmark] = list(bookmarks)

    def build_rows(self) -> list[BookmarkRow]:
        """Return one :class:`BookmarkRow` per stored bookmark, in order."""
        return [BookmarkRow(bookmark) for bookmark in self.bookmarks]

    def bookmark_at(self, index: int | None) -> Bookmark | None:
        """Return the bookmark at ``index``, or ``None`` when out of range."""
        if index is None or not 0 <= index < len(self.bookmarks):
            return None
        return self.bookmarks[index]

    # -- Home/End move the cursor, they do not just scroll ---------------
    #
    # ListView's own scroll_home/scroll_end only move the scrollbar, which
    # would leave the focus marker (and therefore Enter) on a row the user
    # can no longer see.  These actions keep the selection and the viewport
    # in step.

    def action_cursor_top(self) -> None:
        """Move the cursor to the first bookmark."""
        if self.bookmarks:
            self.index = 0
        self.scroll_home(animate=False)

    def action_cursor_bottom(self) -> None:
        """Move the cursor to the last bookmark."""
        if self.bookmarks:
            self.index = len(self.bookmarks) - 1
        self.scroll_end(animate=False)


class EmptyState(Vertical):
    """Inline message shown in place of the list when there are no bookmarks."""

    def __init__(self, config_file: Path | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.config_file = config_file

    DEFAULT_CSS = """
    EmptyState {
        height: 1fr;
        width: 1fr;
        align-horizontal: center;
        align-vertical: middle;
        background: $cdtui-bg;
    }
    EmptyState > Static {
        width: auto;
        height: auto;
        content-align: center middle;
        text-align: center;
        color: $cdtui-text;
    }
    EmptyState > Static.hint {
        color: $cdtui-text-dim;
    }
    EmptyState > Static.spacer {
        height: 1;
    }
    """

    def compose(self) -> ComposeResult:
        """Render the empty-state copy."""
        yield Static("No folders bookmarked yet — press `a` to add one", classes="headline")
        yield Static("", classes="spacer")
        yield Static("then use ↑/↓ or j/k to pick one and press Enter to cd", classes="hint")
        yield Static("", classes="spacer")
        yield Static(
            f"bookmarks live in {effective_config_path(self.config_file)}",
            classes="hint",
        )


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class CDTUIApp(App[None]):
    """Interactive bookmark picker.

    Selecting a row (Enter) writes the directory to ``~/.cd_tui_target`` and
    exits; quitting with ``q`` deliberately leaves no target behind.
    """

    TITLE = APP_NAME
    SUB_TITLE = f"v{__version__}"

    CSS = """
    Screen {
        background: $cdtui-bg;
        layers: base;
    }

    Header {
        background: $cdtui-header-bg;
        color: $cdtui-header-fg;
    }

    Footer {
        background: $cdtui-footer-bg;
        color: $cdtui-footer-fg;
    }

    #error-banner {
        display: none;
        dock: top;
        height: auto;
        padding: 0 1;
        background: $cdtui-surface;
        color: $cdtui-warn;
    }

    #error-banner.-visible {
        display: block;
    }

    BookmarkListView {
        border-left: vkey $cdtui-border;
    }

    BookmarkListView > BookmarkRow {
        background: $cdtui-row;
        color: $cdtui-text;
        height: 1;
        padding: 0;

        &.-hovered {
            background: $cdtui-row-hover;
        }

        &.-highlight {
            background: $cdtui-sel-bg;
            border-left: thick $cdtui-accent;
        }
    }

    BookmarkListView:focus > BookmarkRow.-highlight {
        background: $cdtui-sel-bg;
        border-left: thick $cdtui-accent;
    }

    BookmarkRow .row-line {
        height: 1;
        width: 1fr;
    }

    BookmarkRow .row-marker {
        width: 2;
        height: 1;
        color: $cdtui-accent;
        text-style: bold;
    }

    BookmarkRow .row-alias {
        width: 22;
        height: 1;
        color: $cdtui-text;
        text-style: bold;
    }

    BookmarkRow .row-path {
        width: 1fr;
        height: 1;
        color: $cdtui-text-dim;
        text-style: none;
    }

    BookmarkListView > BookmarkRow.-highlight .row-marker {
        color: $cdtui-sel-text;
    }

    BookmarkListView > BookmarkRow.-highlight .row-alias {
        color: $cdtui-sel-text;
        text-style: bold;
    }

    BookmarkListView > BookmarkRow.-highlight .row-path {
        color: $cdtui-sel-dim;
    }
    """

    BINDINGS: ClassVar[_BindingSpec] = [
        Binding("q", "quit_app", "Quit"),
        Binding("a", "open_settings", "Settings"),
        Binding("r", "reload", "Reload"),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
    ]

    def __init__(self, config_file: Path | None = None) -> None:
        """Create the app.

        Args:
            config_file: Override for ``config.json``; used by the test suite.
        """
        super().__init__()
        self._config_file = config_file
        self._load_error: str | None = None
        self._bookmarks: list[Bookmark] = []
        self._settings_open = False

    # -- theming ----------------------------------------------------------

    def get_css_variables(self) -> dict[str, str]:
        """Inject the audited palette on top of Textual's generated theme."""
        base = super().get_css_variables()
        dark = bool(getattr(self.current_theme, "dark", True))
        return {**base, **build_tokens(dark)}

    # -- composition ------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Assemble header, error banner, list area and footer."""
        yield Header(show_clock=False)
        yield Static("", id="error-banner")
        yield BookmarkListView(self._bookmarks, id="bookmark-list")
        yield EmptyState(self._config_file, id="empty-state")
        yield Footer()

    async def on_mount(self) -> None:
        """Load bookmarks, paint the initial state and focus the list."""
        self._read_bookmarks(initial=True)
        await self._refresh_list(focus_first=True)

    def _read_bookmarks(self, *, initial: bool = False) -> None:
        """Load bookmarks from disk, recording any error for the banner."""
        try:
            self._bookmarks = load_bookmarks(self._config_file)
        except ConfigError as exc:
            self._bookmarks = []
            self._load_error = str(exc)
            if not initial:
                self.notify(str(exc), title="Config error", severity="error", timeout=10)
            return
        self._load_error = None

    async def _refresh_list(self, *, focus_first: bool = False) -> None:
        """Rebuild the list widget from :attr:`_bookmarks`."""
        list_view = self.query_one("#bookmark-list", BookmarkListView)
        empty_state = self.query_one("#empty-state", EmptyState)

        list_view.bookmarks = list(self._bookmarks)
        previous_index = list_view.index
        await list_view.clear()
        if list_view.bookmarks:
            await list_view.extend(list_view.build_rows())
            if not list_view.is_empty:
                if focus_first or previous_index is None:
                    list_view.index = 0
                else:
                    list_view.index = min(previous_index, len(list_view.bookmarks) - 1)
        list_view.display = bool(list_view.bookmarks)
        empty_state.display = not list_view.bookmarks

        banner = self.query_one("#error-banner", Static)
        if self._load_error:
            banner.update(f"Config error: {self._load_error}")
            banner.add_class("-visible")
        else:
            banner.update("")
            banner.remove_class("-visible")

        self.sub_title = (
            f"v{__version__} · {len(self._bookmarks)} "
            f"{'folder' if len(self._bookmarks) == 1 else 'folders'}"
        )

        if list_view.display and not list_view.has_focus:
            list_view.focus()

    # -- actions ----------------------------------------------------------

    def action_quit_app(self) -> None:
        """Quit without choosing anything.

        Any stale handoff file from a previous run is removed so the shell
        wrapper cannot ``cd`` somewhere the user did not just pick.
        """
        try:
            clear_target_file()
        except ConfigError as exc:  # pragma: no cover - home dir should be writable
            self.notify(str(exc), title="Warning", severity="warning")
        self.exit()

    async def action_reload(self) -> None:
        """Re-read ``config.json`` from disk."""
        self._read_bookmarks()
        await self._refresh_list()
        self.notify(f"Reloaded {len(self._bookmarks)} bookmarks.", title="")

    def action_choose_bookmark(self) -> None:
        """Open the highlighted bookmark: write the target file and exit."""
        list_view = self.query_one("#bookmark-list", BookmarkListView)
        bookmark = list_view.bookmark_at(list_view.index)
        if bookmark is None:
            self.notify("Nothing selected.", title="", severity="warning")
            return
        try:
            write_target_file(bookmark.path)
        except ConfigError as exc:
            self.notify(str(exc), title="Cannot open folder", severity="error", timeout=10)
            return
        self.exit()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle Enter (or a click) on a list row."""
        event.stop()
        self.action_choose_bookmark()

    async def action_open_settings(self) -> None:
        """Launch the Tkinter settings panel as a child process.

        The panel is a separate process so that Tk's event loop never fights
        with Textual's.  While it runs, the TUI is suspended so the two do not
        fight over the terminal, and the bookmark list is reloaded afterwards
        so edits made in the panel appear immediately.
        """
        if self._settings_open:
            return
        self._settings_open = True
        self.notify("Opening settings…", title="")
        try:
            return_code = await self._run_settings_panel()
        finally:
            self._settings_open = False

        self._read_bookmarks()
        await self._refresh_list()
        if return_code == 0:
            self.notify("Settings saved.", title="")
        else:
            self.notify(
                "The settings panel could not start. Run `cdtui --settings` "
                "from a terminal to see the full error.",
                title="Settings error",
                severity="error",
                timeout=10,
            )

    async def _run_settings_panel(self) -> int:
        """Run ``python -m cdtui.app --settings`` and wait for it to finish.

        The TUI is suspended for the duration so the child GUI and Textual do
        not fight over the terminal.  Suspension is best effort: it is not
        supported in every environment (headless drivers, some CI runners), and
        losing it is not a reason to refuse to open the settings panel.
        """
        command = [sys.executable, "-m", "cdtui.app", "--settings"]
        environment = dict(os.environ)
        # Make sure the child can import *this* copy of cdtui even when the
        # app is being run straight from a source checkout.
        source_root = str(Path(__file__).resolve().parent.parent)
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = (
            f"{source_root}{os.pathsep}{existing}" if existing else source_root
        )

        with ExitStack() as stack:
            with suppress(Exception):
                stack.enter_context(self.suspend())
            try:
                process = await asyncio.create_subprocess_exec(*command, env=environment)
            except OSError as exc:
                self.notify(f"cannot start settings panel: {exc}", title="", severity="error")
                return 1
            return await process.wait()


def run_tui(config_file: Path | None = None) -> int:
    """Run the interactive bookmark picker.

    Args:
        config_file: Override for ``config.json``; used by the test suite.

    Returns:
        ``0``.  The shell wrapper distinguishes "chose something" from "quit" by
        looking for ``~/.cd_tui_target``, not by the return code.
    """
    # App[None].run() always returns None; the app never sets an exit result.
    CDTUIApp(config_file=config_file).run()
    return 0
