"""Tests for the settings panel.

Most of these run against a real Tk root window so that focus traversal,
``takefocus`` and the themed widget options are exercised for real rather than
against a mock.  Dialogs and native pickers are the exception: they are
monkeypatched, because a blocking modal cannot be driven from a test and the
platform folder picker is not scriptable.
"""

from __future__ import annotations

import tkinter
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from cdtui import __version__, gui
from cdtui.config import Bookmark, ConfigError, load_bookmarks, save_bookmarks

tk = pytest.importorskip("tkinter")


def has_display() -> bool:
    """Return ``True`` when a Tk window can actually be opened."""
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        return False
    root.destroy()
    return True


pytestmark = pytest.mark.skipif(not has_display(), reason="no display")


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    save_bookmarks([Bookmark("alpha", "/one/alpha"), Bookmark("beta", "/one/beta")], path=path)
    return path


def plain_toolkit() -> gui.Toolkit:
    """Resolve the toolkit with CustomTkinter hidden, via production logic."""
    real_import = gui.importlib.import_module

    def no_customtkinter(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "customtkinter":
            raise ImportError("hidden for this test")
        return real_import(name, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gui.importlib, "import_module", no_customtkinter)
        return gui.resolve_toolkit()


def make_panel(
    config_file: Path, toolkit: gui.Toolkit, monkeypatch: pytest.MonkeyPatch
) -> gui.SettingsPanel:
    """Build a panel the way ``run()`` does, but without entering mainloop."""
    monkeypatch.setattr(gui, "resolve_toolkit", lambda: toolkit)
    panel = gui.SettingsPanel(config_file=config_file)
    panel._build_ui()
    panel._reload_from_disk()
    panel._rebuild_focus_order()
    return panel


@pytest.fixture(params=["plain", "custom"])
def toolkit(request: pytest.FixtureRequest) -> gui.Toolkit:
    """Run the whole suite against both supported toolkits."""
    if request.param == "plain":
        return plain_toolkit()
    pytest.importorskip("customtkinter")
    return gui.resolve_toolkit()


@pytest.fixture
def panel(
    toolkit: gui.Toolkit, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[gui.SettingsPanel]:
    instance = make_panel(config_file, toolkit, monkeypatch)
    try:
        yield instance
    finally:
        with suppress(tkinter.TclError):  # pragma: no cover - may be gone
            instance._root.destroy()


class FakeWidget:
    """Duck-typed stand-in for a Tk widget.

    Focus traversal is tested with these rather than real widgets: asking a
    window manager for real focus on an unmapped window is unreliable (it
    segfaults on macOS), and the logic under test is the traversal itself.
    """

    def __init__(self, path: str, state: str = "normal", root: FakeRoot | None = None) -> None:
        self._w = path
        self._state = state
        self._root = root
        self.focused = False

    def winfo_exists(self) -> bool:
        return True

    def cget(self, key: str) -> str:
        if key == "state":
            return self._state
        raise KeyError(key)

    def focus_set(self) -> None:
        self.focused = True
        if self._root is not None:
            self._root.focused = self

    def __str__(self) -> str:
        return self._w


class FakeRoot:
    """Stand-in for the Tk root that reports whatever widget has focus."""

    def __init__(self, focused: FakeWidget | None = None) -> None:
        self.focused = focused

    def focus_get(self) -> FakeWidget | None:
        return self.focused

    def destroy(self) -> None:  # the panel fixture tears the root down
        self.focused = None


_KIND_BY_COLOUR = {colour: kind for kind, colour in gui._STATUS_COLORS.items()}


def status_of(panel: gui.SettingsPanel) -> tuple[str, str]:
    """Return the ``(message, kind)`` currently shown in the status bar."""
    option = "text_color" if panel._tk.is_custom else "fg"
    colour = str(panel._status_label.cget(option))
    return str(panel._status_var.get()), _KIND_BY_COLOUR[colour]


# ---------------------------------------------------------------------------
# Toolkit resolution
# ---------------------------------------------------------------------------


class TestResolveToolkit:
    def test_custom_tkinter_is_preferred(self) -> None:
        pytest.importorskip("customtkinter")
        assert gui.resolve_toolkit().is_custom is True

    def test_falls_back_to_tkinter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_import = gui.importlib.import_module

        def no_customtkinter(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "customtkinter":
                raise ImportError("nope")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(gui.importlib, "import_module", no_customtkinter)
        resolved = gui.resolve_toolkit()
        assert resolved.is_custom is False
        assert resolved.root is tkinter.Tk
        assert resolved.toplevel is tkinter.Toplevel

    def test_toplevel_matches_the_toolkit(self) -> None:
        # The rename dialog must come from the same toolkit as the root, or
        # it silently loses fg_color and every other themed option.
        resolved = gui.resolve_toolkit()
        assert resolved.toplevel.__module__.split(".")[0] == resolved.root.__module__.split(".")[0]

    def test_canvas_is_always_plain_tkinter(self) -> None:
        assert gui.resolve_toolkit().canvas is tkinter.Canvas


# ---------------------------------------------------------------------------
# Construction and layout
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_loads_existing_bookmarks(self, panel: gui.SettingsPanel, config_file: Path) -> None:
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]
        assert len(panel._row_focusables) == 2

    def test_missing_config_yields_an_empty_list(
        self, toolkit: gui.Toolkit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        instance = make_panel(tmp_path / "nope.json", toolkit, monkeypatch)
        try:
            assert instance._bookmarks == []
        finally:
            instance._root.destroy()

    def test_empty_config_shows_the_placeholder(
        self, toolkit: gui.Toolkit, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save_bookmarks([], path=tmp_path / "empty.json")
        instance = make_panel(tmp_path / "empty.json", toolkit, monkeypatch)
        try:
            assert instance._bookmarks == []
            # No rows were built, so there is nothing to traverse.
            assert instance._focus_order[-1] is instance._done_button
        finally:
            instance._root.destroy()

    def test_title_names_the_app_and_version(self, panel: gui.SettingsPanel) -> None:
        title = panel._root.title()
        assert gui.APP_NAME in title
        assert __version__ in title

    def test_char_width_is_positive(self) -> None:
        assert gui._char_width(10) > 0


# ---------------------------------------------------------------------------
# Focus traversal
# ---------------------------------------------------------------------------


class TestFocusOrder:
    def test_order_is_form_then_rows_then_done(self, panel: gui.SettingsPanel) -> None:
        order = panel._focus_order
        assert order[0] is panel._alias_entry
        assert order[1] is panel._path_entry
        assert order[2] is panel._browse_button
        assert order[3] is panel._add_button
        assert order[-1] is panel._done_button

    def test_rows_appear_in_order(self, panel: gui.SettingsPanel) -> None:
        order = panel._focus_order
        first_row = panel._row_focusables[0][0]
        second_row = panel._row_focusables[1][0]
        assert order.index(first_row) < order.index(second_row)

    def test_disabled_widgets_are_skipped(self, panel: gui.SettingsPanel) -> None:
        panel._add_button.configure(state="disabled")
        panel._rebuild_focus_order()
        assert panel._add_button not in panel._focus_order

    def test_destroyed_widgets_are_skipped(self, panel: gui.SettingsPanel) -> None:
        panel._add_button.destroy()
        panel._rebuild_focus_order()
        assert panel._add_button not in panel._focus_order

    def test_real_widgets_have_distinct_paths(self, panel: gui.SettingsPanel) -> None:
        # Guards against a focus order that contains the same widget twice or
        # a dead path, which would make traversal silently skip entries.
        paths = [gui.SettingsPanel._widget_path(widget) for widget in panel._focus_order]
        assert all(paths)
        assert len(set(paths)) == len(paths)

    def test_tab_moves_to_the_next_widget(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = FakeRoot()
        widgets = [FakeWidget(f".w{index}", root=root) for index in range(4)]
        monkeypatch.setattr(panel, "_root", root)
        monkeypatch.setattr(panel, "_focus_order", widgets)
        root.focused = widgets[1]
        assert panel._cycle_focus(backwards=False) == "break"
        assert widgets[2].focused

    def test_tab_wraps_forward(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = FakeRoot()
        widgets = [FakeWidget(f".w{index}", root=root) for index in range(4)]
        monkeypatch.setattr(panel, "_root", root)
        monkeypatch.setattr(panel, "_focus_order", widgets)
        root.focused = widgets[-1]
        assert panel._cycle_focus(backwards=False) == "break"
        assert widgets[0].focused

    def test_shift_tab_wraps_backwards(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = FakeRoot()
        widgets = [FakeWidget(f".w{index}", root=root) for index in range(4)]
        monkeypatch.setattr(panel, "_root", root)
        monkeypatch.setattr(panel, "_focus_order", widgets)
        root.focused = widgets[0]
        assert panel._cycle_focus(backwards=True) == "break"
        assert widgets[-1].focused

    def test_losing_focus_re_enters_at_the_ends(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # focus_get() returning None must not send Tab backwards.
        root = FakeRoot(focused=None)
        widgets = [FakeWidget(f".w{index}", root=root) for index in range(3)]
        monkeypatch.setattr(panel, "_root", root)
        monkeypatch.setattr(panel, "_focus_order", widgets)
        assert panel._cycle_focus(backwards=False) == "break"
        assert widgets[0].focused
        root.focused = None
        widgets[0].focused = False
        assert panel._cycle_focus(backwards=True) == "break"
        assert widgets[-1].focused

    def test_cycle_returns_break_when_nothing_is_focusable(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(panel, "_focus_order", [])
        assert panel._cycle_focus(backwards=False) == "break"
        assert panel._cycle_focus(backwards=True) == "break"

    def test_index_of_focus_matches_composite_widgets(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # CustomTkinter focus lands on an inner Tk widget whose path is a child
        # of the CTk widget's path, so the lookup has to match on prefix.
        root = FakeRoot()
        wrapper = FakeWidget(".ctk", root=root)
        inner = FakeWidget(".ctk.inner", root=root)
        other = FakeWidget(".other", root=root)
        root.focused = inner
        monkeypatch.setattr(panel, "_root", root)
        assert panel._index_of_focus([wrapper, other]) == 0
        assert panel._index_of_focus([other, wrapper]) == 1

    def test_index_of_focus_is_minus_one_when_nothing_has_focus(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(panel, "_root", FakeRoot(focused=None))
        assert panel._index_of_focus(panel._focus_order) == -1


# ---------------------------------------------------------------------------
# Adding
# ---------------------------------------------------------------------------


class TestAdd:
    def test_appends_and_persists(self, panel: gui.SettingsPanel, config_file: Path) -> None:
        panel._alias_var.set("gamma")
        panel._path_var.set("/one/gamma")
        panel._on_add()
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta", "gamma"]
        assert [b.alias for b in load_bookmarks(config_file)] == [
            "alpha",
            "beta",
            "gamma",
        ]

    def test_clears_the_form_after_a_success(self, panel: gui.SettingsPanel) -> None:
        panel._alias_var.set("gamma")
        panel._path_var.set("/one/gamma")
        panel._on_add()
        assert panel._alias_var.get() == ""
        assert panel._path_var.get() == ""

    def test_missing_path_is_rejected(self, panel: gui.SettingsPanel) -> None:
        panel._alias_var.set("gamma")
        panel._path_var.set("")
        panel._on_add()
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]
        message, kind = status_of(panel)
        assert kind == "error"
        assert "folder" in message.lower()

    def test_missing_alias_is_rejected(self, panel: gui.SettingsPanel) -> None:
        panel._alias_var.set("   ")
        panel._path_var.set("/one/gamma")
        panel._on_add()
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]
        message, kind = status_of(panel)
        assert kind == "error"
        assert "alias" in message.lower()

    def test_duplicate_alias_is_rejected(self, panel: gui.SettingsPanel) -> None:
        panel._alias_var.set("alpha")
        panel._path_var.set("/one/gamma")
        panel._on_add()
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]
        message, kind = status_of(panel)
        assert kind == "error"
        assert "alpha" in message

    def test_a_failed_save_is_reported(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(_bookmarks: Any, _path: Any) -> None:
            raise ConfigError("read-only filesystem")

        monkeypatch.setattr(gui, "save_bookmarks", boom)
        panel._alias_var.set("gamma")
        panel._path_var.set("/one/gamma")
        panel._on_add()
        message, kind = status_of(panel)
        assert kind == "error"
        assert "read-only filesystem" in message
        # The in-memory list must not drift away from what is on disk.
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]

    def test_paths_are_expanded(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", "/home/tester")
        panel._alias_var.set("tilde")
        panel._path_var.set("~/projects")
        panel._on_add()
        assert panel._bookmarks[-1].path == "/home/tester/projects"


# ---------------------------------------------------------------------------
# Selecting a row
# ---------------------------------------------------------------------------


class TestSelectRow:
    def test_prefills_the_form(self, panel: gui.SettingsPanel) -> None:
        panel._on_select_row(1)
        assert panel._alias_var.get() == "beta"
        assert panel._path_var.get() == "/one/beta"

    def test_out_of_range_is_ignored(self, panel: gui.SettingsPanel) -> None:
        panel._on_select_row(99)
        assert panel._alias_var.get() == ""
        panel._on_select_row(-1)
        assert panel._alias_var.get() == ""


# ---------------------------------------------------------------------------
# Renaming and repointing
# ---------------------------------------------------------------------------


class TestRename:
    def test_renames_in_place(
        self, panel: gui.SettingsPanel, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(panel, "_ask_string", lambda **_kwargs: "renamed")
        panel._on_rename(0)
        assert [b.alias for b in panel._bookmarks] == ["renamed", "beta"]
        assert load_bookmarks(config_file)[0].alias == "renamed"

    def test_cancelling_changes_nothing(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(panel, "_ask_string", lambda **_kwargs: None)
        panel._on_rename(0)
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]

    def test_an_empty_rename_is_rejected(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(panel, "_ask_string", lambda **_kwargs: "   ")
        panel._on_rename(0)
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]
        _message, kind = status_of(panel)
        assert kind == "error"

    def test_a_colliding_rename_is_rejected(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(panel, "_ask_string", lambda **_kwargs: "beta")
        panel._on_rename(0)
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]
        message, kind = status_of(panel)
        assert kind == "error"
        assert "beta" in message

    def test_keeping_the_same_alias_is_a_no_op(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(panel, "_ask_string", lambda **_kwargs: "alpha")
        panel._on_rename(0)
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]

    def test_out_of_range_is_ignored(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(panel, "_ask_string", lambda **_kwargs: "x")
        panel._on_rename(42)
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]


class TestRepoint:
    """Repointing uses the native folder picker, not the rename text dialog."""

    @staticmethod
    def patch_picker(
        panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch, result: str
    ) -> None:
        monkeypatch.setattr(panel._tk.filedialog, "askdirectory", lambda **_kwargs: result)

    def test_changes_the_path(
        self, panel: gui.SettingsPanel, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.patch_picker(panel, monkeypatch, "/two/alpha")
        panel._on_repoint(0)
        assert panel._bookmarks[0].path == "/two/alpha"
        assert load_bookmarks(config_file)[0].path == "/two/alpha"

    def test_the_picker_must_exist(self, panel: gui.SettingsPanel) -> None:
        save_bookmarks([Bookmark("here", str(Path.home()))], path=panel._config_file)
        panel._reload_from_disk()
        seen: dict[str, Any] = {}

        def capture(**kwargs: Any) -> str:
            seen.update(kwargs)
            return ""

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(panel._tk.filedialog, "askdirectory", capture)
        try:
            panel._on_repoint(0)
        finally:
            monkeypatch.undo()
        assert seen["mustexist"] is True
        assert seen["initialdir"] == str(Path.home())

    def test_cancelling_changes_nothing(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.patch_picker(panel, monkeypatch, "")
        panel._on_repoint(0)
        assert panel._bookmarks[0].path == "/one/alpha"
        message, _ = status_of(panel)
        assert "cancel" in message.lower()

    def test_an_out_of_range_position_never_opens_the_picker(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            panel._tk.filedialog,
            "askdirectory",
            lambda **_kwargs: pytest.fail("must not open the picker"),
        )
        panel._on_repoint(7)
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Reordering and deleting
# ---------------------------------------------------------------------------


class TestMove:
    def test_moves_down(self, panel: gui.SettingsPanel) -> None:
        panel._on_move(0, 1)
        assert [b.alias for b in panel._bookmarks] == ["beta", "alpha"]

    def test_moves_up(self, panel: gui.SettingsPanel) -> None:
        panel._on_move(1, -1)
        assert [b.alias for b in panel._bookmarks] == ["beta", "alpha"]

    def test_moves_down_by_two(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save_bookmarks(
            [Bookmark("a", "/a"), Bookmark("b", "/b"), Bookmark("c", "/c")],
            path=panel._config_file,
        )
        panel._reload_from_disk()
        panel._on_move(0, 2)
        assert [b.alias for b in panel._bookmarks] == ["b", "c", "a"]

    def test_will_not_move_past_the_end(self, panel: gui.SettingsPanel) -> None:
        panel._on_move(1, 1)
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]

    def test_will_not_move_before_the_start(self, panel: gui.SettingsPanel) -> None:
        panel._on_move(0, -1)
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]

    def test_an_out_of_range_position_is_ignored(self, panel: gui.SettingsPanel) -> None:
        panel._on_move(7, -1)
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]


class TestDelete:
    def test_deletes_after_confirmation(
        self, panel: gui.SettingsPanel, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(panel._tk.messagebox, "askyesno", lambda **_kwargs: True)
        panel._on_delete(0)
        assert [b.alias for b in panel._bookmarks] == ["beta"]
        assert [b.alias for b in load_bookmarks(config_file)] == ["beta"]

    def test_cancelling_keeps_the_bookmark(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(panel._tk.messagebox, "askyesno", lambda **_kwargs: False)
        panel._on_delete(0)
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]
        message, _ = status_of(panel)
        assert "cancel" in message.lower()

    def test_the_default_is_no(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def capture(**kwargs: Any) -> bool:
            seen.update(kwargs)
            return False

        monkeypatch.setattr(panel._tk.messagebox, "askyesno", capture)
        panel._on_delete(0)
        assert seen["default"] == "no"
        assert seen["icon"] == "warning"

    def test_an_out_of_range_position_is_ignored(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            panel._tk.messagebox,
            "askyesno",
            lambda **_kwargs: pytest.fail("must not prompt"),
        )
        panel._on_delete(5)
        assert [b.alias for b in panel._bookmarks] == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Browsing
# ---------------------------------------------------------------------------


class TestBrowse:
    def test_fills_in_the_chosen_folder(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            panel._tk.filedialog, "askdirectory", lambda **_kwargs: "/picked/folder"
        )
        panel._on_browse()
        assert panel._path_var.get() == "/picked/folder"

    def test_a_cancelled_picker_leaves_the_form_alone(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        panel._path_var.set("/keep/me")
        monkeypatch.setattr(panel._tk.filedialog, "askdirectory", lambda **_kwargs: "")
        panel._on_browse()
        assert panel._path_var.get() == "/keep/me"
        message, _ = status_of(panel)
        assert "cancel" in message.lower()

    def test_it_keeps_a_valid_existing_path(
        self, panel: gui.SettingsPanel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}
        real = tmp_path / "a folder"
        real.mkdir()
        panel._path_var.set(str(real))

        def capture(**kwargs: Any) -> str:
            seen.update(kwargs)
            return str(real)

        monkeypatch.setattr(panel._tk.filedialog, "askdirectory", capture)
        panel._on_browse()
        assert seen["initialdir"] == str(real)
        assert seen["mustexist"] is True

    def test_a_missing_path_falls_back_to_home(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def capture(**kwargs: Any) -> str:
            seen.update(kwargs)
            return ""

        panel._path_var.set("/definitely/not/here")
        monkeypatch.setattr(panel._tk.filedialog, "askdirectory", capture)
        panel._on_browse()
        assert seen["initialdir"] == str(Path.home())


# ---------------------------------------------------------------------------
# Reloading from disk
# ---------------------------------------------------------------------------


class TestReload:
    def test_picks_up_an_external_change(self, panel: gui.SettingsPanel) -> None:
        save_bookmarks([Bookmark("gamma", "/g")], path=panel._config_file)
        panel._reload_from_disk()
        assert [b.alias for b in panel._bookmarks] == ["gamma"]
        assert len(panel._row_focusables) == 1

    def test_a_broken_config_shows_an_error_and_no_rows(self, panel: gui.SettingsPanel) -> None:
        Path(panel._config_file).write_text("{ not json", encoding="utf-8")
        panel._reload_from_disk()
        assert panel._bookmarks == []
        message, kind = status_of(panel)
        assert kind == "error"
        assert "could not load" in message.lower()

    def test_reload_keeps_the_focus_order_sane(self, panel: gui.SettingsPanel) -> None:
        save_bookmarks([], path=panel._config_file)
        panel._reload_from_disk()
        assert panel._focus_order[-1] is panel._done_button


# ---------------------------------------------------------------------------
# The status bar
# ---------------------------------------------------------------------------


class TestStatus:
    def test_kinds_map_to_audited_colours(self, panel: gui.SettingsPanel) -> None:
        for kind, expected in (
            ("ok", gui.OK_TEXT),
            ("error", gui.ERROR_TEXT),
            ("warn", gui.WARN_TEXT),
            ("info", gui.TEXT_DIM),
        ):
            assert expected in gui._STATUS_COLORS.values()
            panel._set_status("message", kind)
            assert gui.SettingsPanel._widget_path(panel._status_label)
            message, seen_kind = status_of(panel)
            assert (message, seen_kind) == ("message", kind)

    def test_every_kind_has_a_contrast_audited_colour(self) -> None:
        # test_contrast.py proves the ratios; this proves the wiring, i.e. that
        # a kind cannot be added without picking a colour.
        assert set(gui._STATUS_COLORS) == {"info", "ok", "warn", "error"}
        assert all(len(colour) == len("#rrggbb") for colour in gui._STATUS_COLORS.values())

    def test_an_unknown_kind_is_rejected_loudly(self, panel: gui.SettingsPanel) -> None:
        # A typo in a status kind used to raise KeyError from the colour
        # lookup, which would surface as a traceback in the middle of a click.
        with pytest.raises(KeyError):
            panel._set_status("message", "nonsense")


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


class TestRows:
    def test_a_missing_folder_is_marked(
        self, panel: gui.SettingsPanel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save_bookmarks([Bookmark("gone", "/definitely/not/here")], path=panel._config_file)
        panel._reload_from_disk()
        row = panel._row_focusables[0][0].master
        labels = [w for w in row.winfo_children() if isinstance(w, panel._tk.label)]
        text = " ".join(str(widget.cget("text")) for widget in labels)
        assert "missing" in text

    def test_an_existing_folder_is_not_marked(self, panel: gui.SettingsPanel) -> None:
        save_bookmarks([Bookmark("here", str(Path.home()))], path=panel._config_file)
        panel._reload_from_disk()
        row = panel._row_focusables[0][0].master
        labels = [w for w in row.winfo_children() if isinstance(w, panel._tk.label)]
        text = " ".join(str(widget.cget("text")) for widget in labels)
        assert "missing" not in text

    def test_highlighting_swaps_the_row_colour(self, panel: gui.SettingsPanel) -> None:
        frame_option = "fg_color" if panel._tk.is_custom else "bg"
        button_option = "fg_color" if panel._tk.is_custom else "bg"
        row = panel._row_focusables[0][0].master
        alias_button = row.winfo_children()[0]

        panel._highlight_row(row, True)
        assert str(row.cget(frame_option)) == gui.SELECT_DIM_BG
        assert str(alias_button.cget(button_option)) == gui.SELECT_BG

        panel._highlight_row(row, False)
        assert str(row.cget(frame_option)) == gui.SURFACE
        assert str(alias_button.cget(button_option)) == gui.SURFACE

    def test_highlighting_a_row_with_no_children_is_safe(self, panel: gui.SettingsPanel) -> None:
        class FakeRow:
            def __init__(self) -> None:
                self.painted: str | None = None

            def configure(self, **kwargs: Any) -> None:
                self.painted = next(iter(kwargs.values()), None)

            def winfo_children(self) -> list[Any]:
                return []

        row = FakeRow()
        # Must not raise even though there is no alias button to restyle.
        panel._highlight_row(row, True)
        assert row.painted == gui.SELECT_DIM_BG

    def test_move_buttons_are_labelled_with_arrows(self, panel: gui.SettingsPanel) -> None:
        row = panel._row_focusables[0][0].master
        labels = [
            str(widget.cget("text"))
            for widget in row.winfo_children()
            if isinstance(widget, panel._tk.button)
        ]
        assert "▲ Up" in labels
        assert "▼ Down" in labels

    def test_rows_are_rebuilt_not_appended(self, panel: gui.SettingsPanel) -> None:
        before = len(panel._row_focusables)
        panel._rebuild_rows()
        assert len(panel._row_focusables) == before


# ---------------------------------------------------------------------------
# run_settings_panel
# ---------------------------------------------------------------------------


class TestRunSettingsPanel:
    def test_returns_the_exit_code(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakePanel:
            def __init__(self, config_file: Path | None = None) -> None:
                pass

            def run(self) -> int:
                return 0

        monkeypatch.setattr(gui, "SettingsPanel", FakePanel)
        assert gui.run_settings_panel(tmp_path / "c.json") == 0

    def test_a_failure_is_reported_as_a_nonzero_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Exploding:
            def __init__(self, config_file: Path | None = None) -> None:
                pass

            def run(self) -> int:
                raise tkinter.TclError("no display")

        monkeypatch.setattr(gui, "SettingsPanel", Exploding)
        assert gui.run_settings_panel(tmp_path / "c.json") != 0
