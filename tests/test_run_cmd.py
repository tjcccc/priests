"""Tests for priests/cli/run_cmd.py helpers.

Covers _build_memory_context (both branches) and priests/search.py.
The _run_chat loop itself is not tested here — too much async REPL machinery.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# _build_memory_context — consolidate=False (Loaded Memories branch)
# ---------------------------------------------------------------------------


def test_build_memory_context_non_consolidation_all_files(tmp_path):
    """All three files populated: all three sections appear in output."""
    from priests.cli.run_cmd import _build_memory_context

    (tmp_path / "user.md").write_text("user fact")
    (tmp_path / "notes.md").write_text("notes fact")
    (tmp_path / "auto_short.md").write_text("# Short Memories\n\n## 2026-01-01\n\nauto fact\n")

    result = _build_memory_context(tmp_path, 50000, 0, False)

    assert "## Loaded Memories" in result
    assert "user fact" in result
    assert "notes fact" in result
    assert "auto fact" in result


def test_build_memory_context_non_consolidation_partial_files(tmp_path):
    """Only populated files appear — missing files produce no section."""
    from priests.cli.run_cmd import _build_memory_context

    (tmp_path / "user.md").write_text("user fact")
    # notes.md and auto_short.md absent

    result = _build_memory_context(tmp_path, 50000, 0, False)

    assert "## Loaded Memories" in result
    assert "user fact" in result
    assert "notes" not in result.lower() or "Behavioural notes" not in result
    assert "Recent context" not in result


def test_build_memory_context_non_consolidation_no_files(tmp_path):
    """No memory files: Loaded Memories header must not appear."""
    from priests.cli.run_cmd import _build_memory_context

    result = _build_memory_context(tmp_path, 50000, 0, False)

    assert "## Loaded Memories" not in result
    # The append instruction should still be present
    assert "memory_append" in result


def test_build_memory_context_non_consolidation_context_limit(tmp_path):
    """context_limit truncates auto_short on non-consolidation turns too."""
    from priests.cli.run_cmd import _build_memory_context

    (tmp_path / "user.md").write_text("u" * 50)
    (tmp_path / "notes.md").write_text("n" * 50)
    # Large auto_short with multiple sections
    auto_lines = ["# Short Memories\n"]
    for day in range(1, 6):
        auto_lines.append(f"\n## 2026-01-{day:02d}\n\n{'x' * 200}\n")
    (tmp_path / "auto_short.md").write_text("".join(auto_lines))

    # Tight limit: user + notes + only a small slice of auto_short
    context_limit = 50 + 50 + 250

    result = _build_memory_context(tmp_path, 50000, 0, False, context_limit)

    assert "u" * 50 in result
    assert "n" * 50 in result
    sections = re.findall(r"## 2026-01-\d+", result)
    assert len(sections) < 5  # older sections must have been dropped


def test_build_memory_context_non_consolidation_no_consolidation_block(tmp_path):
    """Non-consolidation output must not contain <memory_consolidation> instructions."""
    from priests.cli.run_cmd import _build_memory_context

    (tmp_path / "user.md").write_text("user fact")

    result = _build_memory_context(tmp_path, 50000, 0, False)

    assert "<memory_consolidation>" not in result
    assert "memory_consolidation" not in result


# ---------------------------------------------------------------------------
# _build_memory_context — flat_line_cap hint on consolidation turns
# ---------------------------------------------------------------------------


def test_build_memory_context_flat_line_cap_hint_present(tmp_path):
    """flat_line_cap > 0 embeds the line count in the consolidation hint."""
    from priests.cli.run_cmd import _build_memory_context

    (tmp_path / "user.md").write_text("user fact")

    result = _build_memory_context(tmp_path, 50000, 20, True)

    assert "20 lines" in result


def test_build_memory_context_flat_line_cap_zero_generic_hint(tmp_path):
    """flat_line_cap=0 uses the generic 'keep concise' wording."""
    from priests.cli.run_cmd import _build_memory_context

    (tmp_path / "user.md").write_text("user fact")

    result = _build_memory_context(tmp_path, 50000, 0, True)

    assert "concise" in result
    assert "lines each" not in result


# ---------------------------------------------------------------------------
# priests/search.py
# ---------------------------------------------------------------------------


def _make_mock_ddgs(results: list) -> tuple[MagicMock, MagicMock]:
    """Return a DDGS context manager mock that yields the given text results."""
    instance = MagicMock()
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)
    instance.text = MagicMock(return_value=results)
    cls = MagicMock(return_value=instance)
    return cls, instance


def test_search_formats_results():
    """search() formats DDGS results into a readable text block."""
    from priests.search import search

    fake_results = [
        {"title": "Result One", "href": "https://example.com/1", "body": "Snippet one."},
        {"title": "Result Two", "href": "https://example.com/2", "body": "Snippet two."},
    ]
    cls, instance = _make_mock_ddgs(fake_results)

    # Patch at the source module so the lazy `from ddgs import DDGS` is intercepted.
    with patch("ddgs.DDGS", cls):
        result = search("test query", max_results=2)

    assert "test query" in result
    assert "Result One" in result
    assert "https://example.com/1" in result
    assert "Snippet one." in result
    assert "Result Two" in result
    instance.text.assert_called_once_with("test query", max_results=2)


def test_search_empty_results():
    """search() returns a 'no results' message when DDGS returns nothing."""
    from priests.search import search

    cls, _ = _make_mock_ddgs([])

    with patch("ddgs.DDGS", cls):
        result = search("noresults")

    assert "no results" in result.lower()
    assert "noresults" in result


def test_search_missing_extra_raises_runtime_error():
    """search() raises RuntimeError with install hint when ddgs is not installed."""
    import sys
    import importlib

    # Temporarily hide the ddgs module
    original = sys.modules.get("ddgs")
    sys.modules["ddgs"] = None  # type: ignore[assignment]
    try:
        # Reload search so the lazy import re-runs
        import priests.search as search_mod
        importlib.reload(search_mod)
        try:
            search_mod.search("anything")
            assert False, "Expected RuntimeError"
        except RuntimeError as exc:
            assert "priests[search]" in str(exc)
    finally:
        if original is None:
            sys.modules.pop("ddgs", None)
        else:
            sys.modules["ddgs"] = original
        importlib.reload(search_mod)
