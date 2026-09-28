"""Bookmark storage, configuration paths and shell handoff file for CD-TUI.

Everything in this module is deliberately free of user-interface concerns so it
can be unit tested without a terminal or a display server.  ``cdtui.gui`` and
``cdtui.tui`` are thin layers on top of the functions defined here.

No path in this module is hard coded to a particular machine: locations are
resolved at call time from :mod:`platformdirs` (when importable) or from the
portable environment variables ``%APPDATA%`` / ``$XDG_CONFIG_HOME`` / ``$HOME``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Final

from cdtui import APP_NAME, TARGET_FILE_NAME

__all__ = [
    "Bookmark",
    "ConfigError",
    "add_bookmark",
    "clear_target_file",
    "config_dir",
    "config_path",
    "default_alias",
    "ensure_config_dir",
    "load_bookmarks",
    "move_bookmark",
    "normalize_path",
    "read_target_file",
    "remove_bookmark",
    "rename_bookmark",
    "replace_bookmark_path",
    "save_bookmarks",
    "target_file_path",
    "write_target_file",
]

#: ``platformdirs`` application identifier (the directory created under the
#: platform config root).  Lower case with a dash, per platformdirs guidance.
_APP_IDENTIFIER: Final[str] = "cd-tui"


def _on_windows() -> bool:
    """Report whether this is Windows, in a form type checkers cannot fold.

    Written inline, ``sys.platform == "win32"`` lets mypy resolve the branch
    against the interpreter running the check, which marked everything after the
    Windows early-return as unreachable and failed the Windows CI job. Caching it
    in a module constant fixes that but freezes the value at import time, so the
    other platform's path can no longer be exercised by a test. Going through a
    function keeps both properties: mypy cannot prove the result, and patching
    ``sys.platform`` still takes effect.
    """
    return sys.platform == "win32"


#: Schema version stored alongside the bookmark list for future migrations.
_SCHEMA_VERSION: Final[int] = 1


class ConfigError(RuntimeError):
    """Raised when the configuration file cannot be read, parsed or written.

    The message is intended to be shown verbatim to the user, so it always
    names the offending file and the concrete reason.
    """


@dataclass(frozen=True, slots=True)
class Bookmark:
    """A single directory bookmark.

    Attributes:
        alias: Short, human friendly name shown in the TUI and GUI.
        path: Absolute path of the directory to change into.
    """

    alias: str
    path: str

    def to_dict(self) -> dict[str, str]:
        """Return the bookmark as a JSON-serialisable mapping."""
        return {"alias": self.alias, "path": self.path}

    @classmethod
    def from_dict(cls, raw: Any) -> Bookmark:
        """Build a :class:`Bookmark` from a decoded JSON entry.

        Args:
            raw: Value decoded from the configuration file.

        Returns:
            The parsed bookmark.

        Raises:
            ConfigError: If ``raw`` is not a mapping of two non-empty strings.
        """
        if not isinstance(raw, dict):
            raise ConfigError(
                f"expected an object with 'alias' and 'path' keys, got {type(raw).__name__}"
            )
        alias = raw.get("alias")
        path = raw.get("path")
        if not isinstance(alias, str) or not isinstance(path, str):
            raise ConfigError("'alias' and 'path' must both be strings")
        alias = alias.strip()
        path = path.strip()
        if not alias:
            raise ConfigError("'alias' must not be empty")
        if not path:
            raise ConfigError("'path' must not be empty")
        return cls(alias=alias, path=path)

    @property
    def exists(self) -> bool:
        """``True`` when the bookmarked directory is present on this machine."""
        return Path(self.path).is_dir()


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _fallback_config_dir() -> Path:
    """Resolve the config directory without :mod:`platformdirs`.

    Mirrors the platformdirs layout so that the two code paths agree:

    * Windows: ``%APPDATA%\\cd-tui``
    * macOS / Linux: ``$XDG_CONFIG_HOME/cd-tui`` or ``~/.config/cd-tui``
    """
    if _on_windows():
        base = os.environ.get("APPDATA") or ""
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / _APP_IDENTIFIER
    base = os.environ.get("XDG_CONFIG_HOME") or ""
    if not base:
        base = str(Path.home() / ".config")
    return Path(base) / _APP_IDENTIFIER


def config_dir() -> Path:
    """Return the directory holding ``config.json``.

    Resolution goes through :mod:`platformdirs` so the usual ``XDG_CONFIG_HOME``
    and roaming-profile behaviour is respected, with two deliberate choices:

    * macOS uses platformdirs' *Unix* backend rather than its native one, so
      configuration lives in ``~/.config/cd-tui`` like on Linux instead of
      ``~/Library/Application Support/cd-tui``.
    * Windows uses the roaming profile, i.e. ``%APPDATA%\\cd-tui``.

    If :mod:`platformdirs` is missing, or returns something unusable, a manual
    implementation of the same layout is used instead.  The directory is *not*
    created here; call :func:`ensure_config_dir` for that.
    """
    backend = "windows" if _on_windows() else "unix"
    try:
        module = import_module(f"platformdirs.{backend}")
        if backend == "unix":
            directories = module.Unix(appname=_APP_IDENTIFIER, appauthor=False)
        else:
            directories = module.Windows(appname=_APP_IDENTIFIER, appauthor=False, roaming=True)
        candidate = Path(directories.user_config_dir)
    except Exception:
        return _fallback_config_dir()
    if not candidate.is_absolute():
        return _fallback_config_dir()
    return candidate


def config_path() -> Path:
    """Return the full path of the bookmark configuration file."""
    return config_dir() / "config.json"


def effective_config_path(override: Path | str | None = None) -> Path:
    """Return the config file in use, honouring a ``--config`` override.

    Every user-facing "here is your file" message has to go through this,
    otherwise a run with ``--config`` points the user at a file the app is
    not actually reading.
    """
    return Path(override) if override is not None else config_path()


def ensure_config_dir() -> Path:
    """Create the configuration directory if needed and return it."""
    directory = config_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"cannot create config directory {directory}: {exc}") from exc
    return directory


def target_file_path() -> Path:
    """Return the path of the shell handoff file (``~/.cd_tui_target``).

    ``Path.home()`` resolves to ``%USERPROFILE%`` on Windows and ``$HOME`` on
    Unix-like systems, which is exactly what the shell wrappers read.
    """
    return Path.home() / TARGET_FILE_NAME


# ---------------------------------------------------------------------------
# Path / alias helpers
# ---------------------------------------------------------------------------


def normalize_path(raw: str) -> str:
    """Expand and absolutise a user supplied path.

    ``~`` and environment variables are expanded, and the result is made
    absolute against the current working directory.  Symlinks are deliberately
    *not* resolved so that a bookmark pointing at a symlinked directory keeps
    pointing at that symlink.

    Args:
        raw: Path text as typed by the user or chosen in the folder picker.

    Returns:
        An absolute, normalised path string.

    Raises:
        ConfigError: If the value is empty or expands to nothing.
    """
    text = raw.strip()
    if not text:
        raise ConfigError("path must not be empty")
    text = os.path.expandvars(text)
    text = os.path.expanduser(text)
    if not text:
        raise ConfigError("path must not be empty")
    return os.path.normpath(os.path.abspath(text))


def default_alias(path: str) -> str:
    """Derive a sensible alias from a directory path.

    Uses the final path component, falling back to the drive/root label when
    the path points at a filesystem root such as ``/`` or ``C:\\``.

    Args:
        path: Absolute directory path.

    Returns:
        A non-empty alias suitable for display.
    """
    normalized = path.rstrip(os.sep).rstrip("/")
    if not normalized:
        return os.path.abspath(path)
    name = os.path.basename(normalized)
    return name or os.path.abspath(path)


# ---------------------------------------------------------------------------
# Pure list operations (shared by the GUI and the TUI)
# ---------------------------------------------------------------------------


def add_bookmark(bookmarks: Sequence[Bookmark], alias: str, path: str) -> list[Bookmark]:
    """Return a new list with ``alias``/``path`` appended.

    Args:
        bookmarks: Existing bookmarks, in display order.
        alias: Display name; must not be blank.
        path: Directory path; normalised before storing.

    Returns:
        A new list; the input sequence is left untouched.

    Raises:
        ConfigError: If the alias is blank or duplicates an existing alias, or
            the path is blank.
    """
    clean_alias = alias.strip()
    if not clean_alias:
        raise ConfigError("alias must not be empty")
    if any(existing.alias.casefold() == clean_alias.casefold() for existing in bookmarks):
        raise ConfigError(f"a bookmark named {clean_alias!r} already exists")
    return [*bookmarks, Bookmark(alias=clean_alias, path=normalize_path(path))]


def remove_bookmark(bookmarks: Sequence[Bookmark], index: int) -> list[Bookmark]:
    """Return a new list with the bookmark at ``index`` removed.

    Args:
        bookmarks: Existing bookmarks, in display order.
        index: Position of the bookmark to drop.

    Returns:
        A new list without that entry.

    Raises:
        ConfigError: If ``index`` is out of range.
    """
    _check_index(index, len(bookmarks), "bookmark")
    return [b for position, b in enumerate(bookmarks) if position != index]


def move_bookmark(bookmarks: Sequence[Bookmark], index: int, offset: int) -> list[Bookmark]:
    """Return a new list with the bookmark at ``index`` shifted by ``offset``.

    Args:
        bookmarks: Existing bookmarks, in display order.
        index: Position of the bookmark to move.
        offset: ``-1`` to move up, ``+1`` to move down.

    Returns:
        A new, reordered list.

    Raises:
        ConfigError: If ``index`` is out of range or the move would leave the
            list (in which case the original order is returned unchanged).
    """
    _check_index(index, len(bookmarks), "bookmark")
    target = index + offset
    if target < 0 or target >= len(bookmarks):
        return list(bookmarks)
    reordered = list(bookmarks)
    # Shift rather than swap, so an offset of ±1 is a one-step move and a
    # larger offset still lands the bookmark where the caller asked for.
    moved = reordered.pop(index)
    reordered.insert(target, moved)
    return reordered


def rename_bookmark(bookmarks: Sequence[Bookmark], index: int, alias: str) -> list[Bookmark]:
    """Return a new list with the bookmark at ``index`` renamed.

    Args:
        bookmarks: Existing bookmarks, in display order.
        index: Position of the bookmark to rename.
        alias: New display name; must not be blank or collide with a sibling.

    Returns:
        A new list with the rename applied.

    Raises:
        ConfigError: If ``index`` is out of range, the alias is blank, or the
            alias is already used by a different bookmark.
    """
    _check_index(index, len(bookmarks), "bookmark")
    clean_alias = alias.strip()
    if not clean_alias:
        raise ConfigError("alias must not be empty")
    for position, existing in enumerate(bookmarks):
        if position != index and existing.alias.casefold() == clean_alias.casefold():
            raise ConfigError(f"a bookmark named {clean_alias!r} already exists")
    reordered = list(bookmarks)
    reordered[index] = Bookmark(alias=clean_alias, path=reordered[index].path)
    return reordered


def replace_bookmark_path(bookmarks: Sequence[Bookmark], index: int, path: str) -> list[Bookmark]:
    """Return a new list with the bookmark at ``index`` pointed elsewhere.

    Args:
        bookmarks: Existing bookmarks, in display order.
        index: Position of the bookmark to repoint.
        path: New directory path; normalised before storing.

    Returns:
        A new list with the updated path.

    Raises:
        ConfigError: If ``index`` is out of range.
    """
    _check_index(index, len(bookmarks), "bookmark")
    reordered = list(bookmarks)
    reordered[index] = Bookmark(alias=reordered[index].alias, path=normalize_path(path))
    return reordered


def _check_index(index: int, length: int, what: str) -> None:
    """Validate a list index, raising :class:`ConfigError` when out of range."""
    if not isinstance(index, int) or isinstance(index, bool):
        raise ConfigError(f"{what} index must be an integer, got {index!r}")
    if not 0 <= index < length:
        raise ConfigError(f"{what} index {index} is out of range (0..{max(length - 1, 0)})")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _coerce_document(raw: Any) -> list[Any]:
    """Extract the bookmark array from a decoded configuration document.

    Accepts either the documented envelope
    ``{"version": 1, "bookmarks": [...]}`` or a bare top-level list, so files
    hand-written against the initial schema keep working.
    """
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, dict) and isinstance(raw.get("bookmarks"), list):
        return list(raw["bookmarks"])
    raise ConfigError("expected a list of bookmarks, or an object with a 'bookmarks' list")


def load_bookmarks(path: Path | None = None) -> list[Bookmark]:
    """Read bookmarks from disk, creating the config directory if needed.

    A missing file is not an error: it simply yields an empty list, which is
    what a fresh installation should see.  Malformed content *is* an error and
    raises :class:`ConfigError` naming the file and the problem, so the user is
    never silently given a truncated bookmark list.

    Only the *default* location is created eagerly.  When ``path`` is given the
    file's own directory is left alone, so pointing the app at a scratch file
    never has the side effect of materialising ``~/.config/cd-tui``.

    Args:
        path: Override for the configuration file (used by tests).

    Returns:
        Bookmarks in stored (display) order.
    """
    if path is None:
        ensure_config_dir()
    target = path if path is not None else config_path()
    if not target.exists():
        return []
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {target}: {exc}") from exc
    if not text.strip():
        return []
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{target} is not valid JSON: {exc}") from exc

    try:
        entries = _coerce_document(document)
    except ConfigError as exc:
        raise ConfigError(f"{target}: {exc}") from exc
    bookmarks: list[Bookmark] = []
    for position, entry in enumerate(entries):
        try:
            bookmarks.append(Bookmark.from_dict(entry))
        except ConfigError as exc:
            raise ConfigError(f"{target}: bookmark #{position + 1} is invalid: {exc}") from exc

    duplicates = _duplicate_aliases(bookmarks)
    if duplicates:
        joined = ", ".join(repr(alias) for alias in duplicates)
        raise ConfigError(f"{target}: duplicate bookmark aliases: {joined}")
    return bookmarks


def _duplicate_aliases(bookmarks: Iterable[Bookmark]) -> list[str]:
    """Return aliases that appear more than once (case-insensitively)."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for bookmark in bookmarks:
        key = bookmark.alias.casefold()
        if key in seen and bookmark.alias not in duplicates:
            duplicates.append(bookmark.alias)
        seen.add(key)
    return duplicates


