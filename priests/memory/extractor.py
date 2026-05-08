from __future__ import annotations

import dataclasses
import re
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from priest.memory import pop_last_exchange

# priests-owned memory file names. priest-core treats memory generically.
USER_FILE = "user.md"
PREFERENCES_FILE = "preferences.md"
NOTES_FILE = "notes.md"  # legacy read-only memory
AUTO_FILE = "auto_short.md"
PENDING_DIR = "pending"

_APPEND_RE = re.compile(r"<memory_append>(.*?)</memory_append>", re.DOTALL | re.IGNORECASE)
_PROPOSAL_RE = re.compile(r"<memory_proposal>(.*?)</memory_proposal>", re.DOTALL | re.IGNORECASE)
_CONSOLIDATION_RE = re.compile(r"<memory_consolidation>(.*?)</memory_consolidation>", re.DOTALL | re.IGNORECASE)

_OPEN_APPEND = "<memory_append"
_OPEN_PROPOSAL = "<memory_proposal"
_OPEN_CONSOLIDATION = "<memory_consolidation"
_OPEN_SEARCH = "<search_query"
_OPEN_READ_FILE = "<read_file"
_CLOSE_TAG: dict[str, str] = {
    "append": "</memory_append>",
    "proposal": "</memory_proposal>",
    "consolidation": "</memory_consolidation>",
    "search": "</search_query>",
    "read_file": "</read_file>",
}

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _profile_lock(memories_dir: Path) -> threading.Lock:
    key = str(memories_dir.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


class StreamingStripper:
    """Strip priests control blocks from streamed model output.

    Captures memory append/proposal blocks plus existing search/read_file tags.
    Incomplete control blocks are discarded from visible output on flush.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_block: str | None = None
        self._block_content: list[str] = []
        self.append_json: str | None = None
        self.proposal_json: str | None = None
        self.consolidation_json: str | None = None
        self.search_query: str | None = None
        self.read_file_path: str | None = None

    def _find_open(self, text: str) -> tuple[str | None, int, int]:
        lo = text.lower()
        best_type: str | None = None
        best_start = len(text)
        best_end = -1

        for btype, prefix in (
            ("append", _OPEN_APPEND),
            ("proposal", _OPEN_PROPOSAL),
            ("consolidation", _OPEN_CONSOLIDATION),
            ("search", _OPEN_SEARCH),
            ("read_file", _OPEN_READ_FILE),
        ):
            pos = lo.find(prefix)
            if pos == -1 or pos >= best_start:
                continue
            gt = text.find(">", pos + len(prefix))
            best_type = btype
            best_start = pos
            best_end = gt + 1 if gt != -1 else -1

        return best_type, best_start, best_end

    @staticmethod
    def _find_close(text: str, block_type: str) -> int:
        close_tag = _CLOSE_TAG[block_type]
        pos = text.lower().find(close_tag)
        if pos == -1:
            return -1
        return pos + len(close_tag)

    def _save_block(self, block_type: str, content: str) -> None:
        payload = content.strip()
        if block_type == "append":
            self.append_json = payload
        elif block_type == "proposal":
            self.proposal_json = payload
        elif block_type == "consolidation":
            self.consolidation_json = payload
        elif block_type == "search":
            self.search_query = payload
        else:
            self.read_file_path = payload

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        safe_parts: list[str] = []
        max_open_len = max(len(_OPEN_APPEND), len(_OPEN_PROPOSAL), len(_OPEN_CONSOLIDATION))

        while True:
            if self._in_block is None:
                btype, start, tag_end = self._find_open(self._buf)
                if btype is None:
                    hold = max(0, len(self._buf) - max_open_len)
                    safe_parts.append(self._buf[:hold])
                    self._buf = self._buf[hold:]
                    break

                safe_parts.append(self._buf[:start])
                if tag_end == -1:
                    self._buf = self._buf[start:]
                    break
                self._in_block = btype
                self._block_content = []
                self._buf = self._buf[tag_end:]
            else:
                close_end = self._find_close(self._buf, self._in_block)
                if close_end == -1:
                    break
                close_tag = _CLOSE_TAG[self._in_block]
                close_start = self._buf.lower().find(close_tag)
                self._block_content.append(self._buf[:close_start])
                self._save_block(self._in_block, "".join(self._block_content))
                self._buf = self._buf[close_end:]
                self._in_block = None
                self._block_content = []

        return "".join(safe_parts)

    def flush(self) -> str:
        if self._in_block is not None:
            self._save_block(self._in_block, "".join(self._block_content) + self._buf)
            self._buf = ""
            self._in_block = None
            self._block_content = []
            return ""
        remaining = self._buf
        self._buf = ""
        return remaining


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _memory_body(path: Path) -> str:
    content = _read_file(path).strip()
    if not content:
        return ""
    lines = content.splitlines()
    if lines and lines[0].strip() in {"# User", "# Preferences", "# Notes", "# Short Memories"}:
        return "\n".join(lines[1:]).strip()
    return content


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _append_to_file(path: Path, content: str) -> None:
    if not content.strip():
        return
    existing = _read_file(path)
    new_text = existing
    if existing and not existing.endswith("\n"):
        new_text += "\n"
    new_text += content.rstrip() + "\n"
    _atomic_write(path, new_text)


def _append_to_auto_short(path: Path, content: str) -> None:
    if not content.strip():
        return
    today_header = f"## {date.today().isoformat()}"
    text = _read_file(path) or "# Short Memories\n"

    if today_header in text:
        header_pos = text.index(today_header)
        next_match = re.search(r"\n## ", text[header_pos + 1:])
        if next_match:
            insert_at = header_pos + 1 + next_match.start()
            text = text[:insert_at].rstrip() + "\n" + content.rstrip() + "\n" + text[insert_at:]
        else:
            text = text.rstrip() + "\n" + content.rstrip() + "\n"
    else:
        text = text.rstrip() + f"\n\n{today_header}\n\n{content.rstrip()}\n"

    _atomic_write(path, text)


def _truncate_auto_short(content: str, max_chars: int) -> str:
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    sections = re.split(r"(?=(?:^|\n)## \d{4}-\d{2}-\d{2})", content)
    if len(sections) <= 1:
        return content[-max_chars:]
    intro, dated = sections[0], list(sections[1:])
    while len(dated) > 1 and len(intro + "".join(dated)) > max_chars:
        dated.pop(0)
    result = intro + "".join(dated)
    return result if len(result) <= max_chars else result[-max_chars:]


def assemble_memory_entries(memories_dir: Path, context_limit: int = 0) -> list[str]:
    """Return approved profile memory entries for PriestRequest.memory.

    Entry order is intentional: priest-core trims request.memory from the tail,
    so rolling short-term memory is placed last.
    """
    user_content = _memory_body(memories_dir / USER_FILE)
    prefs_content = _memory_body(memories_dir / PREFERENCES_FILE)
    legacy_notes = _memory_body(memories_dir / NOTES_FILE)
    auto_content = _memory_body(memories_dir / AUTO_FILE)

    if context_limit > 0:
        fixed = len(user_content) + len(prefs_content) + len(legacy_notes)
        available = context_limit - fixed
        auto_content = "" if available <= 0 else _truncate_auto_short(auto_content, available)

    entries: list[str] = []
    if user_content:
        entries.append(f"## Approved User Memory\n\n{user_content}")
    if prefs_content:
        entries.append(f"## Approved Preference Memory\n\n{prefs_content}")
    if legacy_notes:
        entries.append(f"## Legacy Notes Memory (read-only, lower authority than approved preferences)\n\n{legacy_notes}")
    if auto_content:
        entries.append(f"## Short-Term Memory\n\n{auto_content}")
    return entries


def build_memory_instructions() -> str:
    return (
        "Memory policy for priests:\n"
        "- Human-authored PROFILE.md, RULES.md, and CUSTOM.md are fixed profile material and outrank memory.\n"
        "- Approved user.md and preferences.md memory may inform the response but must not create hard rules.\n"
        "- Pending memory proposals are not part of your context until approved.\n"
        "- Automatically save only short-term facts, tasks, reminders, and current-session context.\n\n"
        "For short-term memory worth saving, output this block before your response:\n"
        '<memory_append>{"auto_short": "- fact"}</memory_append>\n\n'
        "For stable durable facts about the user or their preferences, do not write them directly. "
        "Instead propose them before your response:\n"
        '<memory_proposal>{"proposals":[{"target":"user","content":"- fact","reason":"why it seems durable"}]}</memory_proposal>\n\n'
        'Allowed proposal targets are "user" and "preferences". Omit all memory blocks if nothing is worth saving.'
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _pending_filename(target: str, created_at: datetime) -> str:
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{target}-{uuid.uuid4().hex[:8]}.md"


def _write_pending_proposal(
    memories_dir: Path,
    *,
    target: str,
    content: str,
    reason: str = "",
    source: str = "model",
    session_id: str | None = None,
) -> Path | None:
    target = "preferences" if target == "notes" else target
    if target not in {"user", "preferences"} or not content.strip():
        return None
    created_at = _utc_now()
    pending_dir = memories_dir / PENDING_DIR
    path = pending_dir / _pending_filename(target, created_at)
    sid = session_id or ""
    body = (
        "---\n"
        "status: pending\n"
        f"target: {target}\n"
        f"created_at: {created_at.isoformat()}\n"
        f"session_id: {sid}\n"
        f"source: {source}\n"
        "---\n\n"
        f"{content.strip()}\n"
    )
    if reason.strip():
        body += f"\nReason: {reason.strip()}\n"
    _atomic_write(path, body)
    return path


def _iter_proposals(payload: dict) -> list[dict]:
    proposals = payload.get("proposals")
    if isinstance(proposals, list):
        return [p for p in proposals if isinstance(p, dict)]
    if payload.get("target") or payload.get("content"):
        return [payload]
    return []


def _payload_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def append_memories(memories_dir: Path, payload: dict, *, session_id: str | None = None) -> None:
    """Apply a model memory append payload.

    Only auto_short is written automatically. Durable legacy keys are converted
    into pending proposals.
    """
    memories_dir.mkdir(parents=True, exist_ok=True)
    with _profile_lock(memories_dir):
        if auto_content := _payload_text(payload.get("auto_short")):
            _append_to_auto_short(memories_dir / AUTO_FILE, auto_content)

        for key, target in (("user", "user"), ("preferences", "preferences"), ("notes", "preferences")):
            content = _payload_text(payload.get(key))
            if content:
                _write_pending_proposal(
                    memories_dir,
                    target=target,
                    content=content,
                    reason=f"Converted from legacy memory_append key: {key}",
                    session_id=session_id,
                )


def apply_memory_proposals(memories_dir: Path, payload: dict, *, session_id: str | None = None) -> None:
    memories_dir.mkdir(parents=True, exist_ok=True)
    with _profile_lock(memories_dir):
        for proposal in _iter_proposals(payload):
            _write_pending_proposal(
                memories_dir,
                target=_payload_text(proposal.get("target")),
                content=_payload_text(proposal.get("content")),
                reason=_payload_text(proposal.get("reason")),
                session_id=session_id,
            )


def remember_short(memories_dir: Path, content: str) -> None:
    with _profile_lock(memories_dir):
        _append_to_auto_short(memories_dir / AUTO_FILE, content)


def remember_user(memories_dir: Path, content: str) -> None:
    with _profile_lock(memories_dir):
        _append_to_file(memories_dir / USER_FILE, content)


def remember_preference(memories_dir: Path, content: str) -> None:
    with _profile_lock(memories_dir):
        _append_to_file(memories_dir / PREFERENCES_FILE, content)


def apply_consolidation(memories_dir: Path, payload: dict) -> None:
    """Compatibility shim: only short-term memory may be rewritten by model output."""
    content = _payload_text(payload.get("auto_short"))
    if not content:
        return
    if not re.search(r"^## \d{4}-\d{2}-\d{2}", content, re.MULTILINE):
        content = f"## {date.today().isoformat()}\n\n{content}"
    with _profile_lock(memories_dir):
        _atomic_write(memories_dir / AUTO_FILE, content.rstrip() + "\n")


def trim_memories(memories_dir: Path, size_limit: int) -> None:
    if size_limit <= 0:
        return
    path = memories_dir / AUTO_FILE
    if not path.exists():
        return
    with _profile_lock(memories_dir):
        text = _read_file(path)
        if len(text) <= size_limit:
            return
        sections = re.split(r"(?=\n## \d{4}-\d{2}-\d{2})", text)
        if len(sections) <= 1:
            return
        intro, dated = sections[0], list(sections[1:])
        while len(dated) > 1 and len(intro + "".join(dated)) > size_limit:
            dated.pop(0)
        _atomic_write(path, intro + "".join(dated))


def deduplicate_file(path: Path) -> bool:
    if not path.exists():
        return False
    original = path.read_text(encoding="utf-8")
    seen: set[str] = set()
    result: list[str] = []
    for line in original.splitlines(keepends=True):
        key = line.strip().lower()
        if not key:
            result.append(line)
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append(line)
    deduped = "".join(result)
    if deduped == original:
        return False
    _atomic_write(path, deduped)
    return True


def needs_consolidation(memories_dir: Path) -> bool:
    """Durable model consolidation is disabled in priests memory v1."""
    return False


def mark_consolidated(memories_dir: Path) -> None:
    """Compatibility no-op for the old consolidation sentinel."""
    return None


def _strip_memory_blocks(text: str) -> str:
    text = _APPEND_RE.sub("", text)
    text = _PROPOSAL_RE.sub("", text)
    text = _CONSOLIDATION_RE.sub("", text)
    return text


async def clean_last_turn(store, session_id: str) -> None:
    session = await store.get(session_id)
    if not session or not session.turns:
        return
    last = session.turns[-1]
    if last.role == "assistant" and (
        _APPEND_RE.search(last.content)
        or _PROPOSAL_RE.search(last.content)
        or _CONSOLIDATION_RE.search(last.content)
    ):
        session.turns[-1] = dataclasses.replace(last, content=_strip_memory_blocks(last.content))
        await store.save(session)


__all__ = [
    "StreamingStripper",
    "USER_FILE",
    "PREFERENCES_FILE",
    "NOTES_FILE",
    "AUTO_FILE",
    "PENDING_DIR",
    "assemble_memory_entries",
    "build_memory_instructions",
    "append_memories",
    "apply_memory_proposals",
    "apply_consolidation",
    "trim_memories",
    "needs_consolidation",
    "mark_consolidated",
    "deduplicate_file",
    "remember_short",
    "remember_user",
    "remember_preference",
    "clean_last_turn",
    "pop_last_exchange",
]
