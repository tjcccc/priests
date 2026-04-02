from __future__ import annotations

import dataclasses
import re
from pathlib import Path

_TAG_RE = re.compile(r"<memory>(.*?)</memory>", re.DOTALL | re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")  # matches [Unknown], [Name], [N/A], etc.

AUTO_MEMORIES_FILE = "auto_memories.md"


def extract_memories(text: str) -> list[str]:
    """Return memory strings found in the model's response, excluding placeholders."""
    results = []
    for m in _TAG_RE.findall(text):
        fact = m.strip()
        if fact and not _PLACEHOLDER_RE.search(fact):
            results.append(fact)
    return results


def strip_memory_tags(text: str) -> str:
    """Remove all <memory>...</memory> tags from text for display."""
    return _TAG_RE.sub("", text).strip()


def _load_existing(memories_dir: Path) -> list[str]:
    """Return current lines from auto_memories.md, or empty list."""
    path = memories_dir / AUTO_MEMORIES_FILE
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_memories(memories_dir: Path, facts: list[str]) -> list[str]:
    """Append new facts to auto_memories.md, skipping duplicates. Returns newly written facts."""
    memories_dir.mkdir(parents=True, exist_ok=True)
    existing = _load_existing(memories_dir)
    existing_lower = {line.lower().strip() for line in existing}

    new_facts = [f for f in facts if f.lower().strip() not in existing_lower]
    if not new_facts:
        return []

    path = memories_dir / AUTO_MEMORIES_FILE
    with path.open("a", encoding="utf-8") as fh:
        for fact in new_facts:
            fh.write(fact + "\n")

    return new_facts


def trim_memories(memories_dir: Path, limit: int) -> None:
    """Keep only the most recent `limit` lines in auto_memories.md. 0 = unlimited."""
    if limit <= 0:
        return
    path = memories_dir / AUTO_MEMORIES_FILE
    if not path.exists():
        return
    lines = _load_existing(memories_dir)
    if len(lines) > limit:
        path.write_text("\n".join(lines[-limit:]) + "\n", encoding="utf-8")


async def clean_last_turn(store, session_id: str) -> None:
    """Strip memory tags from the last assistant turn so they don't leak into session history."""
    session = await store.get(session_id)
    if not session or not session.turns:
        return
    last = session.turns[-1]
    if last.role == "assistant" and _TAG_RE.search(last.content):
        session.turns[-1] = dataclasses.replace(last, content=strip_memory_tags(last.content))
        await store.save(session)