def save_bookmarks(bookmarks: Sequence[Bookmark], path: Path | None = None) -> Path:
    """Atomically write ``bookmarks`` to the configuration file.

    The payload is written to a temporary file in the destination directory and
    then moved into place with :func:`os.replace`, so an interrupted write can
    never leave a half-written config behind.

    Args:
        bookmarks: Bookmarks to persist, in display order.
        path: Override for the configuration file (used by tests).

    Returns:
        The path that was written.

    Raises:
        ConfigError: If the directory cannot be created or the write fails.
    """
    target = path if path is not None else config_path()
    directory = target.parent if str(target.parent) else ensure_config_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"cannot create config directory {directory}: {exc}") from exc

    document: dict[str, Any] = {
        "version": _SCHEMA_VERSION,
        "bookmarks": [bookmark.to_dict() for bookmark in bookmarks],
    }
    payload = json.dumps(document, indent=2, ensure_ascii=False) + "\n"

    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(directory),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
        tmp_name = None
    except OSError as exc:
        raise ConfigError(f"cannot write {target}: {exc}") from exc
    finally:
        if tmp_name is not None and os.path.exists(tmp_name):
            # Best-effort cleanup: a failure here must not mask the real error.
            with suppress(OSError):  # pragma: no cover - hard to force
                os.unlink(tmp_name)
    return target


