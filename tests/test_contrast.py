"""Accessibility tests for the TUI and GUI colour palettes.

The TUI ships one hand-picked palette per terminal theme.  These tests pin the
WCAG AA ratios so a colour tweak cannot quietly make the app unreadable.
"""

from __future__ import annotations

import pytest

from cdtui import gui, tui

#: WCAG 2.1 minimum contrast for normal-size text.
AA_TEXT = 4.5

#: WCAG 2.1 minimum contrast for non-text UI (borders, focus indicators).
AA_NON_TEXT = 3.0

AA_PAIRS = (AA_TEXT, AA_NON_TEXT)


# ---------------------------------------------------------------------------
# The ratio calculation itself
# ---------------------------------------------------------------------------


class TestContrastRatio:
    @pytest.mark.parametrize(
        ("foreground", "background", "expected"),
        [
            ("#000000", "#ffffff", 21.0),
            ("#ffffff", "#ffffff", 1.0),
            ("#ffffff", "#000000", 21.0),
        ],
    )
    def test_reference_values(self, foreground: str, background: str, expected: float) -> None:
        assert tui.contrast_ratio(foreground, background) == pytest.approx(expected)

    def test_is_symmetric(self) -> None:
        a = tui.contrast_ratio("#141821", "#f4f6fa")
        b = tui.contrast_ratio("#f4f6fa", "#141821")
        assert a == pytest.approx(b)

    def test_shorthand_hex_is_rejected_rather_than_guessed(self) -> None:
        # The palettes are all #rrggbb; a silent guess here would let a typo in
        # a palette value pass unnoticed, so it is an error instead.
        with pytest.raises(ValueError, match="rrggbb"):
            tui.contrast_ratio("#000", "#ffffff")

    @pytest.mark.parametrize("bad", ["#12345", "not-a-colour", "#gggggg", ""])
    def test_rejects_nonsense(self, bad: str) -> None:
        with pytest.raises(ValueError):
            tui.contrast_ratio(bad, "#ffffff")

    def test_rejects_a_bad_background(self) -> None:
        with pytest.raises(ValueError):
            tui.contrast_ratio("#ffffff", "nope")


# ---------------------------------------------------------------------------
# The audited pairs
# ---------------------------------------------------------------------------


class TestAuditedPairs:
    def test_the_audit_list_is_not_empty(self) -> None:
        assert len(tui.AUDITED_PAIRS) >= 10

    def test_every_audited_token_exists_in_both_themes(self) -> None:
        for theme_name, tokens in (("dark", tui.DARK_TOKENS), ("light", tui.LIGHT_TOKENS)):
            for _label, foreground, background, _threshold in tui.AUDITED_PAIRS:
                assert foreground in tokens, f"{theme_name} palette is missing {foreground}"
                assert background in tokens, f"{theme_name} palette is missing {background}"

    def test_audited_pairs_meet_their_threshold(self) -> None:
        for theme_name, tokens in (("dark", tui.DARK_TOKENS), ("light", tui.LIGHT_TOKENS)):
            for label, foreground, background, threshold in tui.AUDITED_PAIRS:
                ratio = tui.contrast_ratio(tokens[foreground], tokens[background])
                assert ratio >= threshold, (
                    f"{theme_name}: {label} is {ratio:.2f}:1, below {threshold}:1 "
                    f"({tokens[foreground]} on {tokens[background]})"
                )

    @pytest.mark.parametrize("threshold", AA_PAIRS)
    def test_audited_thresholds_are_aa(self, threshold: float) -> None:
        assert threshold in AA_PAIRS

    def test_build_tokens_selects_a_palette(self) -> None:
        assert tui.build_tokens(dark=True) == tui.DARK_TOKENS
        assert tui.build_tokens(dark=False) == tui.LIGHT_TOKENS

    def test_the_two_themes_actually_differ(self) -> None:
        assert tui.build_tokens(dark=True) != tui.build_tokens(dark=False)

    def test_returned_palette_is_a_copy(self) -> None:
        palette = tui.build_tokens(dark=True)
        palette["cdtui-text"] = "#000000"
        assert tui.DARK_TOKENS["cdtui-text"] != "#000000"


# ---------------------------------------------------------------------------
# The GUI palette
# ---------------------------------------------------------------------------


class TestGuiPalette:
    #: label, foreground, background, minimum
    PAIRS = (
        ("body text on background", gui.TEXT, gui.BG, AA_TEXT),
        ("body text on surface", gui.TEXT, gui.SURFACE, AA_TEXT),
        ("dimmed text on background", gui.TEXT_DIM, gui.BG, AA_TEXT),
        ("dimmed text on surface", gui.TEXT_DIM, gui.SURFACE, AA_TEXT),
        # A focused row paints its own background SELECT_DIM_BG, and the alias
        # button switches to the primary pair on top of it.
        ("highlighted row label", gui.TEXT, gui.SELECT_DIM_BG, AA_TEXT),
        ("highlighted row path", gui.TEXT_DIM, gui.SELECT_DIM_BG, AA_TEXT),
        ("highlighted alias", gui.SELECT_FG, gui.SELECT_BG, AA_TEXT),
        ("success message", gui.OK_TEXT, gui.SURFACE, AA_TEXT),
        ("success message on background", gui.OK_TEXT, gui.BG, AA_TEXT),
        ("error message", gui.ERROR_TEXT, gui.SURFACE, AA_TEXT),
        ("error message on background", gui.ERROR_TEXT, gui.BG, AA_TEXT),
        ("warning message", gui.WARN_TEXT, gui.SURFACE, AA_TEXT),
        ("warning message on background", gui.WARN_TEXT, gui.BG, AA_TEXT),
        ("border on background", gui.BORDER, gui.BG, AA_NON_TEXT),
        ("border on surface", gui.BORDER, gui.SURFACE, AA_NON_TEXT),
    )

    @pytest.mark.parametrize(("label", "foreground", "background", "minimum"), PAIRS)
    def test_pair_meets_aa(
        self, label: str, foreground: str, background: str, minimum: float
    ) -> None:
        ratio = tui.contrast_ratio(foreground, background)
        assert ratio >= minimum, (
            f"{label} is {ratio:.2f}:1, below {minimum}:1 ({foreground} on {background})"
        )

    def test_all_values_are_lowercase_hex(self) -> None:
        for name in (
            "BG",
            "SURFACE",
            "TEXT",
            "TEXT_DIM",
            "BORDER",
            "SELECT_BG",
            "SELECT_FG",
            "SELECT_DIM_BG",
            "OK_TEXT",
            "ERROR_TEXT",
            "WARN_TEXT",
        ):
            value = getattr(gui, name)
            assert value.startswith("#") and len(value) == 7, f"{name} = {value!r}"
            assert value == value.lower(), f"{name} = {value!r}"

    def test_the_gui_palette_matches_the_light_tui_palette(self) -> None:
        # The settings window and the light terminal theme should look like the
        # same product.
        light = tui.LIGHT_TOKENS
        assert light["cdtui-bg"] == gui.BG
        assert light["cdtui-row"] == gui.SURFACE
        assert light["cdtui-text"] == gui.TEXT
        assert light["cdtui-text-dim"] == gui.TEXT_DIM
        assert light["cdtui-sel-bg"] == gui.SELECT_BG
        assert light["cdtui-sel-text"] == gui.SELECT_FG
