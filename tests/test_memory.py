"""Correctness tests for priests/memory/extractor.py.

These tests verify that the right content ends up in the right file and that
all public functions behave correctly at their boundaries. They complement the
performance benchmarks in bench_memory.py.
"""

from __future__ import annotations

import dataclasses
import re
import time
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from priests.memory.extractor import (
    AUTO_FILE,
    NOTES_FILE,
    USER_FILE,
    append_memories,
    apply_consolidation,
    clean_last_turn,
    deduplicate_file,
    mark_consolidated,
    needs_consolidation,
    trim_memories,
)


# ---------------------------------------------------------------------------
# Minimal turn/session stubs for clean_last_turn
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Turn:
    role: str
    content: str


@dataclasses.dataclass
class _Session:
    turns: list[_Turn]


# ---------------------------------------------------------------------------
# append_memories
# ---------------------------------------------------------------------------


def test_append_memories_writes_user_file(tmp_path):
    append_memories(tmp_path, {"user": "Prefers dark mode."})
    assert (tmp_path / USER_FILE).exists()
    assert "Prefers dark mode." in (tmp_path / USER_FILE).read_text()


def test_append_memories_auto_short_gets_today_header(tmp_path):
    append_memories(tmp_path, {"auto_short": "Fixed a bug."})
    content = (tmp_path / AUTO_FILE).read_text()
    assert f"## {date.today().isoformat()}" in content
    assert "Fixed a bug." in content


def test_append_memories_empty_fields_create_no_files(tmp_path):
    append_memories(tmp_path, {"user": "", "notes": "  ", "auto_short": ""})
    assert not (tmp_path / USER_FILE).exists()
    assert not (tmp_path / NOTES_FILE).exists()
    assert not (tmp_path / AUTO_FILE).exists()


def test_append_memories_re_append_adds_not_overwrites(tmp_path):
    append_memories(tmp_path, {"user": "Line one."})
    append_memories(tmp_path, {"user": "Line two."})
    content = (tmp_path / USER_FILE).read_text()
    assert "Line one." in content
    assert "Line two." in content


# ---------------------------------------------------------------------------
# apply_consolidation
# ---------------------------------------------------------------------------


def test_apply_consolidation_key_present_overwrites(tmp_path):
    (tmp_path / USER_FILE).write_text("old content\n")
    apply_consolidation(tmp_path, {"user": "new content"})
    assert (tmp_path / USER_FILE).read_text() == "new content\n"


def test_apply_consolidation_key_absent_leaves_file_untouched(tmp_path):
    (tmp_path / USER_FILE).write_text("original\n")
    apply_consolidation(tmp_path, {"notes": "something"})
    assert (tmp_path / USER_FILE).read_text() == "original\n"


def test_apply_consolidation_empty_string_clears_file(tmp_path):
    (tmp_path / USER_FILE).write_text("some content\n")
    apply_consolidation(tmp_path, {"user": ""})
    assert (tmp_path / USER_FILE).read_text() == ""


def test_apply_consolidation_auto_short_without_header_gets_wrapped(tmp_path):
    apply_consolidation(tmp_path, {"auto_short": "a bare note"})
    content = (tmp_path / AUTO_FILE).read_text()
    assert f"## {date.today().isoformat()}" in content
    assert "a bare note" in content


def test_apply_consolidation_auto_short_with_header_not_double_wrapped(tmp_path):
    apply_consolidation(tmp_path, {"auto_short": "## 2026-01-01\n\na note"})
    content = (tmp_path / AUTO_FILE).read_text()
    # Exactly one dated section header
    assert content.count("## ") == 1


# ---------------------------------------------------------------------------
# trim_memories
# ---------------------------------------------------------------------------


def _build_auto_short(*dates_and_bodies: tuple[str, str]) -> str:
    """Build an auto_short.md string with explicit dated sections."""
    lines = ["# Short Memories\n"]
    for dt, body in dates_and_bodies:
        lines.append(f"\n## {dt}\n\n{body}\n")
    return "".join(lines)


def test_trim_memories_drops_oldest_sections(tmp_path):
    content = _build_auto_short(
        ("2026-01-01", "old fact"),
        ("2026-02-01", "medium fact"),
        ("2026-03-01", "newest fact"),
    )
    # Limit that fits only the newest section
    limit = len("# Short Memories\n\n## 2026-03-01\n\nnewest fact\n") + 20
    (tmp_path / AUTO_FILE).write_text(content)
    trim_memories(tmp_path, limit)
    result = (tmp_path / AUTO_FILE).read_text()
    assert "newest fact" in result
    assert "medium fact" not in result  # middle section also dropped
    assert "old fact" not in result
    assert len(result) <= limit


def test_trim_memories_already_under_limit_no_write(tmp_path):
    content = _build_auto_short(("2026-01-01", "a"), ("2026-02-01", "b"))
    (tmp_path / AUTO_FILE).write_text(content)
    mtime_before = (tmp_path / AUTO_FILE).stat().st_mtime_ns  # ns for portability
    trim_memories(tmp_path, 999_999)
    assert (tmp_path / AUTO_FILE).stat().st_mtime_ns == mtime_before


