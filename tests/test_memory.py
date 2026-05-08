"""Correctness tests for priests/memory/extractor.py.

These tests verify that the right content ends up in the right file and that
all public functions behave correctly at their boundaries. They complement the
performance benchmarks in bench_memory.py.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from priests.memory.extractor import (
    AUTO_FILE,
    NOTES_FILE,
    PENDING_DIR,
    PREFERENCES_FILE,
    USER_FILE,
    apply_memory_proposals,
    assemble_memory_entries,
    append_memories,
    apply_consolidation,
    clean_last_turn,
    deduplicate_file,
    needs_consolidation,
    remember_preference,
    remember_user,
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


def test_append_memories_user_key_creates_pending_proposal(tmp_path):
    append_memories(tmp_path, {"user": "Prefers dark mode."})
    assert not (tmp_path / USER_FILE).exists()
    proposals = list((tmp_path / PENDING_DIR).glob("*.md"))
    assert len(proposals) == 1
    assert "target: user" in proposals[0].read_text()
    assert "Prefers dark mode." in proposals[0].read_text()


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
    assert not (tmp_path / PENDING_DIR).exists()


def test_append_memories_re_append_creates_multiple_pending_files(tmp_path):
    append_memories(tmp_path, {"user": "Line one."})
    append_memories(tmp_path, {"user": "Line two."})
    contents = "\n".join(p.read_text() for p in (tmp_path / PENDING_DIR).glob("*.md"))
    assert "Line one." in contents
    assert "Line two." in contents


def test_append_memories_notes_key_becomes_preferences_proposal(tmp_path):
    append_memories(tmp_path, {"notes": "Likes concise replies."})
    proposal = next((tmp_path / PENDING_DIR).glob("*.md"))
    text = proposal.read_text()
    assert "target: preferences" in text
    assert "Likes concise replies." in text


def test_apply_memory_proposals_writes_pending_markdown(tmp_path):
    apply_memory_proposals(
        tmp_path,
        {"proposals": [{"target": "preferences", "content": "- Prefers examples.", "reason": "User said so."}]},
        session_id="sess-1",
    )
    proposal = next((tmp_path / PENDING_DIR).glob("*.md"))
    text = proposal.read_text()
    assert "status: pending" in text
    assert "target: preferences" in text
    assert "session_id: sess-1" in text
    assert "- Prefers examples." in text


# ---------------------------------------------------------------------------
# apply_consolidation
# ---------------------------------------------------------------------------


def test_apply_consolidation_ignores_durable_keys(tmp_path):
    (tmp_path / USER_FILE).write_text("old content\n")
    apply_consolidation(tmp_path, {"user": "new content"})
    assert (tmp_path / USER_FILE).read_text() == "old content\n"


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


def test_needs_consolidation_disabled(tmp_path):
    (tmp_path / USER_FILE).write_text("facts")
    assert needs_consolidation(tmp_path) is False


# ---------------------------------------------------------------------------
# approved memory commands and assembly
# ---------------------------------------------------------------------------


def test_remember_user_and_preference_write_approved_files(tmp_path):
    remember_user(tmp_path, "- User is Atlas.")
    remember_preference(tmp_path, "- Prefers concise replies.")

    assert "Atlas" in (tmp_path / USER_FILE).read_text()
    assert "concise" in (tmp_path / PREFERENCES_FILE).read_text()


def test_assemble_memory_entries_orders_short_term_last(tmp_path):
    (tmp_path / USER_FILE).write_text("user fact")
    (tmp_path / PREFERENCES_FILE).write_text("preference fact")
    (tmp_path / NOTES_FILE).write_text("legacy note")
    (tmp_path / AUTO_FILE).write_text("# Short Memories\n\n## 2026-01-01\n\nauto fact\n")

    entries = assemble_memory_entries(tmp_path)

    assert "Approved User Memory" in entries[0]
    assert "Approved Preference Memory" in entries[1]
    assert "Legacy Notes Memory" in entries[2]
    assert "Short-Term Memory" in entries[3]


def test_assemble_memory_entries_ignores_empty_stub_headers(tmp_path):
    (tmp_path / USER_FILE).write_text("# User\n\n")
    (tmp_path / PREFERENCES_FILE).write_text("# Preferences\n\n")
    (tmp_path / AUTO_FILE).write_text("# Short Memories\n\n")

    assert assemble_memory_entries(tmp_path) == []


def test_assemble_memory_entries_context_limit_truncates_auto_short(tmp_path):
    (tmp_path / USER_FILE).write_text("u" * 50)
    (tmp_path / PREFERENCES_FILE).write_text("p" * 50)
    auto_lines = ["# Short Memories\n"]
    for day in range(1, 6):
        auto_lines.append(f"\n## 2026-01-{day:02d}\n\n{'x' * 200}\n")
    (tmp_path / AUTO_FILE).write_text("".join(auto_lines))

    entries = assemble_memory_entries(tmp_path, context_limit=350)
    combined = "\n".join(entries)

    assert "u" * 50 in combined
    assert "p" * 50 in combined
    assert len(re.findall(r"## 2026-01-\d+", combined)) < 5


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
    """A proposal block followed by an append block: both payloads extracted."""
    from priests.memory.extractor import StreamingStripper

    text = (
        '<memory_proposal>{"proposals":[{"target":"user","content":"fact"}]}</memory_proposal>'
        '<memory_append>{"auto_short":"appended"}</memory_append>'
        "prose after"
    )
    s = StreamingStripper()
    visible = ""
    for ch in text:
        visible += s.feed(ch)
    visible += s.flush()

    assert s.proposal_json == '{"proposals":[{"target":"user","content":"fact"}]}'
    assert s.append_json == '{"auto_short":"appended"}'
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