# ---------------------------------------------------------------------------
# Shell handoff file
# ---------------------------------------------------------------------------


def write_target_file(path: str, destination: Path | None = None) -> Path:
    """Write the selected directory to the handoff file for the shell wrapper.

    The file is created with a restrictive mode (``0600``) because it is read
    back by the parent shell immediately afterwards.

    Args:
        path: Absolute directory path the user selected.
        destination: Override for the handoff file (used by tests).

    Returns:
        The path that was written.

    Raises:
        ConfigError: If the file cannot be written.
    """
    target = destination if destination is not None else target_file_path()
    text = normalize_path(path) + os.linesep
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
        tmp_name = None
    except OSError as exc:
        raise ConfigError(f"cannot write {target}: {exc}") from exc
    finally:
        if tmp_name is not None and os.path.exists(tmp_name):
            # Best-effort cleanup: a failure here must not mask the real error.
            with suppress(OSError):  # pragma: no cover - hard to force
                os.unlink(tmp_name)
    return target


def read_target_file(destination: Path | None = None) -> str | None:
    """Read the handoff file, returning the path or ``None`` when absent.

    The file is *not* removed; the shell wrapper is responsible for deleting it
    so that it can decide whether to act on the contents.
    """
    target = destination if destination is not None else target_file_path()
    try:
        return target.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigError(f"cannot read {target}: {exc}") from exc


def clear_target_file(destination: Path | None = None) -> None:
    """Delete a stale handoff file, ignoring the case where it is absent."""
    target = destination if destination is not None else target_file_path()
    try:
        target.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ConfigError(f"cannot remove {target}: {exc}") from exc


def describe_config_location(override: Path | str | None = None) -> str:
    """Return a one-line, user friendly description of the config location."""
    return f"{APP_NAME} config: {effective_config_path(override)}"