def test_trim_memories_single_section_never_dropped(tmp_path):
    content = _build_auto_short(("2026-01-01", "only section " * 100))
    (tmp_path / AUTO_FILE).write_text(content)
    trim_memories(tmp_path, 1)  # impossibly small limit
    result = (tmp_path / AUTO_FILE).read_text()
    assert result.strip()  # file non-empty
    assert "only section" in result


# ---------------------------------------------------------------------------
# needs_consolidation
# ---------------------------------------------------------------------------


def test_needs_consolidation_no_sentinel_returns_true(tmp_path):
    assert needs_consolidation(tmp_path) is True


def test_needs_consolidation_all_files_older_returns_false(tmp_path):
    (tmp_path / USER_FILE).write_text("facts")
    (tmp_path / NOTES_FILE).write_text("notes")
    (tmp_path / AUTO_FILE).write_text("# Short Memories\n")
    time.sleep(0.02)
    mark_consolidated(tmp_path)
    assert needs_consolidation(tmp_path) is False


def test_needs_consolidation_newer_file_returns_true(tmp_path):
    mark_consolidated(tmp_path)
    time.sleep(0.02)
    (tmp_path / USER_FILE).write_text("updated fact")
    assert needs_consolidation(tmp_path) is True


# ---------------------------------------------------------------------------
# deduplicate_file
# ---------------------------------------------------------------------------


def test_deduplicate_file_removes_duplicate_lines(tmp_path):
    f = tmp_path / "user.md"
    f.write_text("line A\nline B\nline A\n")
    result = deduplicate_file(f)
    assert result is True
    assert f.read_text() == "line A\nline B\n"


def test_deduplicate_file_case_insensitive(tmp_path):
    f = tmp_path / "user.md"
    f.write_text("Prefers dark mode\nprefers dark mode\n")
    deduplicate_file(f)
    assert f.read_text() == "Prefers dark mode\n"


def test_deduplicate_file_blank_lines_always_kept(tmp_path):
    f = tmp_path / "user.md"
    # Two non-blank duplicates separated and followed by blank lines
    f.write_text("line A\n\nline A\n\n")
    deduplicate_file(f)
    content = f.read_text()
    # Second "line A" dropped; both blank lines kept
    assert content.count("line A") == 1
    assert content.count("\n\n") >= 1


def test_deduplicate_file_no_duplicates_returns_false_no_write(tmp_path):
    f = tmp_path / "user.md"
    f.write_text("alpha\nbeta\ngamma\n")
    mtime_before = f.stat().st_mtime_ns  # ns for portability across filesystems
    result = deduplicate_file(f)
    assert result is False
    assert f.stat().st_mtime_ns == mtime_before


def test_deduplicate_file_absent_file_returns_false(tmp_path):
    f = tmp_path / "nonexistent.md"
    assert deduplicate_file(f) is False
    assert not f.exists()


# ---------------------------------------------------------------------------
# clean_last_turn
# ---------------------------------------------------------------------------


async def test_clean_last_turn_strips_memory_block():
    turn = _Turn(
        role="assistant",
        content='<memory_append>{"user": "test"}</memory_append>Here is the answer.',
    )
    session = _Session(turns=[turn])
    store = MagicMock()
    store.get = AsyncMock(return_value=session)
    store.save = AsyncMock()

    await clean_last_turn(store, "session-1")

    store.save.assert_called_once()
    saved: _Session = store.save.call_args[0][0]
    assert "<memory_append>" not in saved.turns[-1].content
    assert "Here is the answer." in saved.turns[-1].content


async def test_clean_last_turn_no_tags_does_not_save():
    turn = _Turn(role="assistant", content="Plain response with no tags.")
    session = _Session(turns=[turn])
    store = MagicMock()
    store.get = AsyncMock(return_value=session)
    store.save = AsyncMock()

    await clean_last_turn(store, "session-1")

    store.save.assert_not_called()


async def test_clean_last_turn_none_session_no_error():
    store = MagicMock()
    store.get = AsyncMock(return_value=None)
    store.save = AsyncMock()

    await clean_last_turn(store, "session-1")

    store.save.assert_not_called()


async def test_clean_last_turn_user_last_turn_is_noop():
    """Only assistant turns are stripped; a user last turn must be left alone."""
    turn = _Turn(role="user", content="<memory_append>{}</memory_append>user said this")
    session = _Session(turns=[turn])
    store = MagicMock()
    store.get = AsyncMock(return_value=session)
    store.save = AsyncMock()

    await clean_last_turn(store, "session-1")

    store.save.assert_not_called()
    # Content must be unmodified
    assert turn.content == "<memory_append>{}</memory_append>user said this"


# ---------------------------------------------------------------------------
# StreamingStripper — multi-block and edge cases
# ---------------------------------------------------------------------------


