"""Tkinter (or CustomTkinter) settings panel for managing CD-TUI bookmarks.

Launched by ``cdtui --settings`` or by pressing ``a`` inside the TUI.

The panel is intentionally a *settings* surface rather than a second browser:
add a folder, rename it, point it somewhere else, reorder it, delete it.  Every
mutating action writes ``config.json`` immediately and reports the outcome in an
inline status line, so there is no hidden "unsaved changes" state and no silent
failure.

If :mod:`customtkinter` is installed the panel picks up its theming
automatically; otherwise it falls back to plain :mod:`tkinter` with a native
look.  Both code paths create the same widgets and behave identically.
"""

from __future__ import annotations

import importlib
import sys
import tkinter
import tkinter.filedialog
import tkinter.messagebox
import tkinter.ttk as ttk
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from cdtui import APP_NAME, __version__
from cdtui.config import (
    Bookmark,
    ConfigError,
    add_bookmark,
    default_alias,
    effective_config_path,
    load_bookmarks,
    move_bookmark,
    remove_bookmark,
    rename_bookmark,
    replace_bookmark_path,
    save_bookmarks,
)

__all__ = ["SettingsPanel", "Toolkit", "resolve_toolkit", "run_settings_panel"]

# --- Accessible light palette (contrast audited, see tests/test_contrast.py) --
BG: Final[str] = "#f4f6fa"
SURFACE: Final[str] = "#ffffff"
TEXT: Final[str] = "#141821"
TEXT_DIM: Final[str] = "#4b5364"
BORDER: Final[str] = "#6b7488"
SELECT_BG: Final[str] = "#1a4fd0"
SELECT_FG: Final[str] = "#ffffff"
SELECT_DIM_BG: Final[str] = "#dfe7fb"
OK_TEXT: Final[str] = "#0f6b2a"
ERROR_TEXT: Final[str] = "#a4161a"
WARN_TEXT: Final[str] = "#7a4a00"

_STATUS_COLORS: Final[dict[str, str]] = {
    "info": TEXT_DIM,
    "ok": OK_TEXT,
    "warn": WARN_TEXT,
    "error": ERROR_TEXT,
}

#: Average advance width of a character in Tk's default font, in pixels.  Only
#: used to translate the character-based widths below into the pixel widths
#: CustomTkinter expects.
_CHAR_PX: Final[int] = 8
_CHAR_PADDING_PX: Final[int] = 14
_ROW_BUTTON_PX: Final[int] = 84
_ROW_MOVE_BUTTON_PX: Final[int] = 74


def _char_width(chars: int) -> int:
    """Convert a character count into CustomTkinter's pixel width."""
    return chars * _CHAR_PX + _CHAR_PADDING_PX


# ---------------------------------------------------------------------------
# Toolkit detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Toolkit:
    """The widget classes in use, resolved once when the panel is created.

    Attributes:
        module: ``customtkinter`` or ``tkinter``.
        root: Top level window class.
        toplevel: Secondary window class, used for the rename prompt. This has
            to come from the same toolkit as ``root`` so the dialog honours
            ``fg_color`` and the rest of the themed options.
        frame: Container class.
        label: Static text class.
        entry: Single line text input class.
        button: Clickable button class.
        canvas: Scrollable canvas class (always ``tkinter.Canvas``).
        filedialog: ``tkinter.filedialog`` module, used for the folder picker.
        messagebox: ``tkinter.messagebox`` module, used for confirmations.
        is_custom: ``True`` when CustomTkinter is driving the look.
    """

    module: ModuleType
    root: type
    toplevel: type
    frame: type
    label: type
    entry: type
    button: type
    canvas: type
    filedialog: ModuleType
    messagebox: ModuleType
    is_custom: bool


