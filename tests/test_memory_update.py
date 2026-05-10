"""Focused tests for normal memory saves and conflict-key memory updates."""

from __future__ import annotations

import json

from priests.memory.extractor import USER_JSONL_FILE, assemble_memory_entries, save_memories, save_prompt_memories


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_normal_user_memory_save_is_recalled(tmp_path):
    save_memories(
        tmp_path,
        {
            "memories": [
                {
                    "kind": "user",
                    "text": "The user's favorite editor is Neovim.",
                    "priority": 2,
                    "confidence": 1,
                    "stability": "stable",
                    "source": "user_direct",
                }
            ]
        },
    )

    rows = _read_jsonl(tmp_path / USER_JSONL_FILE)
    assert len(rows) == 1
    assert rows[0]["status"] == "active"
    assert rows[0]["conflict_key"] == ""

    combined = "\n".join(assemble_memory_entries(tmp_path, prompt="Which editor do I like?"))
    assert "Neovim" in combined


def test_conflict_key_memory_update_supersedes_old_value(tmp_path):
    save_memories(
        tmp_path,
        {
            "memories": [
                {
                    "kind": "user",
                    "text": "The user's favorite color is yellow.",
                    "priority": 2,
                    "confidence": 1,
                    "stability": "stable",
                    "source": "user_direct",
                    "conflict_key": "user.favorite_color",
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
                    "text": "The user's favorite color is green.",
                    "priority": 2,
                    "confidence": 1,
                    "stability": "stable",
                    "source": "user_direct",
                    "conflict_key": "user.favorite_color",
                }
            ]
        },
    )

    rows = _read_jsonl(tmp_path / USER_JSONL_FILE)
    assert [row["status"] for row in rows] == ["superseded", "active"]
    assert rows[1]["supersedes"] == [rows[0]["id"]]

    combined = "\n".join(assemble_memory_entries(tmp_path, prompt="What is my favorite color?"))
    assert "green" in combined
    assert "yellow" not in combined


def test_natural_prompt_memory_update_supersedes_old_value(tmp_path):
    save_prompt_memories(tmp_path, "My favorite color is yellow.")
    save_prompt_memories(tmp_path, "Actually, my favorite color is green, not yellow.")

    rows = _read_jsonl(tmp_path / USER_JSONL_FILE)
    assert [row["status"] for row in rows] == ["superseded", "active"]
    assert rows[1]["conflict_key"] == "user.favorite_color"

    combined = "\n".join(assemble_memory_entries(tmp_path, prompt="What is my favorite color?"))
    assert "green" in combined
    assert "yellow" not in combined
