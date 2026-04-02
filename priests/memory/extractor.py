from __future__ import annotations

import dataclasses
import re
from datetime import date
from pathlib import Path
from typing import Literal

# Matches <memory>...</memory> and <memory type="user">...</memory> etc.
_TAG_RE = re.compile(
    r'<memory(?:\s+type=["\']?(\w+)["\']?)?\s*>(.*?)</memory>',
    re.DOTALL | re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")  # [Unknown], [Name], [N/A], etc.

MemoryType = Literal["auto", "user", "note"]

USER_FILE = "user.md"
NOTES_FILE = "notes.md"


def _auto_filename() -> str:
    return f"auto_{date.today().strftime('%Y%m%d')}.md"


def _file_for_type(memory_type: MemoryType) -> str:
    if memory_type == "user":
        return USER_FILE
    if memory_type == "note":
        return NOTES_FILE
    return _auto_filename()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_memories(text: str) -> list[tuple[MemoryType, str]]:
    """Return (type, fact) pairs from the model's response, excluding placeholders."""
    results = []
    for type_attr, content in _TAG_RE.findall(text):
        fact = content.strip()
        if not fact or _PLACEHOLDER_RE.search(fact):
            continue
        mem_type: MemoryType = type_attr.lower() if type_attr.lower() in ("user", "note") else "auto"
        results.append((mem_type, fact))
    return results


def strip_memory_tags(text: str) -> str:
    """Remove all <memory ...>...</memory> tags from text for display."""
    return _TAG_RE.sub("", text).strip()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _load_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_memories(memories_dir: Path, facts: list[tuple[MemoryType, str]]) -> list[tuple[MemoryType, str]]:
    """Write facts to the appropriate memory files. Returns newly written (type, fact) pairs."""
    if not facts:
        return []
    memories_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[MemoryType, str]] = []

    # Group by target file
    by_file: dict[str, list[tuple[MemoryType, str]]] = {}
    for mem_type, fact in facts:
        fname = _file_for_type(mem_type)
        by_file.setdefault(fname, []).append((mem_type, fact))

    for fname, items in by_file.items():
        path = memories_dir / fname
        existing_lower = {l.lower().strip() for l in _load_lines(path)}
        new_items = [(t, f) for t, f in items if f.lower().strip() not in existing_lower]
        if not new_items:
            continue
        with path.open("a", encoding="utf-8") as fh:
            for _, fact in new_items:
                fh.write(fact + "\n")
        written.extend(new_items)

    return written


# ---------------------------------------------------------------------------
# Trimming (auto daily files only)
# ---------------------------------------------------------------------------

def trim_memories(memories_dir: Path, limit: int) -> None:
    """Delete oldest auto_YYYYMMDD.md files beyond limit. user.md and notes.md are never touched."""
    if limit <= 0:
        return
    files = sorted(memories_dir.glob("auto_????????.md"))  # date-named only
    excess = len(files) - limit
    for f in files[:excess]:
        f.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Session cleanup
# ---------------------------------------------------------------------------

async def clean_last_turn(store, session_id: str) -> None:
    """Strip memory tags from the last assistant turn so they don't leak into session history."""
    session = await store.get(session_id)
    if not session or not session.turns:
        return
    last = session.turns[-1]
    if last.role == "assistant" and _TAG_RE.search(last.content):
        session.turns[-1] = dataclasses.replace(last, content=strip_memory_tags(last.content))
        await store.save(session)
