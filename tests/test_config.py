"""Tests for :mod:`cdtui.config`.

These cover the parts that must never be wrong: path resolution, the JSON
schema, atomic writes, and the shell handoff file.  None of it needs a
terminal or a display.
"""

from __future__ import annotations

import dataclasses
import json
import os
import stat
from pathlib import Path

import pytest

from cdtui import config
from cdtui.config import Bookmark, ConfigError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """A path for a scratch config file that does not exist yet."""
    return tmp_path / "cd-tui" / "config.json"


@pytest.fixture
def sample() -> list[Bookmark]:
    return [
        Bookmark("alpha", "/one/alpha"),
        Bookmark("beta", "/one/beta"),
        Bookmark("gamma", "/one/gamma"),
    ]


# ---------------------------------------------------------------------------
# Config location
# ---------------------------------------------------------------------------


class TestConfigLocation:
    def test_default_location_is_under_the_user_config_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr(config.sys, "platform", "linux")
        assert config.config_dir() == tmp_path / "cd-tui"

    def test_macos_uses_the_posix_convention_not_the_native_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # platformdirs' native macOS backend would use ~/Library/Application
        # Support. The documented contract is ~/.config everywhere on POSIX.
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr(config.sys, "platform", "darwin")
        assert config.config_dir() == tmp_path / "cd-tui"
        assert "Library" not in str(config.config_dir())

    def test_windows_uses_appdata(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config.sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        assert config.config_dir() == tmp_path / "Roaming" / "cd-tui"

    def test_falls_back_when_platformdirs_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(_name: str) -> None:
            raise ImportError("no platformdirs here")

        monkeypatch.setattr(config, "import_module", boom)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert config.config_dir() == tmp_path / "cd-tui"

    def test_file_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert config.config_path() == tmp_path / "cd-tui" / "config.json"

    def test_ensure_config_dir_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        first = config.ensure_config_dir()
        assert first.is_dir()
        assert config.ensure_config_dir() == first

    def test_target_file_is_dot_prefixed_in_the_home_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: Path("/home/u")))
        assert config.target_file_path() == Path("/home/u/.cd_tui_target")


# ---------------------------------------------------------------------------
# Bookmark parsing
# ---------------------------------------------------------------------------


class TestBookmarkFromDict:
    def test_round_trip(self) -> None:
        assert Bookmark.from_dict({"alias": "a", "path": "/p"}) == Bookmark("a", "/p")

    @pytest.mark.parametrize(
        "raw",
        [
            "not a mapping",
            42,
            None,
            [],
            {"alias": "a"},
            {"path": "/p"},
            {"alias": "", "path": "/p"},
            {"alias": "   ", "path": "/p"},
            {"alias": "a", "path": ""},
            {"alias": 5, "path": "/p"},
        ],
    )
    def test_rejects_junk(self, raw: object) -> None:
        with pytest.raises(ConfigError):
            Bookmark.from_dict(raw)

    def test_error_names_the_field(self) -> None:
        with pytest.raises(ConfigError, match="alias"):
            Bookmark.from_dict({"alias": "", "path": "/p"})

    def test_alias_is_trimmed(self) -> None:
        assert Bookmark.from_dict({"alias": "  a  ", "path": " /p "}) == Bookmark("a", "/p")

    def test_exists_reports_directory_state(self, tmp_path: Path) -> None:
        assert Bookmark("a", str(tmp_path)).exists
        assert not Bookmark("a", str(tmp_path / "nope")).exists

    def test_is_hashable_and_frozen(self) -> None:
        assert len({Bookmark("a", "/p"), Bookmark("a", "/p")}) == 1
        with pytest.raises(dataclasses.FrozenInstanceError):
            Bookmark("a", "/p").alias = "b"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_normalize_makes_absolute(self) -> None:
        assert os.path.isabs(config.normalize_path("."))

    def test_normalize_expands_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/home/tester")
        assert config.normalize_path("~/projects") == "/home/tester/projects"

    def test_normalize_strips_trailing_separator(self) -> None:
        assert config.normalize_path("/a/b/") == "/a/b"

    def test_default_alias_uses_the_folder_name(self) -> None:
        assert config.default_alias("/home/u/src/my-project") == "my-project"

    def test_default_alias_is_stable_for_one_folder(self, tmp_path: Path) -> None:
        first = config.default_alias(str(tmp_path))
        assert first == config.default_alias(str(tmp_path))


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