def resolve_toolkit() -> Toolkit:
    """Return the GUI toolkit to use, preferring CustomTkinter when present.

    Any import failure (missing package, broken install) is treated as
    "unavailable" so the caller always gets a working plain-Tkinter panel, or a
    clear error from :func:`run_settings_panel`.
    """
    ctk: ModuleType | None
    try:
        ctk = importlib.import_module("customtkinter")
    except Exception:
        ctk = None

    if ctk is not None:
        try:
            # Pin the appearance so the hard coded, contrast-audited foreground
            # colours below stay readable regardless of the OS theme.
            ctk.set_appearance_mode("Light")
            ctk.set_default_color_theme("blue")
            custom = Toolkit(
                module=ctk,
                root=ctk.CTk,
                toplevel=ctk.CTkToplevel,
                frame=ctk.CTkFrame,
                label=ctk.CTkLabel,
                entry=ctk.CTkEntry,
                button=ctk.CTkButton,
                canvas=tkinter.Canvas,
                filedialog=tkinter.filedialog,
                messagebox=tkinter.messagebox,
                is_custom=True,
            )
        except Exception:
            ctk = None
        else:
            return custom

    return Toolkit(
        module=tkinter,
        root=tkinter.Tk,
        toplevel=tkinter.Toplevel,
        frame=tkinter.Frame,
        label=tkinter.Label,
        entry=tkinter.Entry,
        button=tkinter.Button,
        canvas=tkinter.Canvas,
        filedialog=tkinter.filedialog,
        messagebox=tkinter.messagebox,
        is_custom=False,
    )


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------


