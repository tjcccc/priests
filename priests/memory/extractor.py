from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_TAG_RE = re.compile(r"<memory>(.*?)</memory>", re.DOTALL | re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")  # matches [Unknown], [Name], [N/A], etc.


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


def write_memories(memories_dir: Path, facts: list[str]) -> list[Path]:
    """Write each fact to a timestamped .md file in memories_dir."""
    memories_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    for i, fact in enumerate(facts):
        path = memories_dir / f"auto_{ts}_{i:02d}.md"
        path.write_text(fact, encoding="utf-8")
        written.append(path)
    return written


def trim_memories(memories_dir: Path, limit: int) -> None:
    """Delete oldest auto_*.md files beyond limit. User-created files are never touched."""
    if limit <= 0:
        return
    files = sorted(memories_dir.glob("auto_*.md"))  # oldest first (timestamp filename sort)
    excess = len(files) - limit
    for f in files[:excess]:
        f.unlink(missing_ok=True)