def test_stripper_two_consecutive_blocks_both_captured():
    """A consolidation block followed by an append block: both payloads extracted."""
    from priests.memory.extractor import StreamingStripper

    text = (
        '<memory_consolidation>{"user":"consolidated"}</memory_consolidation>'
        '<memory_append>{"notes":"appended"}</memory_append>'
        "prose after"
    )
    s = StreamingStripper()
    visible = ""
    for ch in text:
        visible += s.feed(ch)
    visible += s.flush()

    assert s.consolidation_json == '{"user":"consolidated"}'
    assert s.append_json == '{"notes":"appended"}'
    assert "prose after" in visible
    assert "<memory_" not in visible


def test_stripper_incomplete_block_discarded_at_flush():
    """A block whose closing tag never arrives is discarded silently at flush."""
    from priests.memory.extractor import StreamingStripper

    text = "<memory_append>incomplete payload — no closing tag"
    s = StreamingStripper()
    for ch in text:
        s.feed(ch)
    visible = s.flush()

    # Block content is captured internally (flush saves what it has)
    # but nothing is emitted as visible text during the block
    assert "<memory_append>" not in visible


# ---------------------------------------------------------------------------
# _build_memory_context — context_limit enforcement
# ---------------------------------------------------------------------------


def test_build_memory_context_truncates_auto_short_when_over_limit(tmp_path):
    """On a consolidation turn, context_limit causes old auto_short sections to be dropped."""
    from priests.cli.run_cmd import _build_memory_context

    (tmp_path / "user.md").write_text("user fact")
    (tmp_path / "notes.md").write_text("notes fact")

    # Build a large auto_short with multiple dated sections
    auto_lines = ["# Short Memories\n"]
    for day in range(1, 10):
        auto_lines.append(f"\n## 2026-01-{day:02d}\n\n{'x' * 200}\n")
    (tmp_path / "auto_short.md").write_text("".join(auto_lines))

    # Tight limit: user + notes + only a small slice of auto_short
    context_limit = len("user fact") + len("notes fact") + 250

    # consolidate=True is required — that's when file contents are injected
    result = _build_memory_context(tmp_path, 50000, 0, True, context_limit)

    assert "user fact" in result
    assert "notes fact" in result
    # Older dated sections must be gone; not all 9 sections should survive
    auto_sections = re.findall(r"## 2026-01-\d+", result)
    assert len(auto_sections) < 9


def test_build_memory_context_single_oversized_section_hard_truncated(tmp_path):
    """Hard tail-truncation kicks in when a single auto_short section exceeds the budget."""
    from priests.cli.run_cmd import _build_memory_context

    (tmp_path / "user.md").write_text("u" * 100)
    (tmp_path / "notes.md").write_text("n" * 100)
    # Single section larger than what the limit allows after user + notes
    (tmp_path / "auto_short.md").write_text(
        "# Short Memories\n\n## 2026-01-01\n\n" + "a" * 2000
    )

    context_limit = 300  # user(100) + notes(100) = 200 fixed; 100 left for auto

    result = _build_memory_context(tmp_path, 50000, 0, True, context_limit)

    assert "u" * 100 in result
    assert "n" * 100 in result
    # Some auto content is present (tail-truncated, not fully dropped)
    assert "a" in result
    # The full 2000-char run of 'a' must not be present
    assert "a" * 2000 not in result


def test_build_memory_context_zero_limit_injects_all(tmp_path):
    """context_limit=0 (default off) injects full auto_short without truncation."""
    from priests.cli.run_cmd import _build_memory_context

    (tmp_path / "user.md").write_text("user fact")
    (tmp_path / "auto_short.md").write_text(
        "# Short Memories\n\n## 2026-01-01\n\n" + "detail " * 100
    )

    result = _build_memory_context(tmp_path, 50000, 0, True, 0)

    assert "user fact" in result
    assert "detail " * 5 in result  # bulk of auto_short content present


# ---------------------------------------------------------------------------
# deduplicate_file — interaction with needs_consolidation
# ---------------------------------------------------------------------------


def test_deduplicate_before_needs_consolidation_no_false_positive(tmp_path):
    """Dedup that finds nothing to remove must not trigger consolidation."""
    (tmp_path / "user.md").write_text("unique line A\nunique line B\n")
    (tmp_path / "notes.md").write_text("unique note\n")
    time.sleep(0.02)
    mark_consolidated(tmp_path)

    # Dedup finds nothing — no write, no mtime bump
    deduplicate_file(tmp_path / "user.md")
    deduplicate_file(tmp_path / "notes.md")

    assert needs_consolidation(tmp_path) is False


def test_deduplicate_before_needs_consolidation_dedup_write_visible(tmp_path):
    """Dedup that removes lines bumps the mtime; needs_consolidation sees it."""
    mark_consolidated(tmp_path)
    time.sleep(0.02)
    # Write files with duplicates AFTER the sentinel
    (tmp_path / "user.md").write_text("line A\nline A\n")

    deduplicate_file(tmp_path / "user.md")

    # needs_consolidation should return True because user.md (post-dedup) is newer
    assert needs_consolidation(tmp_path) is True