class SettingsPanel:
    """Bookmark management window.

    Args:
        config_file: Override for ``config.json``; used by the test suite.
    """

    def __init__(self, config_file: Path | None = None) -> None:
        self._config_file = config_file
        self._tk = resolve_toolkit()
        self._bookmarks: list[Bookmark] = []
        self._focus_order: list[Any] = []
        self._row_focusables: list[list[Any]] = []
        self._root: Any = None
        self._alias_var: Any = None
        self._path_var: Any = None
        self._status_var: Any = None
        self._status_label: Any = None
        self._alias_entry: Any = None
        self._path_entry: Any = None
        self._browse_button: Any = None
        self._add_button: Any = None
        self._done_button: Any = None
        self._canvas: Any = None
        self._rows_frame: Any = None
        self._rows_window: Any = None

    # -- toolkit helpers --------------------------------------------------

    def _make_label(
        self, parent: Any, text: str = "", *, width_chars: int | None = None, **kwargs: Any
    ) -> Any:
        """Create a label, translating colours and widths for the toolkit.

        Args:
            parent: Container to parent the label to.
            text: Text to display.
            width_chars: Requested width in characters; ignored when ``None``.
            **kwargs: Forwarded to the toolkit widget class.
        """
        options = dict(kwargs)
        if self._tk.is_custom:
            options["fg_color"] = options.pop("bg", "transparent")
            options["text_color"] = options.pop("fg", TEXT)
            if width_chars is not None:
                options["width"] = _char_width(width_chars)
        else:
            if width_chars is not None:
                options["width"] = width_chars
        return self._tk.label(parent, text=text, **options)

    def _make_entry(self, parent: Any, *, width_chars: int | None = None, **kwargs: Any) -> Any:
        """Create a single line entry, translating colours and widths.

        Args:
            parent: Container to parent the entry to.
            width_chars: Requested width in characters; ignored when ``None``.
            **kwargs: Forwarded to the toolkit widget class.
        """
        options = dict(kwargs)
        if self._tk.is_custom:
            options["fg_color"] = options.pop("bg", SURFACE)
            options["text_color"] = options.pop("fg", TEXT)
            options["border_color"] = options.pop("highlightbackground", BORDER)
            options["border_width"] = 1
            options.setdefault("height", 28)
            if width_chars is not None:
                options["width"] = _char_width(width_chars)
        else:
            options.setdefault("relief", "solid")
            options.setdefault("highlightthickness", 1)
            options.setdefault("insertbackground", TEXT)
            if width_chars is not None:
                options["width"] = width_chars
        return self._tk.entry(parent, **options)

    def _make_button(
        self,
        parent: Any,
        text: str,
        *,
        command: Any = None,
        kind: str = "normal",
        width_chars: int | None = None,
        width_px: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Create a button.

        Args:
            parent: Container to parent the button to.
            text: Button caption.
            command: Callback invoked on activation.
            kind: ``normal``, ``primary``, ``danger`` or ``quiet``; selects the
                colour pair from the audited palette.
            width_chars: Width in characters (interpreted per toolkit).
            width_px: Explicit pixel width, overriding ``width_chars``.
            **kwargs: Forwarded to the toolkit widget class.
        """
        options = dict(kwargs)
        background, foreground = _BUTTON_COLORS[kind]
        if self._tk.is_custom:
            options["fg_color"] = options.pop("bg", background)
            options["text_color"] = options.pop("fg", foreground)
            options["hover_color"] = options.pop("activebackground", background)
            options.setdefault("corner_radius", 4)
            options.setdefault("height", 28)
            if width_px is not None:
                options["width"] = width_px
            elif width_chars is not None:
                options["width"] = _char_width(width_chars)
        else:
            options["bg"] = options.pop("bg", background)
            options["fg"] = options.pop("fg", foreground)
            options["activebackground"] = options.pop("activebackground", background)
            options["activeforeground"] = options.pop("activeforeground", foreground)
            options.setdefault("relief", "raised")
            options.setdefault("highlightthickness", 1)
            options.setdefault("highlightbackground", BORDER)
            options.setdefault("bd", 1)
            if width_px is not None:
                options["width"] = max(width_px // _CHAR_PX, 3)
            elif width_chars is not None:
                options["width"] = width_chars
        return self._tk.button(parent, text=text, command=command, **options)

    def _make_frame(self, parent: Any, **kwargs: Any) -> Any:
        """Create a container, translating colours for the toolkit."""
        options = dict(kwargs)
        if self._tk.is_custom:
            options["fg_color"] = options.pop("bg", BG)
        return self._tk.frame(parent, **options)

    # -- status -----------------------------------------------------------

    def _set_status(self, message: str, kind: str = "info") -> None:
        """Show an inline status message.

        Args:
            message: Text to display.
            kind: ``info``, ``ok``, ``warn`` or ``error``; selects the colour.
        """
        self._status_var.set(message)
        if self._tk.is_custom:
            self._status_label.configure(text_color=_STATUS_COLORS[kind])
        else:
            self._status_label.configure(fg=_STATUS_COLORS[kind])

    # -- construction -----------------------------------------------------

    def _build_ui(self) -> None:
        """Create the whole widget tree."""
        self._root = self._tk.root()
        self._root.title(f"{APP_NAME} — Settings (v{__version__})")
        self._root.geometry("880x580")
        self._root.minsize(700, 440)
        self._configure_background(self._root, BG)
        self._root.protocol("WM_DELETE_WINDOW", self._on_done)

        self._alias_var = tkinter.StringVar()
        self._path_var = tkinter.StringVar()
        self._status_var = tkinter.StringVar(
            value=(
                f"Changes are saved to {effective_config_path(self._config_file)} as you make them."
            )
        )

        outer = self._make_frame(self._root, bg=BG)
        outer.pack(fill="both", expand=True, padx=14, pady=12)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)

        self._build_heading(outer)
        self._build_add_form(outer)
        self._build_status(outer)
        self._build_list(outer)
        self._build_bottom_bar(outer)
        self._bind_global_keys()

    def _configure_background(self, widget: Any, color: str) -> None:
        """Paint a widget's background using whichever option it understands."""
        if self._tk.is_custom:
            widget.configure(fg_color=color)
        else:
            widget.configure(bg=color)

    def _build_heading(self, parent: Any) -> None:
        """Create the title label."""
        self._make_label(
            parent,
            f"{APP_NAME} settings",
            bg=BG,
            fg=TEXT,
            font=("TkDefaultFont", 15, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

    def _build_add_form(self, parent: Any) -> None:
        """Create the labelled alias/folder inputs and the Add button."""
        form = self._make_frame(parent, bg=BG)
        form.grid(row=1, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        alias_label = self._make_label(form, "Alias", bg=BG, fg=TEXT, anchor="w", width_chars=8)
        alias_label.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 6))
        self._alias_entry = self._make_entry(
            form, textvariable=self._alias_var, bg=SURFACE, fg=TEXT
        )
        self._alias_entry.grid(row=0, column=1, sticky="ew", pady=(0, 6))
        self._link_label(alias_label, self._alias_entry)

        path_label = self._make_label(form, "Folder", bg=BG, fg=TEXT, anchor="w", width_chars=8)
        path_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(0, 6))
        self._path_entry = self._make_entry(form, textvariable=self._path_var, bg=SURFACE, fg=TEXT)
        self._path_entry.grid(row=1, column=1, sticky="ew", pady=(0, 6))
        self._link_label(path_label, self._path_entry)

        self._browse_button = self._make_button(
            form, "Browse…", command=self._on_browse, width_chars=10
        )
        self._browse_button.grid(row=1, column=2, padx=(8, 0), pady=(0, 6))

        self._add_button = self._make_button(
            form, "Add Folder", command=self._on_add, kind="primary", width_chars=12
        )
        self._add_button.grid(row=0, column=2, rowspan=2, padx=(8, 0), sticky="ns")

        for entry in (self._alias_entry, self._path_entry):
            entry.bind("<Control-Return>", self._on_ctrl_return)
            entry.bind("<KP_Enter>", self._on_ctrl_return)
        self._path_entry.bind("<Return>", self._on_ctrl_return)

    def _link_label(self, label: Any, entry: Any) -> None:
        """Associate a ``<Label>`` with its ``<Entry>`` for assistive tech.

        ``labelwidget`` is what screen readers use to announce the purpose of a
        field; the grid positioning above already gives the visual association.
        Toolkit builds that do not support the option degrade silently because
        the visible ``<Label>`` still conveys the same information.
        """
        with suppress(Exception):
            entry.configure(labelwidget=label)

    def _build_status(self, parent: Any) -> None:
        """Create the inline status line."""
        self._status_label = self._make_label(
            parent,
            textvariable=self._status_var,
            bg=BG,
            fg=TEXT_DIM,
            anchor="w",
            justify="left",
            wraplength=820,
        )
        self._status_label.grid(row=2, column=0, sticky="ew", pady=(8, 10))

    def _build_list(self, parent: Any) -> None:
        """Create the heading and the scrollable bookmark list."""
        self._make_label(
            parent,
            "Saved folders",
            bg=BG,
            fg=TEXT,
            font=("TkDefaultFont", 11, "bold"),
            anchor="w",
        ).grid(row=3, column=0, sticky="w", pady=(0, 4))

        list_frame = self._make_frame(parent, bg=BG)
        list_frame.grid(row=4, column=0, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self._canvas = self._tk.canvas(
            list_frame,
            bg=SURFACE,
            highlightthickness=1,
            highlightbackground=BORDER,
            bd=0,
        )
        self._canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self._canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self._rows_frame = self._make_frame(self._canvas, bg=SURFACE)
        self._rows_window = self._canvas.create_window((0, 0), window=self._rows_frame, anchor="nw")
        self._rows_frame.bind("<Configure>", self._on_rows_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.bind_all(sequence, self._on_mousewheel, add="+")

    def _build_bottom_bar(self, parent: Any) -> None:
        """Create the keyboard hint and the Done button."""
        bottom = self._make_frame(parent, bg=BG)
        bottom.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)

        self._make_label(
            bottom,
            "Tab / Shift-Tab move through the form, then the rows, then Done. "
            "Esc closes. Ctrl-Enter adds the folder above.",
            bg=BG,
            fg=TEXT_DIM,
            anchor="w",
            justify="left",
            wraplength=700,
        ).grid(row=0, column=0, sticky="w")

        self._done_button = self._make_button(
            bottom, "Done", command=self._on_done, kind="primary", width_chars=8
        )
        self._done_button.grid(row=0, column=1, sticky="e")

    def _bind_global_keys(self) -> None:
        """Install the application-wide key bindings."""
        self._root.bind_all("<Tab>", lambda _event: self._cycle_focus(False), add="+")
        self._root.bind_all("<Shift-Tab>", lambda _event: self._cycle_focus(True), add="+")
        self._root.bind_all("<ISO_Left_Tab>", lambda _event: self._cycle_focus(True), add="+")
        self._root.bind_all("<Escape>", lambda _event: self._on_done(), add="+")

    def _on_ctrl_return(self, _event: Any) -> str:
        """Treat Ctrl-Enter / Enter in the form as "Add Folder"."""
        self._on_add()
        return "break"

    def _on_rows_configure(self, _event: Any) -> None:
        """Keep the scroll region in step with the rows frame size."""
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event: Any) -> None:
        """Stretch the rows frame to the canvas width on resize."""
        self._canvas.itemconfigure(self._rows_window, width=event.width)

    def _on_mousewheel(self, event: Any) -> str:
        """Scroll the bookmark list with the mouse wheel."""
        if self._canvas is None:
            return "break"
        number = getattr(event, "num", None)
        if number == 4:
            self._canvas.yview_scroll(-3, "units")
        elif number == 5:
            self._canvas.yview_scroll(3, "units")
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            self._canvas.yview_scroll(-(delta // 120) if delta else 0, "units")
        return "break"

    # -- focus cycling ----------------------------------------------------

    def _rebuild_focus_order(self) -> None:
        """Recompute the Tab traversal order: form, then rows, then Done.

        Tk walks widgets in creation order, which a list that is rebuilt after
        every edit cannot satisfy.  Driving traversal from an explicit list is
        what makes Tab flow logically top-to-bottom.
        """
        order: list[Any] = [
            self._alias_entry,
            self._path_entry,
            self._browse_button,
            self._add_button,
        ]
        for row_widgets in self._row_focusables:
            order.extend(widget for widget in row_widgets if self._is_focusable(widget))
        order.append(self._done_button)
        self._focus_order = [widget for widget in order if self._is_focusable(widget)]

    @staticmethod
    def _is_focusable(widget: Any) -> bool:
        """Return ``True`` when ``widget`` exists and accepts keyboard focus."""
        if widget is None:
            return False
        try:
            if not widget.winfo_exists():
                return False
            return str(widget.cget("state")) != "disabled"
        except tkinter.TclError:  # pragma: no cover - destroyed or exotic widget
            return False

    @staticmethod
    def _widget_path(widget: Any) -> str:
        """Return a widget's Tk path name, or ``""`` if it is unusable.

        ``str(widget)`` is the widget path in every supported CPython version
        (``Misc.__str__`` returns ``self._w``); ``winfo_pathname`` is avoided
        because its signature changed in Python 3.14.
        """
        try:
            return str(widget._w)
        except Exception:
            return ""

    def _index_of_focus(self, order: list[Any]) -> int:
        """Return the position in ``order`` of the widget that holds focus.

        CustomTkinter widgets are composite: ``focus_set()`` on a ``CTkEntry``
        lands on the inner Tk ``Entry``, whose path is a *child* of the CTk
        widget's path.  Matching on the path prefix therefore resolves both
        plain Tk widgets (equal paths) and CustomTkinter wrappers (prefix).
        """
        try:
            focused = self._root.focus_get()
        except tkinter.TclError:  # pragma: no cover - no window focused yet
            return -1
        if focused is None:
            return -1
        path = self._widget_path(focused)
        if not path:
            return -1
        for position, widget in enumerate(order):
            candidate = self._widget_path(widget)
            if candidate and (path == candidate or path.startswith(f"{candidate}.")):
                return position
        return -1

    def _cycle_focus(self, backwards: bool) -> str:
        """Move focus one step through :attr:`_focus_order`, wrapping around."""
        order = [widget for widget in self._focus_order if self._is_focusable(widget)]
        if not order:
            return "break"
        current = self._index_of_focus(order)
        if current < 0:
            current = 0 if backwards else -1
        order[(current - 1 if backwards else current + 1) % len(order)].focus_set()
        return "break"

    # -- persistence ------------------------------------------------------

    def _commit(self, bookmarks: list[Bookmark], message: str) -> bool:
        """Persist ``bookmarks`` and refresh the list on success.

        Args:
            bookmarks: The new, complete bookmark list.
            message: Status text to show when the write succeeds.

        Returns:
            ``True`` when the write succeeded.
        """
        try:
            save_bookmarks(bookmarks, self._config_file)
        except ConfigError as exc:
            self._set_status(f"Could not save: {exc}", "error")
            return False
        self._bookmarks = bookmarks
        self._rebuild_rows()
        self._set_status(message, "ok")
        return True

    def _reload_from_disk(self) -> None:
        """Re-read bookmarks from disk, reporting failures inline."""
        try:
            self._bookmarks = load_bookmarks(self._config_file)
        except ConfigError as exc:
            self._bookmarks = []
            self._set_status(f"Could not load bookmarks: {exc}", "error")
        self._rebuild_rows()

    # -- row rendering ----------------------------------------------------

    def _rebuild_rows(self) -> None:
        """Destroy and recreate every row, then restore the focus order."""
        for child in self._rows_frame.winfo_children():
            child.destroy()
        self._row_focusables = []
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

        if not self._bookmarks:
            self._make_label(
                self._rows_frame,
                "No folders bookmarked yet — add one above with “Add Folder”.",
                bg=SURFACE,
                fg=TEXT_DIM,
                anchor="w",
                justify="left",
            ).pack(fill="x", padx=12, pady=14)
            self._rebuild_focus_order()
            return

        for position, bookmark in enumerate(self._bookmarks):
            self._build_row(position, bookmark)
        self._rebuild_focus_order()

    def _build_row(self, position: int, bookmark: Bookmark) -> None:
        """Create the widget row for one bookmark.

        Args:
            position: Index of the bookmark, used for the button callbacks.
            bookmark: The bookmark to render.
        """
        row = self._make_frame(self._rows_frame, bg=SURFACE)
        row.pack(fill="x", padx=6, pady=3)
        row.columnconfigure(1, weight=1)

        alias_button = self._make_button(
            row,
            bookmark.alias,
            command=lambda p=position: self._on_select_row(p),
            width_chars=18,
            anchor="w",
        )
        alias_button.grid(row=0, column=0, sticky="w", padx=(6, 8))

        self._make_label(
            row,
            bookmark.path if bookmark.exists else f"{bookmark.path}   (missing)",
            bg=SURFACE,
            fg=TEXT if bookmark.exists else WARN_TEXT,
            anchor="w",
        ).grid(row=0, column=1, sticky="ew")

        rename_button = self._make_button(
            row, "Rename", command=lambda p=position: self._on_rename(p), width_px=_ROW_BUTTON_PX
        )
        rename_button.grid(row=0, column=2, padx=4)

        repoint_button = self._make_button(
            row, "Change…", command=lambda p=position: self._on_repoint(p), width_px=_ROW_BUTTON_PX
        )
        repoint_button.grid(row=0, column=3, padx=4)

        up_button = self._make_button(
            row,
            "▲ Up",
            command=lambda p=position: self._on_move(p, -1),
            width_px=_ROW_MOVE_BUTTON_PX,
        )
        up_button.grid(row=0, column=4, padx=2)
        if position == 0:
            up_button.configure(state="disabled")

        down_button = self._make_button(
            row,
            "▼ Down",
            command=lambda p=position: self._on_move(p, +1),
            width_px=_ROW_MOVE_BUTTON_PX,
        )
        down_button.grid(row=0, column=5, padx=2)
        if position == len(self._bookmarks) - 1:
            down_button.configure(state="disabled")

        delete_button = self._make_button(
            row,
            "Delete",
            command=lambda p=position: self._on_delete(p),
            kind="danger",
            width_px=_ROW_BUTTON_PX,
        )
        delete_button.grid(row=0, column=6, padx=(4, 6))
        delete_button.bind("<Delete>", lambda _event, p=position: self._on_delete(p))

        focusable = [
            alias_button,
            rename_button,
            repoint_button,
            up_button,
            down_button,
            delete_button,
        ]
        for widget in focusable:
            widget.bind("<FocusIn>", lambda _e, r=row: self._highlight_row(r, True), add="+")
            widget.bind("<FocusOut>", lambda _e, r=row: self._highlight_row(r, False), add="+")
        alias_button.bind("<Return>", lambda _e, p=position: self._on_select_row(p))

        self._row_focusables.append(focusable)
        self._highlight_row(row, False)

    def _highlight_row(self, row: Any, active: bool) -> None:
        """Show or clear the focus ring on a row.

        The row background changes *and* the alias button switches to the
        primary colour pair, so the focused row is identifiable without relying
        on colour alone.
        """
        background = SELECT_DIM_BG if active else SURFACE
        self._configure_background(row, background)
        children = row.winfo_children()
        if not children:  # pragma: no cover - row already destroyed
            return
        alias_button = children[0]
        if self._tk.is_custom:
            alias_button.configure(
                fg_color=SELECT_BG if active else background,
                text_color=SELECT_FG if active else TEXT,
            )
        else:
            alias_button.configure(
                bg=SELECT_BG if active else background,
                fg=SELECT_FG if active else TEXT,
                activebackground=SELECT_BG,
                activeforeground=SELECT_FG,
            )

    # -- actions ----------------------------------------------------------

    def _on_browse(self) -> None:
        """Open the native OS folder picker and prefill the alias."""
        current = self._path_var.get().strip()
        initial = current if current and Path(current).is_dir() else str(Path.home())
        chosen = self._tk.filedialog.askdirectory(
            parent=self._root,
            title="Choose a folder to bookmark",
            initialdir=initial,
            mustexist=True,
        )
        if not chosen:
            self._set_status("Folder selection cancelled.", "info")
            return
        self._path_var.set(chosen)
        if not self._alias_var.get().strip():
            self._alias_var.set(default_alias(chosen))
        self._alias_entry.focus_set()

    def _on_add(self) -> None:
        """Validate the form and append a new bookmark."""
        alias = self._alias_var.get().strip()
        path = self._path_var.get().strip()
        if not path:
            self._set_status("Pick a folder first — use “Browse…”.", "error")
            self._browse_button.focus_set()
            return
        if not alias:
            self._set_status("An alias is required for every bookmark.", "error")
            self._alias_entry.focus_set()
            return
        try:
            updated = add_bookmark(self._bookmarks, alias, path)
        except ConfigError as exc:
            self._set_status(str(exc), "error")
            return
        if self._commit(updated, f"Added bookmark {alias!r}."):
            self._alias_var.set("")
            self._path_var.set("")
            self._alias_entry.focus_set()

    def _on_select_row(self, position: int) -> None:
        """Prefill the form from an existing row so it can be renamed/repointed."""
        if not 0 <= position < len(self._bookmarks):
            return
        bookmark = self._bookmarks[position]
        self._alias_var.set(bookmark.alias)
        self._path_var.set(bookmark.path)
        self._set_status(
            f"Row {position + 1} of {len(self._bookmarks)}: {bookmark.alias!r} — "
            "use Rename or Change… on the row, or edit the form and Add.",
            "info",
        )

    def _on_rename(self, position: int) -> None:
        """Open a real modal dialog asking for the new alias."""
        if not 0 <= position < len(self._bookmarks):
            return
        bookmark = self._bookmarks[position]
        entered = self._ask_string(
            title=f"Rename {bookmark.alias!r}", prompt="New alias:", initial=bookmark.alias
        )
        if entered is None:
            self._set_status("Rename cancelled.", "info")
            return
        try:
            updated = rename_bookmark(self._bookmarks, position, entered)
        except ConfigError as exc:
            self._set_status(str(exc), "error")
            return
        self._commit(updated, f"Renamed to {entered.strip()!r}.")

    def _on_repoint(self, position: int) -> None:
        """Point an existing bookmark at a different directory."""
        if not 0 <= position < len(self._bookmarks):
            return
        bookmark = self._bookmarks[position]
        initial = bookmark.path if Path(bookmark.path).is_dir() else str(Path.home())
        chosen = self._tk.filedialog.askdirectory(
            parent=self._root,
            title=f"New folder for {bookmark.alias!r}",
            initialdir=initial,
            mustexist=True,
        )
        if not chosen:
            self._set_status("Folder change cancelled.", "info")
            return
        try:
            updated = replace_bookmark_path(self._bookmarks, position, chosen)
        except ConfigError as exc:
            self._set_status(str(exc), "error")
            return
        self._commit(updated, f"{bookmark.alias!r} now points at {chosen}.")

    def _on_move(self, position: int, offset: int) -> None:
        """Move a row up or down, persisting the new order immediately."""
        if not 0 <= position < len(self._bookmarks):
            return
        target = position + offset
        if not 0 <= target < len(self._bookmarks):
            edge = "top" if offset < 0 else "bottom"
            self._set_status(
                f"{self._bookmarks[position].alias!r} is already at the {edge} of the list.",
                "info",
            )
            return
        try:
            updated = move_bookmark(self._bookmarks, position, offset)
        except ConfigError as exc:
            self._set_status(str(exc), "error")
            return
        self._commit(
            updated,
            f"Moved {updated[target].alias!r} {'up' if offset < 0 else 'down'} "
            f"to position {target + 1}.",
        )

    def _on_delete(self, position: int) -> None:
        """Delete a bookmark after an explicit confirmation."""
        if not 0 <= position < len(self._bookmarks):
            return
        bookmark = self._bookmarks[position]
        confirmed = self._tk.messagebox.askyesno(
            parent=self._root,
            title="Delete bookmark",
            message=f"Delete the bookmark {bookmark.alias!r}?\n\n{bookmark.path}",
            icon="warning",
            default="no",
        )
        if not confirmed:
            self._set_status("Delete cancelled.", "info")
            return
        try:
            updated = remove_bookmark(self._bookmarks, position)
        except ConfigError as exc:
            self._set_status(str(exc), "error")
            return
        self._commit(updated, f"Deleted {bookmark.alias!r}.")

    def _ask_string(self, title: str, prompt: str, initial: str) -> str | None:
        """Show a modal single line prompt and return the entered text.

        Args:
            title: Dialog window title.
            prompt: Label text for the input.
            initial: Pre-filled value.

        Returns:
            The entered text, or ``None`` if the user cancelled.
        """
        dialog = self._tk.toplevel(self._root)
        dialog.title(title)
        dialog.transient(self._root)
        dialog.resizable(False, False)
        self._configure_background(dialog, BG)

        variable = tkinter.StringVar(value=initial)
        prompt_label = self._make_label(dialog, prompt, bg=BG, fg=TEXT, anchor="w")
        prompt_label.grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(14, 4))
        entry = self._make_entry(dialog, textvariable=variable, bg=SURFACE, fg=TEXT, width_chars=34)
        entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14)
        self._link_label(prompt_label, entry)
        entry.select_range(0, "end")
        entry.focus_set()

        result: list[str | None] = [None]

        def accept() -> None:
            result[0] = variable.get()
            dialog.destroy()

        def cancel() -> None:
            result[0] = None
            dialog.destroy()

        self._make_button(dialog, "OK", command=accept, kind="primary", width_chars=8).grid(
            row=2, column=0, sticky="e", padx=(14, 6), pady=14
        )
        self._make_button(dialog, "Cancel", command=cancel, width_chars=8).grid(
            row=2, column=1, sticky="w", padx=(6, 14), pady=14
        )

        entry.bind("<Return>", lambda _event: accept())
        entry.bind("<KP_Enter>", lambda _event: accept())
        # Bound on the dialog as well as the entry: the main panel binds Escape
        # with bind_all, so if focus is on OK/Cancel that binding would otherwise
        # tear the whole panel down instead of just the dialog.
        dialog.bind("<Escape>", lambda _event: cancel())
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.grab_set()
        self._root.wait_window(dialog)
        return result[0]

    def _on_done(self) -> None:
        """Close the panel.  Everything is already saved on disk."""
        with suppress(Exception):
            self._root.destroy()

    # -- entry point ------------------------------------------------------

    def run(self) -> int:
        """Build the UI, show it and block until the window closes.

        Returns:
            ``0`` on a clean close, ``1`` when the display or Tk is unavailable.
        """
        try:
            self._build_ui()
        except tkinter.TclError as exc:
            print(
                f"{APP_NAME}: cannot open the settings window ({exc}).\n"
                "The settings panel needs a graphical display. Edit the bookmark "
                f"file directly at {effective_config_path(self._config_file)} instead.",
                file=sys.stderr,
            )
            return 1
        self._reload_from_disk()
        self._rebuild_focus_order()
        self._alias_entry.focus_set()
        self._root.mainloop()
        return 0


_BUTTON_COLORS: Final[dict[str, tuple[str, str]]] = {
    "normal": (SURFACE, TEXT),
    "primary": (SELECT_BG, SELECT_FG),
    "danger": (SURFACE, ERROR_TEXT),
    "quiet": (SURFACE, TEXT_DIM),
}


def run_settings_panel(config_file: Path | None = None) -> int:
    """Open the bookmark settings panel.

    Args:
        config_file: Override for ``config.json``; used by the test suite.

    Returns:
        ``0`` when the panel closed normally, non-zero when it could not start.
    """
    try:
        panel = SettingsPanel(config_file=config_file)
    except Exception as exc:
        print(
            f"{APP_NAME}: the settings panel needs tkinter, which is not "
            f"available ({exc}).\n"
            "On Debian/Ubuntu install it with: sudo apt install python3-tk",
            file=sys.stderr,
        )
        return 1
    try:
        return panel.run()
    except Exception as exc:
        # run() handles the display-not-available case itself; this catches
        # anything else that escapes from inside the event loop.
        print(
            f"{APP_NAME}: the settings panel stopped unexpectedly ({exc}).\n"
            f"You can still edit your bookmarks directly at "
            f"{effective_config_path(config_file)}.",
            file=sys.stderr,
        )
        return 1
