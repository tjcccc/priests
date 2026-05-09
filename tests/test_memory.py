"""Correctness tests for priests/memory/extractor.py.

These tests verify that the right content ends up in the right file and that
all public functions behave correctly at their boundaries. They complement the
performance benchmarks in bench_memory.py.
"""

from __future__ import annotations

import dataclasses
import json
import re
from unittest.mock import AsyncMock, MagicMock

from priests.memory.extractor import (
    AUTO_FILE,
    AUTO_JSONL_FILE,
    NOTES_FILE,
    PREFERENCES_FILE,
    PREFERENCES_JSONL_FILE,
    USER_FILE,
    USER_JSONL_FILE,
    apply_memory_proposals,
    assemble_memory_entries,
    append_memories,
    apply_consolidation,
    clean_last_turn,
    deduplicate_file,
    needs_consolidation,
    remember_preference,
    remember_user,
    save_memories,
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


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# append_memories
# ---------------------------------------------------------------------------


def test_append_memories_user_key_writes_user_jsonl(tmp_path):
    append_memories(tmp_path, {"user": "Prefers dark mode."})
    rows = _read_jsonl(tmp_path / USER_JSONL_FILE)
    assert rows[0]["text"] == "Prefers dark mode."
    assert rows[0]["kind"] == "user"


def test_append_memories_auto_short_writes_auto_jsonl(tmp_path):
    append_memories(tmp_path, {"auto_short": "Fixed a bug."})
    rows = _read_jsonl(tmp_path / AUTO_JSONL_FILE)
    assert rows[0]["text"] == "Fixed a bug."
    assert rows[0]["kind"] == "auto_short"


def test_append_memories_empty_fields_create_no_files(tmp_path):
    append_memories(tmp_path, {"user": "", "notes": "  ", "auto_short": ""})
    assert not (tmp_path / USER_FILE).exists()
    assert not (tmp_path / NOTES_FILE).exists()
    assert not (tmp_path / AUTO_FILE).exists()
    assert not (tmp_path / PREFERENCES_FILE).exists()
    assert not (tmp_path / USER_JSONL_FILE).exists()
    assert not (tmp_path / PREFERENCES_JSONL_FILE).exists()
    assert not (tmp_path / AUTO_JSONL_FILE).exists()


def test_memory_writers_ignore_non_object_payloads(tmp_path):
    append_memories(tmp_path, ["note"])  # type: ignore[arg-type]
    apply_memory_proposals(tmp_path, ["note"])  # type: ignore[arg-type]
    save_memories(tmp_path, ["note"])  # type: ignore[arg-type]

    assert not (tmp_path / USER_JSONL_FILE).exists()
    assert not (tmp_path / PREFERENCES_JSONL_FILE).exists()
    assert not (tmp_path / AUTO_JSONL_FILE).exists()


def test_append_memories_re_append_adds_to_user_jsonl(tmp_path):
    append_memories(tmp_path, {"user": "Line one."})
    append_memories(tmp_path, {"user": "Line two."})
    rows = _read_jsonl(tmp_path / USER_JSONL_FILE)
    assert [row["text"] for row in rows] == ["Line one.", "Line two."]


def test_append_memories_notes_key_writes_preferences_jsonl(tmp_path):
    append_memories(tmp_path, {"notes": "Likes concise replies."})
    assert _read_jsonl(tmp_path / PREFERENCES_JSONL_FILE)[0]["text"] == "Likes concise replies."


def test_apply_memory_proposals_writes_preferences_jsonl(tmp_path):
    apply_memory_proposals(
        tmp_path,
        {"proposals": [{"target": "preferences", "content": "- Prefers examples.", "reason": "User said so."}]},
        session_id="sess-1",
    )
    assert _read_jsonl(tmp_path / PREFERENCES_JSONL_FILE)[0]["text"] == "- Prefers examples."
    assert not (tmp_path / "pending").exists()


def test_apply_memory_proposals_writes_user_jsonl(tmp_path):
    apply_memory_proposals(
        tmp_path,
        {"proposals": [{"target": "user", "content": "- Name: Jack"}]},
        session_id="sess-1",
    )
    assert _read_jsonl(tmp_path / USER_JSONL_FILE)[0]["text"] == "- Name: Jack"
    assert not (tmp_path / "pending").exists()


def test_apply_memory_proposals_accepts_legacy_flat_payload(tmp_path):
    apply_memory_proposals(tmp_path, {"user": "- Name: Jack", "preferences": "- Keep replies short"})

    assert _read_jsonl(tmp_path / USER_JSONL_FILE)[0]["text"] == "- Name: Jack"
    assert _read_jsonl(tmp_path / PREFERENCES_JSONL_FILE)[0]["text"] == "- Keep replies short"
    assert not (tmp_path / "pending").exists()


def test_save_memories_writes_priority_zero_user_memory(tmp_path):
    save_memories(
        tmp_path,
        {
            "memories": [
                {
                    "kind": "user",
                    "text": "The user's name is Jack.",
                    "priority": 0,
                    "confidence": 1,
                    "stability": "stable",
                    "source": "user_direct",
                    "evidence": "My name is Jack.",
                    "reason": "User explicitly stated their name.",
                }
            ]
        },
    )

    row = _read_jsonl(tmp_path / USER_JSONL_FILE)[0]
    assert row["priority"] == 0
    assert row["confidence"] == 1
    assert row["status"] == "active"


def test_save_memories_coerces_time_sensitive_user_fact_to_auto_short(tmp_path):
    save_memories(
        tmp_path,
        {
            "memories": [
                {
                    "kind": "user",
                    "text": "I have a project meeting tomorrow at 3 p.m.",
                    "priority": 2,
                    "confidence": 1,
                    "stability": "stable",
                    "source": "user_direct",
                }
            ]
        },
    )

    assert not (tmp_path / USER_JSONL_FILE).read_text().strip()
    row = _read_jsonl(tmp_path / AUTO_JSONL_FILE)[0]
    assert row["kind"] == "auto_short"
    assert "project meeting" in row["text"]


def test_save_memories_coerces_response_style_user_fact_to_preferences(tmp_path):
    save_memories(
        tmp_path,
        {
            "memories": [
                {
                    "kind": "user",
                    "text": "I prefer short, normal conversation replies.",
                    "priority": 2,
                    "confidence": 1,
                    "stability": "stable",
                    "source": "user_direct",
                }
            ]
        },
    )

    assert not (tmp_path / USER_JSONL_FILE).read_text().strip()
    row = _read_jsonl(tmp_path / PREFERENCES_JSONL_FILE)[0]
    assert row["kind"] == "preferences"
    assert "short" in row["text"]


def test_save_memories_merges_duplicate_memory_with_best_priority(tmp_path):
    save_memories(tmp_path, {"memories": [{"kind": "preferences", "text": "Prefers short replies.", "priority": 5}]})
    save_memories(tmp_path, {"memories": [{"kind": "preferences", "text": "Prefers short replies.", "priority": 2}]})

    rows = _read_jsonl(tmp_path / PREFERENCES_JSONL_FILE)
    assert len(rows) == 1
    assert rows[0]["priority"] == 2


def test_save_memories_supersedes_conflicting_name(tmp_path):
    save_memories(
        tmp_path,
        {
            "memories": [
                {
                    "kind": "user",
                    "text": "The user's name is Jack.",
                    "priority": 0,
                    "confidence": 1,
                    "stability": "stable",
                    "source": "user_direct",
                }
            ]
        },
    )
    save_memories(
        tmp_path,
        {
            "memories": [
                {
                    "kind": "user",
                    "text": "The user's name is Tao.",
                    "priority": 0,
                    "confidence": 1,
                    "stability": "stable",
                    "source": "user_direct",
                }
            ]
        },
    )

    rows = _read_jsonl(tmp_path / USER_JSONL_FILE)
    assert [row["status"] for row in rows] == ["superseded", "active"]
    assert rows[1]["supersedes"] == [rows[0]["id"]]
    combined = "\n".join(assemble_memory_entries(tmp_path))
    assert "Tao" in combined
    assert "Jack" not in combined


def test_save_memories_supersedes_conflicting_project_meeting_time(tmp_path):
    save_memories(
        tmp_path,
        {
            "memories": [
                {
                    "kind": "auto_short",
                    "text": "The user has a project meeting tomorrow at 3 p.m.",
                    "priority": 2,
                    "confidence": 1,
                    "stability": "session",
                    "source": "user_direct",
                }
            ]
        },
    )
    save_memories(
        tmp_path,
        {
            "memories": [
                {
                    "kind": "auto_short",
                    "text": "The project meeting tomorrow is at 4 p.m., not 3 p.m.",
                    "priority": 2,
                    "confidence": 1,
                    "stability": "session",
                    "source": "user_direct",
                }
            ]
        },
    )

    rows = _read_jsonl(tmp_path / AUTO_JSONL_FILE)
    assert [row["status"] for row in rows] == ["superseded", "active"]
    combined = "\n".join(assemble_memory_entries(tmp_path))
    assert "4 p.m." in combined
    assert "project meeting tomorrow at 3 p.m." not in combined


# ---------------------------------------------------------------------------
# apply_consolidation
# ---------------------------------------------------------------------------


def test_apply_consolidation_ignores_durable_keys(tmp_path):
    (tmp_path / USER_FILE).write_text("old content\n")
    apply_consolidation(tmp_path, {"user": "new content"})
    assert (tmp_path / USER_FILE).read_text() == "old content\n"
    assert not (tmp_path / USER_JSONL_FILE).exists()


def test_apply_consolidation_auto_short_without_header_writes_jsonl(tmp_path):
    apply_consolidation(tmp_path, {"auto_short": "a bare note"})
    assert _read_jsonl(tmp_path / AUTO_JSONL_FILE)[0]["text"] == "a bare note"


def test_apply_consolidation_auto_short_with_header_stores_single_entry(tmp_path):
    apply_consolidation(tmp_path, {"auto_short": "## 2026-01-01\n\na note"})
    rows = _read_jsonl(tmp_path / AUTO_JSONL_FILE)
    assert len(rows) == 1
    assert rows[0]["text"].count("## ") == 1


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


def test_trim_memories_structured_keeps_priority_zero(tmp_path):
    memories = [{"kind": "auto_short", "text": "Critical short fact.", "priority": 0, "confidence": 1, "stability": "stable"}]
    for idx in range(20):
        memories.append({"kind": "auto_short", "text": f"low priority fact {idx} " * 20, "priority": 9})
    save_memories(tmp_path, {"memories": memories})

    trim_memories(tmp_path, 800)

    rows = _read_jsonl(tmp_path / AUTO_JSONL_FILE)
    assert any(row["text"] == "Critical short fact." for row in rows)
    assert (tmp_path / AUTO_JSONL_FILE).stat().st_size <= 800


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

    assert "Atlas" in _read_jsonl(tmp_path / USER_JSONL_FILE)[0]["text"]
    assert "concise" in _read_jsonl(tmp_path / PREFERENCES_JSONL_FILE)[0]["text"]


def test_assemble_memory_entries_orders_short_term_last(tmp_path):
    save_memories(
        tmp_path,
        {
            "memories": [
                {"kind": "user", "text": "user fact", "priority": 1, "confidence": 1, "stability": "stable"},
                {"kind": "preferences", "text": "preference fact", "priority": 2},
                {"kind": "auto_short", "text": "auto fact", "priority": 3, "stability": "session"},
            ]
        },
    )
    (tmp_path / NOTES_FILE).write_text("legacy note")

    entries = assemble_memory_entries(tmp_path)

    assert "Important User Memory" in entries[0]
    assert "Preferences" in entries[1]
    assert "Legacy Notes Memory" in entries[2]
    assert "Current Context" in entries[3]


def test_assemble_memory_entries_ignores_empty_stub_headers(tmp_path):
    (tmp_path / USER_FILE).write_text("# User\n\n")
    (tmp_path / PREFERENCES_FILE).write_text("# Preferences\n\n")
    (tmp_path / AUTO_FILE).write_text("# Short Memories\n\n")

    assert assemble_memory_entries(tmp_path) == []


def test_assemble_memory_entries_context_limit_truncates_auto_short(tmp_path):
    memories = [
        {"kind": "user", "text": "u" * 50, "priority": 1, "confidence": 1, "stability": "stable"},
        {"kind": "preferences", "text": "p" * 50, "priority": 2},
    ]
    for day in range(1, 6):
        memories.append({"kind": "auto_short", "text": f"2026-01-{day:02d}: {'x' * 200}", "priority": 3})
    save_memories(tmp_path, {"memories": memories})

    entries = assemble_memory_entries(tmp_path, context_limit=350)
    combined = "\n".join(entries)

    assert "u" * 50 in combined
    assert "p" * 50 in combined
    assert len(re.findall(r"2026-01-\d+", combined)) < 5


def test_assemble_memory_entries_priority_cutoff_normal_vs_thinking(tmp_path):
    save_memories(
        tmp_path,
        {
            "memories": [
                {
                    "kind": "user",
                    "text": "The user's name is Jack.",
                    "priority": 0,
                    "confidence": 1,
                    "stability": "stable",
                    "source": "user_direct",
                },
                {"kind": "auto_short", "text": "Low priority old context.", "priority": 8},
            ]
        },
    )

    normal = "\n".join(assemble_memory_entries(tmp_path))
    thinking = "\n".join(assemble_memory_entries(tmp_path, thinking=True))

    assert "Jack" in normal
    assert "Low priority" not in normal
    assert "Low priority" in thinking


def test_assemble_memory_entries_ranks_relevant_memory_within_priority(tmp_path):
    save_memories(
        tmp_path,
        {
            "memories": [
                {"kind": "preferences", "text": "Prefers Python examples.", "priority": 2},
                {"kind": "preferences", "text": "Prefers Go examples.", "priority": 2},
            ]
        },
    )

    combined = "\n".join(assemble_memory_entries(tmp_path, prompt="Can you show Python code?"))

    assert combined.index("Python examples") < combined.index("Go examples")


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
        content='<memory_save>{"memories":[{"kind":"user","text":"test"}]}</memory_save>Here is the answer.',
    )
    session = _Session(turns=[turn])
    store = MagicMock()
    store.get = AsyncMock(return_value=session)
    store.save = AsyncMock()

    await clean_last_turn(store, "session-1")

    store.save.assert_called_once()
    saved: _Session = store.save.call_args[0][0]
    assert "<memory_save>" not in saved.turns[-1].content
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
    """A save block followed by a legacy append block: both payloads extracted."""
    from priests.memory.extractor import StreamingStripper

    text = (
        '<memory_save>{"memories":[{"kind":"user","text":"fact"}]}</memory_save>'
        '<memory_append>{"auto_short":"appended"}</memory_append>'
        "prose after"
    )
    s = StreamingStripper()
    visible = ""
    for ch in text:
        visible += s.feed(ch)
    visible += s.flush()

    assert s.save_json == '{"memories":[{"kind":"user","text":"fact"}]}'
    assert s.append_json == '{"auto_short":"appended"}'
    assert "prose after" in visible
    assert "<memory_" not in visible


def test_stripper_incomplete_block_discarded_at_flush():
    """A block whose closing tag never arrives is discarded silently at flush."""
    from priests.memory.extractor import StreamingStripper

    text = "<memory_save>incomplete payload - no closing tag"
    s = StreamingStripper()
    for ch in text:
        s.feed(ch)
    visible = s.flush()

    # Block content is captured internally (flush saves what it has)
    # but nothing is emitted as visible text during the block
    assert "<memory_save>" not in visible
