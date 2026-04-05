from __future__ import annotations

import dataclasses
import re
from datetime import date
from pathlib import Path

# Regex used only for session-history cleanup (complete strings, not streaming)
_APPEND_RE = re.compile(r"<memory_append>(.*?)</memory_append>", re.DOTALL | re.IGNORECASE)
_CONSOLIDATION_RE = re.compile(r"<memory_consolidation>(.*?)</memory_consolidation>", re.DOTALL | re.IGNORECASE)

USER_FILE = "user.md"
NOTES_FILE = "notes.md"
AUTO_FILE = "auto_short.md"
SENTINEL_FILE = ".last_consolidated"


# ---------------------------------------------------------------------------
# StreamingStripper — state-machine implementation
# ---------------------------------------------------------------------------

# Open-tag prefixes (lowercase) used for detection
_OPEN_APPEND = "<memory_append"
_OPEN_CONSOLIDATION = "<memory_consolidation"
_CLOSE_APPEND = "</memory_append>"
_CLOSE_CONSOLIDATION = "</memory_consolidation>"


class StreamingStripper:
    """State-machine stripper for <memory_append> and <memory_consolidation> blocks.

    Tolerates any whitespace or attributes inside the opening tag (e.g. the
    model adding a newline between ``<memory_append`` and ``>``).  Blocks must
    appear before the prose response; once the first non-block character is
    seen, buffering stops and everything is streamed live.

    Call feed() for each streamed chunk; call flush() once after the stream
    ends.  The captured JSON payloads are available as append_json and
    consolidation_json after flush().
    """

    def __init__(self) -> None:
        self._buf = ""          # accumulated text not yet safe to emit
        self._in_block: str | None = None   # "append" or "consolidation"
        self._block_content: list[str] = [] # raw chars inside current block
        self.append_json: str | None = None
        self.consolidation_json: str | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_open(self, text: str) -> tuple[str | None, int, int]:
        """Return (block_type, match_start, tag_end) for the earliest open tag.

        tag_end is the index just after the closing ``>`` of the opening tag,
        or -1 if the tag isn't fully present yet.
        """
        lo = text.lower()
        best_type: str | None = None
        best_start = len(text)
        best_end = -1

        for btype, prefix in (("append", _OPEN_APPEND), ("consolidation", _OPEN_CONSOLIDATION)):
            pos = lo.find(prefix)
            if pos == -1:
                continue
            if pos < best_start:
                # Find the ``>`` that closes the opening tag
                gt = text.find(">", pos + len(prefix))
                best_type = btype
                best_start = pos
                best_end = gt + 1 if gt != -1 else -1

        return best_type, best_start, best_end

    def _find_close(self, text: str, block_type: str) -> int:
        """Return index just after the closing tag, or -1 if not present."""
        close_tag = _CLOSE_APPEND if block_type == "append" else _CLOSE_CONSOLIDATION
        pos = text.lower().find(close_tag)
        if pos == -1:
            return -1
        return pos + len(close_tag)

    def _save_block(self, block_type: str, content: str) -> None:
        payload = content.strip()
        if block_type == "append":
            self.append_json = payload
        else:
            self.consolidation_json = payload

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def feed(self, chunk: str) -> str:
        """Accept a streaming chunk; return text safe to display immediately."""
        self._buf += chunk
        safe_parts: list[str] = []

        while True:
            if self._in_block is None:
                # NORMAL state — look for an opening tag
                btype, start, tag_end = self._find_open(self._buf)
                if btype is None:
                    # No tag at all — but hold back the last few chars in case
                    # an opening tag is split across chunks.
                    hold = max(0, len(self._buf) - max(len(_OPEN_APPEND), len(_OPEN_CONSOLIDATION)))
                    safe_parts.append(self._buf[:hold])
                    self._buf = self._buf[hold:]
                    break
                else:
                    # Emit everything before the tag start
                    safe_parts.append(self._buf[:start])
                    if tag_end == -1:
                        # Opening tag not yet complete — hold the rest
                        self._buf = self._buf[start:]
                        break
                    # Opening tag complete — enter IN_BLOCK state
                    self._in_block = btype
                    self._block_content = []
                    self._buf = self._buf[tag_end:]
                    # fall through to IN_BLOCK handling
            else:
                # IN_BLOCK state — look for the closing tag
                close_end = self._find_close(self._buf, self._in_block)
                if close_end == -1:
                    # Closing tag not yet seen — keep buffering
                    break
                # Capture content before the closing tag
                close_tag = _CLOSE_APPEND if self._in_block == "append" else _CLOSE_CONSOLIDATION
                close_start = self._buf.lower().find(close_tag)
                self._block_content.append(self._buf[:close_start])
                self._save_block(self._in_block, "".join(self._block_content))
                self._buf = self._buf[close_end:]
                self._in_block = None
                self._block_content = []
                # continue loop — may be another block or normal text

        return "".join(safe_parts)

    def flush(self) -> str:
        """Flush remaining buffer.  Any incomplete block is silently discarded."""
        if self._in_block is not None:
            # Incomplete block — discard buffered content, save what we have
            self._save_block(self._in_block, "".join(self._block_content) + self._buf)
            self._buf = ""
            self._in_block = None
            self._block_content = []
            return ""
        remaining = self._buf
        self._buf = ""
        return remaining


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _append_to_file(path: Path, content: str) -> None:
    """Append content to a flat memory file (user.md / notes.md)."""
    if not content.strip():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_file(path)
    with path.open("a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(content.rstrip() + "\n")


def _append_to_auto_short(path: Path, content: str) -> None:
    """Append content under today's dated section in auto_short.md."""
    if not content.strip():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    today_header = f"## {date.today().isoformat()}"
    text = _read_file(path) or "# Short Memories\n"

    if today_header in text:
        # Append to end of today's existing section (before next ## header or EOF)
        header_pos = text.index(today_header)
        next_match = re.search(r"\n## ", text[header_pos + 1:])
        if next_match:
            insert_at = header_pos + 1 + next_match.start()
            text = text[:insert_at].rstrip() + "\n" + content.rstrip() + "\n" + text[insert_at:]
        else:
            text = text.rstrip() + "\n" + content.rstrip() + "\n"
    else:
        text = text.rstrip() + f"\n\n{today_header}\n\n{content.rstrip()}\n"

    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Memory operations
# ---------------------------------------------------------------------------

def append_memories(memories_dir: Path, payload: dict) -> None:
    """Append new content from a model JSON payload to memory files."""
    memories_dir.mkdir(parents=True, exist_ok=True)
    if user_content := payload.get("user", "").strip():
        _append_to_file(memories_dir / USER_FILE, user_content)
    if notes_content := payload.get("notes", "").strip():
        _append_to_file(memories_dir / NOTES_FILE, notes_content)
    if auto_content := payload.get("auto_short", "").strip():
        _append_to_auto_short(memories_dir / AUTO_FILE, auto_content)


def apply_consolidation(memories_dir: Path, payload: dict) -> None:
    """Rewrite memory files from a consolidation JSON payload.

    A key present in the payload always overwrites the file — even an empty
    string clears it.  A key absent from the payload leaves the file untouched.

    Does NOT touch the sentinel — call mark_consolidated() after all writes
    for the turn (including any subsequent append_memories call) so the sentinel
    is always newer than every memory file.
    """
    memories_dir.mkdir(parents=True, exist_ok=True)
    for key, fname in (("user", USER_FILE), ("notes", NOTES_FILE), ("auto_short", AUTO_FILE)):
        if key not in payload:
            continue
        content = payload[key].strip()
        if not content:
            (memories_dir / fname).write_text("", encoding="utf-8")
            continue
        # auto_short must have at least one dated section for trim_memories to work.
        # If the model didn't include one, wrap the content under today's date.
        if fname == AUTO_FILE and not re.search(r"^## \d{4}-\d{2}-\d{2}", content, re.MULTILINE):
            content = f"## {date.today().isoformat()}\n\n{content}"
        (memories_dir / fname).write_text(content.rstrip() + "\n", encoding="utf-8")


def trim_memories(memories_dir: Path, size_limit: int) -> None:
    """Trim oldest dated sections from auto_short.md until total size <= size_limit."""
    if size_limit <= 0:
        return
    path = memories_dir / AUTO_FILE
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if len(text) <= size_limit:
        return

    # Split on dated section headers; sections[0] is the intro
    sections = re.split(r"(?=\n## \d{4}-\d{2}-\d{2})", text)
    if len(sections) <= 1:
        return

    intro, dated = sections[0], sections[1:]
    while dated and len(intro + "".join(dated)) > size_limit:
        dated.pop(0)

    path.write_text(intro + "".join(dated), encoding="utf-8")


def needs_consolidation(memories_dir: Path) -> bool:
    """Return True if any memory file is newer than .last_consolidated, or sentinel is absent."""
    sentinel = memories_dir / SENTINEL_FILE
    if not sentinel.exists():
        return True
    sentinel_mtime = sentinel.stat().st_mtime
    for fname in (USER_FILE, NOTES_FILE, AUTO_FILE):
        f = memories_dir / fname
        if f.exists() and f.stat().st_mtime > sentinel_mtime:
            return True
    return False


def mark_consolidated(memories_dir: Path) -> None:
    """Touch .last_consolidated to record that consolidation just ran."""
    (memories_dir / SENTINEL_FILE).touch()


# ---------------------------------------------------------------------------
# Session cleanup
# ---------------------------------------------------------------------------

def _strip_memory_blocks(text: str) -> str:
    """Remove all memory block tags from a complete string."""
    text = _APPEND_RE.sub("", text)
    text = _CONSOLIDATION_RE.sub("", text)
    return text


async def clean_last_turn(store, session_id: str) -> None:
    """Strip memory blocks from the last assistant turn so they don't leak into session history."""
    session = await store.get(session_id)
    if not session or not session.turns:
        return
    last = session.turns[-1]
    if last.role == "assistant" and (
        _APPEND_RE.search(last.content) or _CONSOLIDATION_RE.search(last.content)
    ):
        session.turns[-1] = dataclasses.replace(last, content=_strip_memory_blocks(last.content))
        await store.save(session)