class TestCrud:
    def test_add_appends(self, sample: list[Bookmark]) -> None:
        out = config.add_bookmark(sample, "delta", "/one/delta")
        assert [b.alias for b in out] == ["alpha", "beta", "gamma", "delta"]

    def test_add_rejects_a_blank_alias(self, sample: list[Bookmark]) -> None:
        # Suggesting a default is the GUI's job (default_alias); the storage
        # layer stays strict so a blank alias can never reach the file.
        with pytest.raises(ConfigError, match="alias"):
            config.add_bookmark(sample, "  ", "/one/delta")

    def test_default_alias_feeds_a_suggested_add(self, sample: list[Bookmark]) -> None:
        suggested = config.default_alias("/one/delta")
        out = config.add_bookmark(sample, suggested, "/one/delta")
        assert out[-1] == Bookmark("delta", "/one/delta")

    def test_add_rejects_a_blank_path(self, sample: list[Bookmark]) -> None:
        with pytest.raises(ConfigError, match="path"):
            config.add_bookmark(sample, "delta", "   ")

    def test_add_rejects_a_duplicate_alias(self, sample: list[Bookmark]) -> None:
        with pytest.raises(ConfigError, match="already exists"):
            config.add_bookmark(sample, "alpha", "/elsewhere")

    def test_add_rejects_a_duplicate_alias_case_insensitively(self, sample: list[Bookmark]) -> None:
        with pytest.raises(ConfigError, match="already exists"):
            config.add_bookmark(sample, "ALPHA", "/elsewhere")

    def test_add_normalizes_the_path(self, sample: list[Bookmark]) -> None:
        out = config.add_bookmark(sample, "delta", "/one/delta/")
        assert out[-1].path == "/one/delta"

    def test_input_is_not_mutated(self, sample: list[Bookmark]) -> None:
        config.add_bookmark(sample, "delta", "/one/delta")
        assert len(sample) == 3

    def test_remove(self, sample: list[Bookmark]) -> None:
        assert [b.alias for b in config.remove_bookmark(sample, 1)] == ["alpha", "gamma"]

    @pytest.mark.parametrize("index", [-1, 3, 99])
    def test_remove_rejects_out_of_range(self, sample: list[Bookmark], index: int) -> None:
        with pytest.raises(ConfigError):
            config.remove_bookmark(sample, index)

    def test_move_up_and_down(self, sample: list[Bookmark]) -> None:
        assert [b.alias for b in config.move_bookmark(sample, 2, -1)] == [
            "alpha",
            "gamma",
            "beta",
        ]
        assert [b.alias for b in config.move_bookmark(sample, 0, +1)] == [
            "beta",
            "alpha",
            "gamma",
        ]

    def test_move_beyond_the_edge_leaves_the_order_untouched(self, sample: list[Bookmark]) -> None:
        assert config.move_bookmark(sample, 2, +1) == sample
        assert config.move_bookmark(sample, 0, -1) == sample

    def test_move_rejects_a_bad_index(self, sample: list[Bookmark]) -> None:
        with pytest.raises(ConfigError):
            config.move_bookmark(sample, 9, -1)

    def test_rename(self, sample: list[Bookmark]) -> None:
        out = config.rename_bookmark(sample, 1, "  beta2 ")
        assert out[1] == Bookmark("beta2", "/one/beta")
        assert out[0] == sample[0]

    def test_rename_to_the_same_alias_is_a_no_op(self, sample: list[Bookmark]) -> None:
        assert config.rename_bookmark(sample, 1, "beta") == sample

    def test_rename_onto_another_alias_is_rejected(self, sample: list[Bookmark]) -> None:
        with pytest.raises(ConfigError, match="already exists"):
            config.rename_bookmark(sample, 1, "alpha")

    def test_rename_to_blank_is_rejected(self, sample: list[Bookmark]) -> None:
        with pytest.raises(ConfigError, match="alias"):
            config.rename_bookmark(sample, 1, "  ")

    def test_replace_path(self, sample: list[Bookmark]) -> None:
        out = config.replace_bookmark_path(sample, 0, "/two/alpha/")
        assert out[0] == Bookmark("alpha", "/two/alpha")

    def test_replace_path_rejects_blank(self, sample: list[Bookmark]) -> None:
        with pytest.raises(ConfigError, match="path"):
            config.replace_bookmark_path(sample, 0, "")


