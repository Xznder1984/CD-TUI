"""Headless behaviour tests for the Textual application.

Driven through Textual's ``run_test`` pilot, so these exercise the real widget
tree, bindings and key handling without needing a terminal.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Label, Static

from cdtui import config, tui
from cdtui.config import Bookmark, ConfigError, save_bookmarks


@pytest.fixture
def three_bookmarks(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    save_bookmarks(
        [
            Bookmark("alpha", "/one/alpha"),
            Bookmark("beta", "/one/beta"),
            Bookmark("gamma", "/one/gamma"),
        ],
        path=path,
    )
    return path


class FakeProcess:
    """Stand-in for ``asyncio.subprocess.Process``."""

    def __init__(self, returncode: int = 0, release: asyncio.Event | None = None) -> None:
        self._returncode = returncode
        self._release = release

    async def wait(self) -> int:
        if self._release is not None:
            await self._release.wait()
        return self._returncode


def install_subprocess_recorder(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int = 0,
    *,
    started: asyncio.Event | None = None,
    release: asyncio.Event | None = None,
) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
    """Capture the settings-panel launch instead of really running it.

    When ``started``/``release`` events are supplied the fake child stays
    "open" until ``release`` is set, which is what it takes to observe the
    app's re-entrancy guard from the outside.
    """
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    async def fake_exec(*command: str, **kwargs: Any) -> FakeProcess:
        calls.append((command, kwargs))
        if started is not None:
            started.set()
        return FakeProcess(returncode, release=release)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


def rows_of(app: tui.CDTUIApp) -> list[tui.BookmarkRow]:
    return list(app.query(tui.BookmarkRow))


def marker_of(row: tui.BookmarkRow) -> str:
    return str(row.query_one(".row-marker", Label).content)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRendering:
    async def test_lists_every_bookmark(self, three_bookmarks: Path) -> None:
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert [b.alias for b in app._bookmarks] == ["alpha", "beta", "gamma"]
            assert len(rows_of(app)) == 3

    async def test_first_row_is_highlighted_by_default(self, three_bookmarks: Path) -> None:
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one(tui.BookmarkListView).index == 0

    async def test_header_shows_the_count(self, three_bookmarks: Path) -> None:
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "3 folders" in app.sub_title

    async def test_header_singularises_one_folder(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        save_bookmarks([Bookmark("only", "/only")], path=path)
        app = tui.CDTUIApp(config_file=path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "1 folder" in app.sub_title

    async def test_empty_state_replaces_the_list(self, tmp_path: Path) -> None:
        app = tui.CDTUIApp(config_file=tmp_path / "missing.json")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one(tui.EmptyState).display
            assert not app.query_one(tui.BookmarkListView).display

    async def test_empty_state_copy_is_the_documented_string(self, tmp_path: Path) -> None:
        app = tui.CDTUIApp(config_file=tmp_path / "missing.json")
        async with app.run_test() as pilot:
            await pilot.pause()
            copy = " ".join(
                str(widget.content) for widget in app.query_one(tui.EmptyState).query(Static)
            )
            assert "No folders bookmarked yet — press `a` to add one" in copy

    async def test_empty_state_names_the_config_file_in_use(self, tmp_path: Path) -> None:
        # With --config the user has to be pointed at the file the app is
        # actually reading, not at the default location.
        override = tmp_path / "elsewhere" / "bookmarks.json"
        app = tui.CDTUIApp(config_file=override)
        async with app.run_test() as pilot:
            await pilot.pause()
            copy = " ".join(
                str(widget.content) for widget in app.query_one(tui.EmptyState).query(Static)
            )
            assert str(override) in copy
            assert str(config.config_path()) not in copy

    async def test_empty_state_falls_back_to_the_default_path(self, tmp_path: Path) -> None:
        app = tui.CDTUIApp(config_file=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            copy = " ".join(
                str(widget.content) for widget in app.query_one(tui.EmptyState).query(Static)
            )
            assert str(config.config_path()) in copy

    async def test_error_banner_appears_for_a_broken_config(self, tmp_path: Path) -> None:
        broken = tmp_path / "config.json"
        broken.write_text("{ not json", encoding="utf-8")
        app = tui.CDTUIApp(config_file=broken)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._load_error
            banner = app.query_one("#error-banner")
            assert banner.display
            assert "config.json" in str(banner.content)

    async def test_error_banner_is_hidden_when_the_config_is_fine(
        self, three_bookmarks: Path
    ) -> None:
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not app.query_one("#error-banner").display

    async def test_a_broken_config_still_offers_the_empty_state(self, tmp_path: Path) -> None:
        broken = tmp_path / "config.json"
        broken.write_text("{ not json", encoding="utf-8")
        app = tui.CDTUIApp(config_file=broken)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one(tui.EmptyState).display


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


class TestNavigation:
    async def test_down_and_j(self, three_bookmarks: Path) -> None:
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            view = app.query_one(tui.BookmarkListView)
            await pilot.press("down")
            assert view.index == 1
            await pilot.press("j")
            assert view.index == 2

    async def test_up_and_k(self, three_bookmarks: Path) -> None:
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            view = app.query_one(tui.BookmarkListView)
            await pilot.press("end")
            assert view.index == 2
            await pilot.press("up")
            assert view.index == 1
            await pilot.press("k")
            assert view.index == 0

    async def test_home_and_end(self, three_bookmarks: Path) -> None:
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            view = app.query_one(tui.BookmarkListView)
            await pilot.press("end")
            assert view.index == 2
            await pilot.press("home")
            assert view.index == 0

    async def test_navigation_clamps_at_both_ends(self, three_bookmarks: Path) -> None:
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            view = app.query_one(tui.BookmarkListView)
            for _ in range(6):
                await pilot.press("up")
            assert view.index == 0
            for _ in range(6):
                await pilot.press("down")
            assert view.index == 2

    async def test_focus_marker_moves_with_the_selection(self, three_bookmarks: Path) -> None:
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            rows = rows_of(app)
            assert marker_of(rows[0]) == tui.MARKER_SELECTED
            assert marker_of(rows[1]) == tui.MARKER_IDLE
            await pilot.press("down")
            await pilot.pause()
            assert marker_of(rows[0]) == tui.MARKER_IDLE
            assert marker_of(rows[1]) == tui.MARKER_SELECTED

    async def test_bookmark_at_maps_an_index(self, three_bookmarks: Path) -> None:
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            view = app.query_one(tui.BookmarkListView)
            assert view.bookmark_at(1) == Bookmark("beta", "/one/beta")
            assert view.bookmark_at(99) is None
            assert view.bookmark_at(None) is None


# ---------------------------------------------------------------------------
# Choosing a folder
# ---------------------------------------------------------------------------


class TestChoose:
    async def test_enter_writes_the_target_file(
        self, three_bookmarks: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / ".cd_tui_target"
        monkeypatch.setattr(config, "target_file_path", lambda: target)
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
        assert target.read_text(encoding="utf-8").strip() == "/one/beta"

    async def test_quit_writes_nothing(
        self, three_bookmarks: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / ".cd_tui_target"
        monkeypatch.setattr(config, "target_file_path", lambda: target)
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
        assert not target.exists()

    async def test_quit_clears_a_stale_target_file(
        self, three_bookmarks: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A leftover file from a crashed session must not hijack the next
        # Enter press in the user's shell.
        target = tmp_path / ".cd_tui_target"
        target.write_text("/stale/place", encoding="utf-8")
        monkeypatch.setattr(config, "target_file_path", lambda: target)
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
        assert not target.exists()

    async def test_enter_with_no_bookmarks_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / ".cd_tui_target"
        monkeypatch.setattr(config, "target_file_path", lambda: target)
        app = tui.CDTUIApp(config_file=tmp_path / "missing.json")
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
        assert not target.exists()

    async def test_write_failure_is_reported_and_the_app_stays_open(
        self, three_bookmarks: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(_path: str) -> None:
            raise ConfigError("disk on fire")

        monkeypatch.setattr(tui, "write_target_file", boom)
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.is_running
            messages = [str(n.message) for n in app._notifications]
            assert any("disk on fire" in message for message in messages)


# ---------------------------------------------------------------------------
# Reloading
# ---------------------------------------------------------------------------


class TestReload:
    async def test_r_picks_up_an_external_edit(self, three_bookmarks: Path) -> None:
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            save_bookmarks([Bookmark("delta", "/one/delta")], path=three_bookmarks)
            await pilot.press("r")
            await pilot.pause()
            assert [b.alias for b in app._bookmarks] == ["delta"]
            assert len(rows_of(app)) == 1

    async def test_r_clears_a_previous_error(self, tmp_path: Path) -> None:
        broken = tmp_path / "config.json"
        broken.write_text("{ not json", encoding="utf-8")
        app = tui.CDTUIApp(config_file=broken)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._load_error
            save_bookmarks([Bookmark("fixed", "/fixed")], path=broken)
            await pilot.press("r")
            await pilot.pause()
            assert not app._load_error
            assert not app.query_one("#error-banner").display

    async def test_r_on_an_empty_list_stays_empty(self, tmp_path: Path) -> None:
        app = tui.CDTUIApp(config_file=tmp_path / "missing.json")
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            assert app.query_one(tui.EmptyState).display

    async def test_r_keeps_a_valid_selection_in_range(self, three_bookmarks: Path) -> None:
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("end")
            save_bookmarks([Bookmark("only", "/only")], path=three_bookmarks)
            await pilot.press("r")
            await pilot.pause()
            assert app.query_one(tui.BookmarkListView).index == 0


# ---------------------------------------------------------------------------
# Launching the settings panel
# ---------------------------------------------------------------------------


class TestSettingsLauncher:
    async def test_a_launches_the_module_with_the_same_interpreter(
        self, three_bookmarks: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = install_subprocess_recorder(monkeypatch)
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()

        assert len(calls) == 1
        command, kwargs = calls[0]
        assert list(command) == [sys.executable, "-m", "cdtui.app", "--settings"]
        # The child must be able to import this copy of cdtui even from a
        # source checkout.
        source_root = str(Path(tui.__file__).resolve().parent.parent)
        assert source_root in kwargs["env"]["PYTHONPATH"]

    async def test_settings_edits_are_picked_up_on_close(
        self, three_bookmarks: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_subprocess_recorder(monkeypatch)
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Pretend the child edited the file while it was open.
            original = app._read_bookmarks

            def edit_then_read() -> None:
                save_bookmarks([Bookmark("edited", "/edited")], path=three_bookmarks)
                original()

            monkeypatch.setattr(app, "_read_bookmarks", edit_then_read)
            await pilot.press("a")
            await pilot.pause()
            await pilot.pause()
            assert [b.alias for b in app._bookmarks] == ["edited"]

    async def test_a_failure_reports_an_error(
        self, three_bookmarks: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_subprocess_recorder(monkeypatch, returncode=1)
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            messages = [str(n.message) for n in app._notifications]
            assert any("could not start" in message for message in messages)

    async def test_an_oserror_reports_instead_of_crashing(
        self, three_bookmarks: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def boom(*_args: Any, **_kwargs: Any) -> None:
            raise OSError("no interpreter")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            messages = [str(n.message) for n in app._notifications]
            assert any("no interpreter" in message for message in messages)

    async def test_a_tolerates_an_unsupported_suspend(
        self, three_bookmarks: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # App.suspend() is not implemented in every environment; losing it is
        # not a reason to refuse to open the settings panel.
        from textual.app import App

        calls = install_subprocess_recorder(monkeypatch)

        @contextmanager
        def boom() -> Iterator[None]:
            raise NotImplementedError("suspend is unsupported here")
            yield  # pragma: no cover - unreachable, keeps this a generator

        monkeypatch.setattr(App, "suspend", boom)
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
        assert len(calls) == 1

    async def test_a_twice_does_not_start_two_panels(
        self, three_bookmarks: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls = install_subprocess_recorder(monkeypatch, started=started, release=release)
        app = tui.CDTUIApp(config_file=three_bookmarks)
        async with app.run_test() as pilot:
            await pilot.pause()
            # The panel stays genuinely open until `release` is set, so the
            # second press really does land while _settings_open is True.
            calls_ = calls
            in_flight = asyncio.ensure_future(app.action_open_settings())
            await started.wait()
            await app.action_open_settings()
            assert app._settings_open
            release.set()
            await in_flight
            await pilot.pause()
        assert len(calls_) == 1
        assert not app._settings_open


# ---------------------------------------------------------------------------
# run_tui's contract
# ---------------------------------------------------------------------------


class TestRunTui:
    def test_returns_an_int(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # main() hands this straight to SystemExit, so it must be an int.
        started: list[Path | None] = []

        class FakeApp:
            def __init__(self, config_file: Path | None = None) -> None:
                started.append(config_file)

            def run(self) -> None:
                return None

        monkeypatch.setattr(tui, "CDTUIApp", FakeApp)
        result = tui.run_tui(tmp_path / "config.json")
        assert result == 0
        assert isinstance(result, int)
        assert started == [tmp_path / "config.json"]