# ---------------------------------------------------------------------------
# Reading and writing the file
# ---------------------------------------------------------------------------


class TestSaveAndLoad:
    def test_missing_file_is_an_empty_list(self, config_file: Path) -> None:
        assert config.load_bookmarks(config_file) == []

    def test_missing_file_does_not_create_the_default_config_dir(
        self, tmp_path: Path, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A --config override must not have the side effect of materialising
        # ~/.config/cd-tui on the user's machine.
        fake_home = tmp_path / "home"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
        assert config.load_bookmarks(config_file) == []
        assert not (fake_home / ".config" / "cd-tui").exists()

    def test_round_trip(self, config_file: Path, sample: list[Bookmark]) -> None:
        config.save_bookmarks(sample, path=config_file)
        assert config.load_bookmarks(config_file) == sample

    def test_written_shape(self, config_file: Path, sample: list[Bookmark]) -> None:
        config.save_bookmarks(sample, path=config_file)
        document = json.loads(config_file.read_text(encoding="utf-8"))
        assert document == {
            "version": 1,
            "bookmarks": [
                {"alias": "alpha", "path": "/one/alpha"},
                {"alias": "beta", "path": "/one/beta"},
                {"alias": "gamma", "path": "/one/gamma"},
            ],
        }

    def test_creates_missing_parent_directories(
        self, tmp_path: Path, sample: list[Bookmark]
    ) -> None:
        target = tmp_path / "deeply" / "nested" / "config.json"
        config.save_bookmarks(sample, path=target)
        assert config.load_bookmarks(target) == sample

    def test_ends_with_a_newline_for_hand_editing(
        self, config_file: Path, sample: list[Bookmark]
    ) -> None:
        config.save_bookmarks(sample, path=config_file)
        assert config_file.read_text(encoding="utf-8").endswith("\n")

    def test_empty_list_round_trips(self, config_file: Path) -> None:
        config.save_bookmarks([], path=config_file)
        assert config.load_bookmarks(config_file) == []

    def test_accepts_a_bare_empty_list_as_no_bookmarks(self, config_file: Path) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text("[]", encoding="utf-8")
        assert config.load_bookmarks(config_file) == []

    def test_accepts_a_bare_list_document(self, config_file: Path) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps([{"alias": "a", "path": "/p"}]), encoding="utf-8")
        assert config.load_bookmarks(config_file) == [Bookmark("a", "/p")]

    def test_overwrite_replaces_rather_than_merges(
        self, config_file: Path, sample: list[Bookmark]
    ) -> None:
        config.save_bookmarks(sample, path=config_file)
        config.save_bookmarks([Bookmark("only", "/only")], path=config_file)
        assert config.load_bookmarks(config_file) == [Bookmark("only", "/only")]

    def test_rejects_duplicate_aliases_on_disk(self, config_file: Path) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "bookmarks": [
                        {"alias": "a", "path": "/1"},
                        {"alias": "A", "path": "/2"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match=r"[Aa]"):
            config.load_bookmarks(config_file)

    @pytest.mark.parametrize(
        "content",
        [
            "{not json",
            '{"version": 1, "bookmarks": {}}',
            '{"bookmarks": "nope"}',
            '{"version": 1, "bookmarks": [{"alias": "a"}]}',
            '"a string"',
            "42",
        ],
    )
    def test_malformed_content_names_the_file(self, config_file: Path, content: str) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text(content, encoding="utf-8")
        with pytest.raises(ConfigError, match=r"config\.json"):
            config.load_bookmarks(config_file)

    def test_malformed_entry_names_its_position(self, config_file: Path) -> None:
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            json.dumps({"version": 1, "bookmarks": [{"alias": "ok", "path": "/1"}, 7]}),
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match=r"2|index"):
            config.load_bookmarks(config_file)

    def test_write_failure_is_reported(self, config_file: Path, sample: list[Bookmark]) -> None:
        # A directory where the file should be makes the write fail.
        config_file.mkdir(parents=True)
        with pytest.raises(ConfigError):
            config.save_bookmarks(sample, path=config_file)

    def test_a_duplicate_saved_some_other_way_is_caught_on_read(self, config_file: Path) -> None:
        # Nothing in the UI can create a duplicate, but the file is plain text
        # and hand-editable, so the read path is the gate that matters.
        config.save_bookmarks([Bookmark("a", "/1"), Bookmark("A", "/2")], path=config_file)
        with pytest.raises(ConfigError, match="duplicate"):
            config.load_bookmarks(config_file)

    def test_leaves_no_temporary_files_behind(
        self, config_file: Path, sample: list[Bookmark]
    ) -> None:
        config.save_bookmarks(sample, path=config_file)
        leftovers = [p.name for p in config_file.parent.iterdir() if p != config_file]
        assert leftovers == []


# ---------------------------------------------------------------------------
# The shell handoff file
# ---------------------------------------------------------------------------


class TestTargetFile:
    def test_path_is_in_the_home_directory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: Path("/home/u")))
        assert config.target_file_path() == Path("/home/u/.cd_tui_target")

    def test_write_then_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "target_file_path", lambda: tmp_path / ".cd_tui_target")
        written = config.write_target_file("/some/folder")
        assert written == tmp_path / ".cd_tui_target"
        assert config.read_target_file() == "/some/folder"

    def test_content_is_usable_by_a_shell_substitution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The wrappers do target=$(cat file); make sure no stray quoting or
        # NUL bytes would break that.
        monkeypatch.setattr(config, "target_file_path", lambda: tmp_path / ".cd_tui_target")
        config.write_target_file("/a folder/with spaces & $dollar")
        assert config.read_target_file() == "/a folder/with spaces & $dollar"

    def test_is_owner_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "target_file_path", lambda: tmp_path / ".cd_tui_target")
        written = config.write_target_file("/x")
        mode = stat.S_IMODE(written.stat().st_mode)
        assert mode == 0o600, f"expected 0600, got {mode:o}"

    def test_stored_path_is_absolute(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "target_file_path", lambda: tmp_path / ".cd_tui_target")
        config.write_target_file("/definitely/absolute")
        assert os.path.isabs(config.read_target_file() or "")

    def test_read_missing_file_is_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "target_file_path", lambda: tmp_path / ".cd_tui_target")
        assert config.read_target_file() is None

    def test_read_blank_file_is_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / ".cd_tui_target"
        target.write_text("   \n", encoding="utf-8")
        monkeypatch.setattr(config, "target_file_path", lambda: target)
        assert config.read_target_file() is None

    def test_clear_is_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / ".cd_tui_target"
        monkeypatch.setattr(config, "target_file_path", lambda: target)
        config.clear_target_file()
        config.clear_target_file()
        assert not target.exists()

    def test_clear_removes_an_existing_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / ".cd_tui_target"
        target.write_text("/x", encoding="utf-8")
        monkeypatch.setattr(config, "target_file_path", lambda: target)
        config.clear_target_file()
        assert not target.exists()

    def test_write_failure_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Put a regular file where the target's home directory should be, so
        # creating the temporary file cannot succeed.
        blocker = tmp_path / "blocked"
        blocker.write_text("not a directory", encoding="utf-8")
        monkeypatch.setattr(config, "target_file_path", lambda: blocker / ".cd_tui_target")
        with pytest.raises(ConfigError):
            config.write_target_file("/x")


class TestEffectiveConfigPath:
    def test_the_default_is_used_when_there_is_no_override(self) -> None:
        assert config.effective_config_path() == config.config_path()
        assert config.effective_config_path(None) == config.config_path()

    def test_an_override_wins(self) -> None:
        assert config.effective_config_path("/tmp/other.json") == Path("/tmp/other.json")

    def test_a_path_override_is_kept(self, tmp_path: Path) -> None:
        assert config.effective_config_path(tmp_path) == tmp_path

    def test_an_empty_override_is_still_an_override(self) -> None:
        # "" is falsy, so a naive `override or config_path()` would silently
        # ignore it; the type is Path | str | None, so None is the only default.
        assert config.effective_config_path("") == Path("")

    def test_describe_config_location_mentions_the_override(self) -> None:
        text = config.describe_config_location("/tmp/other.json")
        assert "/tmp/other.json" in text
        assert "config" in text
