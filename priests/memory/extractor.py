from __future__ import annotations

import dataclasses
import json
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from priest.memory import pop_last_exchange

try:  # POSIX cross-process locking; tests also run on POSIX.
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback.
    fcntl = None  # type: ignore[assignment]

# Legacy Markdown memory file names. They remain readable fallback inputs.
USER_FILE = "user.md"
PREFERENCES_FILE = "preferences.md"
NOTES_FILE = "notes.md"
AUTO_FILE = "auto_short.md"

# Canonical structured memory files.
USER_JSONL_FILE = "user.jsonl"
PREFERENCES_JSONL_FILE = "preferences.jsonl"
AUTO_JSONL_FILE = "auto_short.jsonl"

_KIND_TO_JSONL = {
    "user": USER_JSONL_FILE,
    "preferences": PREFERENCES_JSONL_FILE,
    "auto_short": AUTO_JSONL_FILE,
}

_SAVE_RE = re.compile(r"<memory_save>(.*?)</memory_save>", re.DOTALL | re.IGNORECASE)
_APPEND_RE = re.compile(r"<memory_append>(.*?)</memory_append>", re.DOTALL | re.IGNORECASE)
_PROPOSAL_RE = re.compile(r"<memory_proposal>(.*?)</memory_proposal>", re.DOTALL | re.IGNORECASE)
_FORGET_RE = re.compile(r"<memory_forget>(.*?)</memory_forget>", re.DOTALL | re.IGNORECASE)
_CONSOLIDATION_RE = re.compile(r"<memory_consolidation>(.*?)</memory_consolidation>", re.DOTALL | re.IGNORECASE)

_OPEN_SAVE = "<memory_save"
_OPEN_APPEND = "<memory_append"
_OPEN_PROPOSAL = "<memory_proposal"
_OPEN_FORGET = "<memory_forget"
_OPEN_CONSOLIDATION = "<memory_consolidation"
_OPEN_SEARCH = "<search_query"
_OPEN_READ_FILE = "<read_file"
_CLOSE_TAG: dict[str, str] = {
    "save": "</memory_save>",
    "append": "</memory_append>",
    "proposal": "</memory_proposal>",
    "forget": "</memory_forget>",
    "consolidation": "</memory_consolidation>",
    "search": "</search_query>",
    "read_file": "</read_file>",
}

_ALLOWED_KINDS = {"user", "preferences", "auto_short"}
_ALLOWED_STABILITY = {"stable", "evolving", "session", "ephemeral"}
_ALLOWED_SOURCE = {"user_direct", "model_inferred", "system"}
_ALLOWED_STATUS = {"active", "superseded"}
_CONFLICT_KEY_RE = re.compile(r"^(?:user|preferences|auto_short)(?:\.[a-z0-9][a-z0-9_]{0,39}){1,5}$")
_GENERIC_CONFLICT_KEYS = {
    "user.info",
    "user.fact",
    "user.memory",
    "preferences.info",
    "preferences.fact",
    "preferences.memory",
    "auto_short.info",
    "auto_short.fact",
    "auto_short.memory",
}
_CONFLICT_KEY_ALIASES = {
    "user.fav_color": "user.favorite_color",
    "user.favorite_colour": "user.favorite_color",
    "user.preferred_color": "user.favorite_color",
    "user.preferred_colour": "user.favorite_color",
    "user.color": "user.favorite_color",
    "user.colour": "user.favorite_color",
    "user.color_preference": "user.favorite_color",
    "user.colour_preference": "user.favorite_color",
    "user.preferred_name": "user.name",
    "preferences.answer_style": "preferences.reply_style",
    "preferences.answers_style": "preferences.reply_style",
    "preferences.response_style": "preferences.reply_style",
    "preferences.responses_style": "preferences.reply_style",
    "preferences.communication_style": "preferences.reply_style",
    "preferences.conversation_style": "preferences.reply_style",
    "preferences.tone_style": "preferences.reply_style",
    "preferences.reply_length": "preferences.reply_style",
    "preferences.response_length": "preferences.reply_style",
    "preferences.preferred_language": "preferences.language",
    "preferences.language_preference": "preferences.language",
    "auto_short.project_meeting": "auto_short.project_meeting_time",
    "auto_short.meeting": "auto_short.meeting_time",
}

_DEFAULT_PRIORITY = 5
_NORMAL_PRIORITY_CUTOFF = 3
_THINKING_PRIORITY_CUTOFF = 10
_SIMPLE_CORE_PRIORITY_CUTOFF = 0

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


@contextmanager
def _memory_write_lock(memories_dir: Path):
    """Serialize memory writers across threads and cooperating processes."""
    memories_dir.mkdir(parents=True, exist_ok=True)
    with _profile_lock(memories_dir):
        if fcntl is None:
            yield
            return

        lock_path = memories_dir / ".memories.lock"
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def _clamp_int(value: object, default: int, low: int, high: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _clamp_float(value: object, default: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _payload_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize_kind(value: object) -> str:
    if not isinstance(value, str):
        return ""
    key = value.strip().lower().replace("-", "_")
    aliases = {
        "pref": "preferences",
        "prefs": "preferences",
        "preference": "preferences",
        "preferences": "preferences",
        "notes": "preferences",
        "note": "auto_short",
        "short": "auto_short",
        "short_term": "auto_short",
        "session": "auto_short",
        "current": "auto_short",
        "auto": "auto_short",
        "auto_short": "auto_short",
        "user": "user",
    }
    return aliases.get(key, "")


def _looks_time_sensitive(text: str) -> bool:
    normalized = _normalize_text(text)
    if not re.search(r"\b(today|tomorrow|tonight|meeting|deadline|appointment|reminder|schedule)\b", normalized):
        return False
    return bool(
        re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|am|pm)\b", normalized)
        or re.search(r"\b\d{4}-\d{2}-\d{2}\b", normalized)
    )


def _looks_response_preference(text: str) -> bool:
    normalized = _normalize_text(text)
    if re.search(r"(偏好|喜欢).*(回答|回复|简短|详细|中文|英文)", normalized):
        return True
    if not re.search(r"\b(prefer|prefers|preference|like|likes)\b", normalized):
        return False
    return bool(
        re.search(r"\b(reply|replies|answer|answers|response|responses|conversation|tone|style)\b", normalized)
        or re.search(r"\b(short|brief|concise|detailed|normal|casual|formal)\b", normalized)
    )


def _looks_identity_name_fact(text: str) -> bool:
    normalized = _normalize_text(text)
    return bool(
        re.search(
            r"\b(?:the\s+)?user(?:'s)?\s+name\s+is\b"
            r"|\buser\s+is\s+named\b"
            r"|^name\s*:"
            r"|\bpreferred\s+name\b"
            r"|\bcall\s+(?:the\s+)?user\b",
            normalized,
        )
    )


def _default_stability(kind: str) -> str:
    return "session" if kind == "auto_short" else "evolving"


def _default_priority(kind: str, explicit_default: int = _DEFAULT_PRIORITY) -> int:
    return explicit_default if 0 <= explicit_default <= 10 else _DEFAULT_PRIORITY


def _normalize_text(text: str) -> str:
    text = re.sub(r"^\s*[-*]\s*", "", text.strip().lower())
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .;")


def _slot_key(text: str) -> str:
    key = text.strip().lower().replace("colour", "color")
    key = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    key = re.sub(r"_+", "_", key)
    if key.startswith("fav_"):
        key = f"favorite_{key[4:]}"
    return key[:40].strip("_")


def _normalize_conflict_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    key = value.strip().lower().replace("-", "_").replace(":", ".")
    key = re.sub(r"\s+", "_", key)
    key = re.sub(r"\.+", ".", key).strip(".")
    key = _CONFLICT_KEY_ALIASES.get(key, key)
    if key.startswith("user.preferred_") and key != "user.preferred_name":
        key = f"user.favorite_{key.removeprefix('user.preferred_')}"
    if key.startswith("user.fav_"):
        key = f"user.favorite_{key.removeprefix('user.fav_')}"
    key = key.replace("favourite", "favorite").replace("colour", "color")
    key = _CONFLICT_KEY_ALIASES.get(key, key)
    if key in _GENERIC_CONFLICT_KEYS:
        return ""
    if not _CONFLICT_KEY_RE.fullmatch(key):
        return ""
    return key


def _entry_key(entry: "MemoryEntry") -> str:
    return f"{entry.kind}:{_normalize_text(entry.text)}"


def _favorite_conflict_key_from_text(text: str) -> str:
    patterns = (
        r"\b(?:the\s+)?user(?:'s)?\s+favou?rite\s+([a-z0-9][a-z0-9 _-]{0,40}?)\s+(?:is|=|:)\b",
        r"\bmy\s+favou?rite\s+([a-z0-9][a-z0-9 _-]{0,40}?)\s+(?:is|=|:)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        slot = _slot_key(match.group(1))
        if slot:
            return _normalize_conflict_key(f"user.favorite_{slot}")
    return ""


def _inferred_conflict_group(entry: "MemoryEntry") -> str | None:
    text = _normalize_text(entry.text)
    if entry.kind == "user":
        if _looks_identity_name_fact(text):
            return "user.name"
        if favorite_key := _favorite_conflict_key_from_text(text):
            return favorite_key

    if entry.kind == "preferences" and _looks_response_preference(entry.text):
        return "preferences.reply_style"

    if "meeting" in text and re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|am|pm)\b", text):
        date_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b|\btomorrow\b|\btoday\b", text)
        date_key = date_match.group(0) if date_match else "unspecified"
        topic_key = "project" if "project meeting" in text else "general"
        return f"{entry.kind}:meeting:{date_key}:{topic_key}"
    return None


def _conflict_groups(entry: "MemoryEntry") -> set[str]:
    groups: set[str] = set()
    if entry.conflict_key:
        groups.add(entry.conflict_key)
        if entry.conflict_key.endswith("project_meeting_time"):
            groups.add(f"{entry.kind}:meeting:project")
        elif entry.conflict_key.endswith("meeting_time"):
            groups.add(f"{entry.kind}:meeting:general")
    if inferred := _inferred_conflict_group(entry):
        groups.add(inferred)
        if ":meeting:" in inferred:
            parts = inferred.split(":")
            if len(parts) >= 4:
                groups.add(f"{entry.kind}:meeting:{parts[-1]}")
    return groups


def _meeting_date(text: str) -> str:
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b|\btomorrow\b|\btoday\b|\btonight\b", text, flags=re.IGNORECASE)
    return match.group(0).lower() if match else ""


def _preserve_meeting_date(incoming: "MemoryEntry", conflicts: list["MemoryEntry"]) -> None:
    if incoming.kind != "auto_short":
        return
    incoming_text = _normalize_text(incoming.text)
    if "meeting" not in incoming_text or _meeting_date(incoming.text):
        return
    for entry in conflicts:
        date_text = _meeting_date(entry.text)
        if not date_text:
            continue
        incoming.text = re.sub(
            r"\b(meeting)(\s+at\s+)",
            rf"\1 {date_text}\2",
            incoming.text,
            count=1,
            flags=re.IGNORECASE,
        )
        return


def _format_bullet(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if "\n" in stripped:
        return stripped
    if stripped.startswith(("- ", "* ")):
        return stripped
    return f"- {stripped}"


def _format_memory_entry(entry: "MemoryEntry") -> str:
    line = _format_bullet(entry.text)
    if not line or entry.kind != "auto_short":
        return line
    date_text = _meeting_date(entry.text)
    if date_text:
        line += f" (When answering about this, include the date word exactly: {date_text}.)"
    return line


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def is_simple_memory_prompt(prompt: str) -> bool:
    """Return True for prompts that should not pay broad memory costs."""
    if not isinstance(prompt, str):
        return False
    normalized = _normalize_text(prompt)
    if not normalized or len(normalized) > 80:
        return False
    simple_patterns = (
        r"^(?:hi|hello|hey|yo|sup|hiya|howdy)$",
        r"^(?:hi|hello|hey|yo|hiya|howdy)[!. ]*$",
        r"^(?:thanks|thank you|thx|ty|ok|okay|k|cool|nice|great|got it|sounds good)[!. ]*$",
        r"^(?:good morning|good afternoon|good evening|good night)[!. ]*$",
        r"^(?:yes|no|yep|yeah|nope|sure|alright|all right)[!. ]*$",
    )
    return any(re.fullmatch(pattern, normalized) for pattern in simple_patterns)


def should_inject_memory_instructions(prompt: str) -> bool:
    """Memory write policy can be skipped for cheap non-memory turns."""
    return not is_simple_memory_prompt(prompt)


@dataclasses.dataclass
class MemoryEntry:
    id: str
    kind: str
    text: str
    priority: int = _DEFAULT_PRIORITY
    confidence: float = 0.6
    stability: str = "evolving"
    source: str = "model_inferred"
    evidence: str = ""
    reason: str = ""
    status: str = "active"
    conflict_key: str = ""
    supersedes: list[str] = dataclasses.field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    last_seen_at: str = ""
    expires_at: str | None = None

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any],
        *,
        default_kind: str = "",
        default_priority: int = _DEFAULT_PRIORITY,
        default_confidence: float = 0.6,
        default_source: str = "model_inferred",
    ) -> "MemoryEntry | None":
        kind = _normalize_kind(raw.get("kind") or raw.get("target") or default_kind)
        text = _payload_text(raw.get("text") or raw.get("content"))
        if kind not in _ALLOWED_KINDS or not text:
            return None
        if kind in {"user", "preferences"} and _looks_time_sensitive(text):
            kind = "auto_short"
        elif kind == "user" and _looks_response_preference(text):
            kind = "preferences"

        now = _utc_now()
        priority = _clamp_int(raw.get("priority"), _default_priority(kind, default_priority), 0, 10)
        confidence = _clamp_float(raw.get("confidence"), default_confidence)
        stability = raw.get("stability") if isinstance(raw.get("stability"), str) else _default_stability(kind)
        if stability not in _ALLOWED_STABILITY:
            stability = _default_stability(kind)

        source = raw.get("source") if isinstance(raw.get("source"), str) else default_source
        if source not in _ALLOWED_SOURCE:
            source = default_source if default_source in _ALLOWED_SOURCE else "model_inferred"

        status = raw.get("status") if isinstance(raw.get("status"), str) else "active"
        if status not in _ALLOWED_STATUS:
            status = "active"

        raw_conflict_value = raw.get("conflict_key") or raw.get("conflicts_with")
        raw_conflict_provided = bool(_payload_text(raw_conflict_value))
        conflict_key = _normalize_conflict_key(raw_conflict_value)
        if conflict_key and not conflict_key.startswith(f"{kind}."):
            conflict_key = ""
        if kind == "preferences" and not conflict_key and not raw_conflict_provided and _looks_response_preference(text):
            conflict_key = "preferences.reply_style"
        if kind == "auto_short" and not conflict_key and not raw_conflict_provided and "meeting" in _normalize_text(text):
            if "project meeting" in _normalize_text(text):
                conflict_key = "auto_short.project_meeting_time"
            elif re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|am|pm)\b", _normalize_text(text)):
                conflict_key = "auto_short.meeting_time"

        priority_zero_allowed = (
            kind == "user"
            and confidence >= 0.9
            and stability == "stable"
            and (conflict_key == "user.name" or _looks_identity_name_fact(text))
        )
        if priority == 0 and not priority_zero_allowed:
            if kind == "preferences":
                priority = 2
            elif kind == "auto_short":
                priority = 3
            else:
                priority = 1

        raw_supersedes = raw.get("supersedes")
        supersedes = [str(v) for v in raw_supersedes if str(v).strip()] if isinstance(raw_supersedes, list) else []

        entry_id = raw.get("id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            entry_id = f"mem_{uuid.uuid4().hex}"

        created_at = _payload_text(raw.get("created_at")) or now
        updated_at = _payload_text(raw.get("updated_at")) or now
        last_seen_at = _payload_text(raw.get("last_seen_at")) or updated_at
        expires_at = raw.get("expires_at") if isinstance(raw.get("expires_at"), str) else None

        return cls(
            id=entry_id,
            kind=kind,
            text=text,
            priority=priority,
            confidence=confidence,
            stability=stability,
            source=source,
            evidence=_payload_text(raw.get("evidence")),
            reason=_payload_text(raw.get("reason")),
            status=status,
            conflict_key=conflict_key,
            supersedes=supersedes,
            created_at=created_at,
            updated_at=updated_at,
            last_seen_at=last_seen_at,
            expires_at=expires_at,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "priority": self.priority,
            "confidence": self.confidence,
            "stability": self.stability,
            "source": self.source,
            "evidence": self.evidence,
            "reason": self.reason,
            "status": self.status,
            "conflict_key": self.conflict_key,
            "supersedes": self.supersedes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_seen_at": self.last_seen_at,
            "expires_at": self.expires_at,
        }

    def is_expired(self, now_ts: float | None = None) -> bool:
        if not self.expires_at:
            return False
        cutoff = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        return 0 < _parse_timestamp(self.expires_at) <= cutoff


class StreamingStripper:
    """Strip priests control blocks from streamed model output.

    Captures structured memory-save blocks, legacy memory blocks, and existing
    search/read_file tags. Incomplete control blocks are discarded from visible
    output on flush.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_block: str | None = None
        self._block_content: list[str] = []
        self.save_json: str | None = None
        self.append_json: str | None = None
        self.proposal_json: str | None = None
        self.forget_json: str | None = None
        self.consolidation_json: str | None = None
        self.search_query: str | None = None
        self.read_file_path: str | None = None
        self.save_jsons: list[str] = []
        self.append_jsons: list[str] = []
        self.proposal_jsons: list[str] = []
        self.forget_jsons: list[str] = []
        self.consolidation_jsons: list[str] = []
        self.search_queries: list[str] = []
        self.read_file_paths: list[str] = []

    def _find_open(self, text: str) -> tuple[str | None, int, int]:
        lo = text.lower()
        best_type: str | None = None
        best_start = len(text)
        best_end = -1

        for btype, prefix in (
            ("save", _OPEN_SAVE),
            ("append", _OPEN_APPEND),
            ("proposal", _OPEN_PROPOSAL),
            ("forget", _OPEN_FORGET),
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
        if block_type == "save":
            self.save_json = payload
            self.save_jsons.append(payload)
        elif block_type == "append":
            self.append_json = payload
            self.append_jsons.append(payload)
        elif block_type == "proposal":
            self.proposal_json = payload
            self.proposal_jsons.append(payload)
        elif block_type == "forget":
            self.forget_json = payload
            self.forget_jsons.append(payload)
        elif block_type == "consolidation":
            self.consolidation_json = payload
            self.consolidation_jsons.append(payload)
        elif block_type == "search":
            self.search_query = payload
            self.search_queries.append(payload)
        else:
            self.read_file_path = payload
            self.read_file_paths.append(payload)

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        safe_parts: list[str] = []
        max_open_len = max(
            len(_OPEN_SAVE),
            len(_OPEN_APPEND),
            len(_OPEN_PROPOSAL),
            len(_OPEN_FORGET),
            len(_OPEN_CONSOLIDATION),
            len(_OPEN_SEARCH),
            len(_OPEN_READ_FILE),
        )

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


def _load_jsonl(path: Path) -> list[MemoryEntry]:
    entries: list[MemoryEntry] = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            entry = MemoryEntry.from_dict(raw)
            if entry is not None:
                entries.append(entry)
    return entries


def _write_jsonl(path: Path, entries: list[MemoryEntry]) -> None:
    lines = [json.dumps(entry.to_json(), ensure_ascii=False, sort_keys=True) for entry in entries]
    _atomic_write(path, "\n".join(lines) + ("\n" if lines else ""))


def _legacy_entries_from_markdown(path: Path, kind: str, priority: int, reason: str) -> list[MemoryEntry]:
    body = _memory_body(path)
    if not body:
        return []

    now = _utc_now()
    entries: list[MemoryEntry] = []
    current_date = ""
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        date_match = re.match(r"^##\s+(\d{4}-\d{2}-\d{2})", line)
        if date_match:
            current_date = date_match.group(1)
            continue
        if line.startswith("#"):
            continue
        text = f"{current_date}: {line}" if current_date else line
        entries.append(
            MemoryEntry(
                id=f"legacy_{uuid.uuid4().hex}",
                kind=kind,
                text=text,
                priority=priority,
                confidence=1.0,
                stability="session" if kind == "auto_short" else "stable",
                source="system",
                reason=reason,
                status="active",
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
        )
    return entries


def _load_all_memory_entries(memories_dir: Path, *, include_legacy: bool = True) -> list[MemoryEntry]:
    entries: list[MemoryEntry] = []
    for kind, filename in _KIND_TO_JSONL.items():
        entries.extend(_load_jsonl(memories_dir / filename))

    if include_legacy:
        entries.extend(_legacy_entries_from_markdown(memories_dir / USER_FILE, "user", 3, "Legacy user.md fallback"))
        entries.extend(
            _legacy_entries_from_markdown(
                memories_dir / PREFERENCES_FILE,
                "preferences",
                3,
                "Legacy preferences.md fallback",
            )
        )
        entries.extend(
            _legacy_entries_from_markdown(memories_dir / NOTES_FILE, "preferences", 3, "Legacy notes.md fallback")
        )
        entries.extend(_legacy_entries_from_markdown(memories_dir / AUTO_FILE, "auto_short", 8, "Legacy auto_short.md fallback"))

    deduped: dict[str, MemoryEntry] = {}
    for entry in entries:
        key = _entry_key(entry)
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = entry
            continue
        if (entry.priority, -entry.confidence) < (existing.priority, -existing.confidence):
            deduped[key] = entry
    return list(deduped.values())


def _merge_entry(existing: list[MemoryEntry], incoming: MemoryEntry) -> None:
    now = _utc_now()
    incoming.updated_at = now
    incoming.last_seen_at = now
    key = _entry_key(incoming)

    for entry in existing:
        if _entry_key(entry) != key:
            continue
        entry.priority = min(entry.priority, incoming.priority)
        entry.confidence = max(entry.confidence, incoming.confidence)
        if incoming.stability == "stable" or entry.stability not in _ALLOWED_STABILITY:
            entry.stability = incoming.stability
        if incoming.source == "user_direct":
            entry.source = "user_direct"
        if incoming.evidence:
            entry.evidence = incoming.evidence
        if incoming.reason:
            entry.reason = incoming.reason
        if incoming.conflict_key:
            entry.conflict_key = incoming.conflict_key
        if incoming.expires_at:
            entry.expires_at = incoming.expires_at
        entry.status = "active"
        entry.updated_at = now
        entry.last_seen_at = now
        entry.supersedes = sorted(set(entry.supersedes).union(incoming.supersedes))
        return

    groups = _conflict_groups(incoming)
    if groups:
        conflicting_entries = [entry for entry in existing if _conflict_groups(entry) & groups]
        _preserve_meeting_date(incoming, conflicting_entries)
        superseded: list[str] = []
        for entry in conflicting_entries:
            if entry.status == "active":
                entry.status = "superseded"
                entry.updated_at = now
                superseded.append(entry.id)
        incoming.supersedes = sorted(set(incoming.supersedes).union(superseded))

    existing.append(incoming)


def _save_entries(memories_dir: Path, incoming: list[MemoryEntry]) -> None:
    memories_dir.mkdir(parents=True, exist_ok=True)
    by_kind: dict[str, list[MemoryEntry]] = {
        kind: _load_jsonl(memories_dir / filename) for kind, filename in _KIND_TO_JSONL.items()
    }
    for entry in incoming:
        _merge_entry(by_kind[entry.kind], entry)
    for kind, entries in by_kind.items():
        _write_jsonl(memories_dir / _KIND_TO_JSONL[kind], entries)


def _clean_memory_value(value: str) -> str:
    value = value.strip().strip("\"'`“”‘’")
    value = re.split(r"\s*,?\s+(?:not|instead of)\b", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = value.strip().strip("\"'`“”‘’")
    return value[:120].strip()


def _prompt_rejects_memory(prompt: str) -> bool:
    normalized = _normalize_text(prompt)
    return bool(
        re.search(
            r"\b(?:do not|don't|dont)\s+(?:remember|save|store)\b|\b(?:do not|don't|dont)\s+keep\s+(?:this|that)\b",
            normalized,
        )
    )


def _prompt_body(prompt: str) -> str:
    return re.sub(r"^\s*memory\s+test\s*:\s*", "", prompt.strip(), flags=re.IGNORECASE)


def _entry_from_prompt(
    *,
    kind: str,
    text: str,
    priority: int,
    stability: str,
    evidence: str,
    reason: str,
    conflict_key: str = "",
) -> MemoryEntry | None:
    return MemoryEntry.from_dict(
        {
            "kind": kind,
            "text": text,
            "priority": priority,
            "confidence": 1.0,
            "stability": stability,
            "source": "user_direct",
            "evidence": evidence,
            "reason": reason,
            "conflict_key": conflict_key,
        },
        default_source="user_direct",
        default_confidence=1.0,
    )


def _extract_name_memory(prompt: str, body: str) -> MemoryEntry | None:
    patterns = (
        r"\bmy\s+name\s+is\s+([^\n.!?。！？;；,，]{1,80})",
        r"\bcall\s+me\s+([^\n.!?。！？;；,，]{1,80})",
        r"(?:我叫|我的名字是)\s*([^\s，。！？!?,;；]{1,40})",
    )
    for pattern in patterns:
        match = re.search(pattern, body, flags=re.IGNORECASE)
        if not match:
            continue
        value = _clean_memory_value(match.group(1))
        if not value or len(value.split()) > 6:
            continue
        return _entry_from_prompt(
            kind="user",
            text=f"The user's name is {value}.",
            priority=0,
            stability="stable",
            evidence=prompt,
            reason="Runtime fallback extracted an explicit user name statement.",
            conflict_key="user.name",
        )
    return None


def _extract_favorite_memories(prompt: str, body: str) -> list[MemoryEntry]:
    entries: list[MemoryEntry] = []
    patterns = (
        r"\bmy\s+favou?rite\s+([a-z][a-z0-9 _-]{0,40}?)\s+is\s+([^\n.!?。！？;；]{1,120})",
        r"\bmy\s+preferred\s+([a-z][a-z0-9 _-]{0,40}?)\s+is\s+([^\n.!?。！？;；]{1,120})",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, body, flags=re.IGNORECASE):
            slot_text = match.group(1).strip()
            slot = _slot_key(slot_text)
            value = _clean_memory_value(match.group(2))
            if not slot or not value:
                continue
            entry = _entry_from_prompt(
                kind="user",
                text=f"The user's favorite {slot_text.strip().lower()} is {value}.",
                priority=2,
                stability="stable",
                evidence=prompt,
                reason="Runtime fallback extracted an explicit favorite/preferred user fact.",
                conflict_key=f"user.favorite_{slot}",
            )
            if entry is not None:
                entries.append(entry)

    cn_slot_aliases = {
        "颜色": "color",
        "编辑器": "editor",
        "代码编辑器": "editor",
        "语言": "language",
    }
    for match in re.finditer(r"我(?:最喜欢|喜欢)的?([^是，。！？]{1,20})是([^，。！？]{1,80})", body):
        slot_text = match.group(1).strip()
        slot = cn_slot_aliases.get(slot_text, _slot_key(slot_text))
        value = _clean_memory_value(match.group(2))
        if not slot or not value:
            continue
        entry = _entry_from_prompt(
            kind="user",
            text=f"The user's favorite {slot_text} is {value}.",
            priority=2,
            stability="stable",
            evidence=prompt,
            reason="Runtime fallback extracted an explicit favorite user fact.",
            conflict_key=f"user.favorite_{slot}",
        )
        if entry is not None:
            entries.append(entry)
    return entries


def _extract_preference_memory(prompt: str, body: str) -> MemoryEntry | None:
    match = re.search(r"\bi\s+prefer\s+([^\n.!?。！？;；]{1,160})", body, flags=re.IGNORECASE)
    if match:
        preference = _clean_memory_value(match.group(1))
    else:
        cn_match = re.search(r"我(?:喜欢|偏好)\s*([^\n。！？!?;；]{1,160})", body)
        preference = _clean_memory_value(cn_match.group(1)) if cn_match else ""
    if not preference:
        return None
    conflict_key = "preferences.reply_style" if _looks_response_preference(body) else ""
    return _entry_from_prompt(
        kind="preferences",
        text=f"The user prefers {preference}.",
        priority=2,
        stability="evolving",
        evidence=prompt,
        reason="Runtime fallback extracted an explicit user preference.",
        conflict_key=conflict_key,
    )


def _extract_meeting_memory(prompt: str, body: str) -> MemoryEntry | None:
    normalized = _normalize_text(body)
    if "meeting" not in normalized:
        return None
    if "?" in body and not re.search(r"\b(correction|actually|not|instead|now)\b", normalized):
        return None
    time_match = re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?\s*m\.?|p\.?\s*m\.?|am|pm)\b", body, flags=re.IGNORECASE)
    if not time_match:
        return None
    date_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b|\btomorrow\b|\btoday\b|\btonight\b", body, flags=re.IGNORECASE)
    date_text = date_match.group(0).lower() if date_match else ""
    topic = "project meeting" if "project meeting" in normalized else "meeting"
    time_text = re.sub(r"\s+", " ", time_match.group(0).strip())
    text = f"The user has a {topic}"
    if date_text:
        text += f" {date_text}"
    text += f" at {time_text}."
    conflict_key = "auto_short.project_meeting_time" if topic == "project meeting" else "auto_short.meeting_time"
    return _entry_from_prompt(
        kind="auto_short",
        text=text,
        priority=2,
        stability="session",
        evidence=prompt,
        reason="Runtime fallback extracted an explicit meeting time.",
        conflict_key=conflict_key,
    )


def extract_prompt_memories(prompt: str) -> list[MemoryEntry]:
    """Conservative code-side extraction for explicit, high-value user facts."""
    if not isinstance(prompt, str) or _prompt_rejects_memory(prompt):
        return []

    body = _prompt_body(prompt)
    entries: list[MemoryEntry] = []
    if name_entry := _extract_name_memory(prompt, body):
        entries.append(name_entry)
    entries.extend(_extract_favorite_memories(prompt, body))
    if pref_entry := _extract_preference_memory(prompt, body):
        entries.append(pref_entry)
    if meeting_entry := _extract_meeting_memory(prompt, body):
        entries.append(meeting_entry)

    deduped: dict[str, MemoryEntry] = {}
    for entry in entries:
        key = entry.conflict_key or _entry_key(entry)
        deduped[key] = entry
    return list(deduped.values())


def _forget_queries_from_prompt(prompt: str) -> list[str]:
    if not isinstance(prompt, str):
        return []
    body = _prompt_body(prompt)
    normalized = _normalize_text(body)
    if not re.search(r"\b(?:forget|delete|remove|clear)\b|\b(?:do not|don't|dont)\s+(?:remember|save|store)\b", normalized):
        if not re.search(r"(忘记|删除|清除).*(记忆|名字|偏好|喜好|颜色)", body):
            return []

    queries: list[str] = []
    if re.search(r"\bname\b|名字", normalized):
        queries.append("user.name")

    favorite_match = re.search(r"\bfavou?rite\s+([a-z][a-z0-9 _-]{0,40})\b", normalized)
    if favorite_match:
        slot = _slot_key(favorite_match.group(1))
        if slot:
            queries.append(f"user.favorite_{slot}")
    if "颜色" in body:
        queries.append("user.favorite_color")
    if "编辑器" in body:
        queries.append("user.favorite_editor")

    if re.search(r"\b(reply|response|answer|conversation|tone|style)\b", normalized):
        queries.append("preferences.reply_style")
    if re.search(r"\blanguage\b", normalized):
        queries.append("preferences.language")

    if "meeting" in normalized:
        queries.append("auto_short.project_meeting_time" if "project meeting" in normalized else "auto_short.meeting_time")

    if not queries:
        remainder = re.sub(
            r"^\s*(?:please\s+)?(?:forget|delete|remove|clear|do not remember|don't remember|dont remember|do not save|don't save|dont save)\s+",
            "",
            body,
            flags=re.IGNORECASE,
        ).strip()
        if remainder:
            queries.append(remainder)
    return list(dict.fromkeys(queries))


def _entry_matches_query(entry: MemoryEntry, query: str, *, kind: str | None = None) -> bool:
    normalized_kind = _normalize_kind(kind) if kind else ""
    if normalized_kind and entry.kind != normalized_kind:
        return False
    conflict_key = _normalize_conflict_key(query)
    query_text = _normalize_text(query)
    query_tokens = _tokens(query_text)
    text = _normalize_text(entry.text)
    text_tokens = _tokens(entry.text)
    matches_conflict_key = bool(conflict_key and conflict_key in _conflict_groups(entry))
    matches_text = bool(query_text and query_text in text)
    matches_tokens = bool(query_tokens and query_tokens.issubset(text_tokens))
    return matches_conflict_key or matches_text or matches_tokens


def forget_memories(memories_dir: Path, query: str, *, kind: str | None = None) -> int:
    query = query.strip() if isinstance(query, str) else ""
    if not query:
        return 0
    now = _utc_now()
    forgotten = 0

    with _memory_write_lock(memories_dir):
        by_kind: dict[str, list[MemoryEntry]] = {
            memory_kind: _load_jsonl(memories_dir / filename) for memory_kind, filename in _KIND_TO_JSONL.items()
        }
        for memory_kind, entries in by_kind.items():
            if kind and _normalize_kind(kind) != memory_kind:
                continue
            for entry in entries:
                if entry.status != "active":
                    continue
                if _entry_matches_query(entry, query, kind=kind):
                    entry.status = "superseded"
                    entry.updated_at = now
                    forgotten += 1
        if forgotten:
            for memory_kind, entries in by_kind.items():
                _write_jsonl(memories_dir / _KIND_TO_JSONL[memory_kind], entries)
    return forgotten


def delete_memories(memories_dir: Path, query: str, *, kind: str | None = None) -> int:
    """Permanently remove matching structured memory rows from JSONL files."""
    query = query.strip() if isinstance(query, str) else ""
    if not query:
        return 0
    deleted = 0
    normalized_kind = _normalize_kind(kind) if kind else ""

    with _memory_write_lock(memories_dir):
        for memory_kind, filename in _KIND_TO_JSONL.items():
            if normalized_kind and normalized_kind != memory_kind:
                continue
            path = memories_dir / filename
            if not path.exists():
                continue
            original_lines = path.read_text(encoding="utf-8").splitlines()
            kept_lines: list[str] = []
            changed = False
            for line in original_lines:
                if not line.strip():
                    kept_lines.append(line)
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    kept_lines.append(line)
                    continue
                entry = MemoryEntry.from_dict(raw) if isinstance(raw, dict) else None
                if entry is not None and _entry_matches_query(entry, query, kind=kind):
                    deleted += 1
                    changed = True
                    continue
                kept_lines.append(line)
            if changed:
                _atomic_write(path, "\n".join(kept_lines) + ("\n" if kept_lines else ""))
    return deleted


def apply_memory_forget(memories_dir: Path, payload: dict[str, Any], *, session_id: str | None = None) -> int:
    if not isinstance(payload, dict):
        return 0
    raw_items = payload.get("forget") or payload.get("queries") or payload.get("items")
    items: list[object]
    if isinstance(raw_items, list):
        items = raw_items
    else:
        items = [payload]

    forgotten = 0
    for item in items:
        if isinstance(item, str):
            forgotten += forget_memories(memories_dir, item)
            continue
        if not isinstance(item, dict):
            continue
        query = _payload_text(item.get("query") or item.get("conflict_key") or item.get("text"))
        if not query:
            continue
        kind = _payload_text(item.get("kind"))
        forgotten += forget_memories(memories_dir, query, kind=kind or None)
    return forgotten


def forget_prompt_memories(memories_dir: Path, prompt: str, *, session_id: str | None = None) -> int:
    forgotten = 0
    for query in _forget_queries_from_prompt(prompt):
        forgotten += forget_memories(memories_dir, query)
    return forgotten


def _iter_save_entries(
    payload: dict[str, Any],
    *,
    default_source: str = "model_inferred",
    default_priority: int = _DEFAULT_PRIORITY,
    default_confidence: float = 0.6,
) -> list[MemoryEntry]:
    raw_memories = payload.get("memories")
    if isinstance(raw_memories, list):
        candidates = [m for m in raw_memories if isinstance(m, dict)]
    elif payload.get("kind") or payload.get("target") or payload.get("text") or payload.get("content"):
        candidates = [payload]
    else:
        candidates = []

    entries: list[MemoryEntry] = []
    for item in candidates:
        entry = MemoryEntry.from_dict(
            item,
            default_priority=default_priority,
            default_confidence=default_confidence,
            default_source=default_source,
        )
        if entry is not None:
            entries.append(entry)
    return entries


def _iter_legacy_append_entries(payload: dict[str, Any]) -> list[MemoryEntry]:
    now = _utc_now()
    specs = (
        ("user", "user", 5, 0.6, "evolving"),
        ("preferences", "preferences", 5, 0.6, "evolving"),
        ("notes", "preferences", 5, 0.6, "evolving"),
        ("auto_short", "auto_short", 5, 0.6, "session"),
    )
    entries: list[MemoryEntry] = []
    for key, kind, priority, confidence, stability in specs:
        text = _payload_text(payload.get(key))
        if not text:
            continue
        entries.append(
            MemoryEntry(
                id=f"mem_{uuid.uuid4().hex}",
                kind=kind,
                text=text,
                priority=priority,
                confidence=confidence,
                stability=stability,
                source="model_inferred",
                reason="Legacy memory_append payload",
                status="active",
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
        )
    return entries


def _iter_proposals(payload: dict[str, Any]) -> list[dict[str, Any]]:
    proposals = payload.get("proposals")
    if isinstance(proposals, list):
        return [p for p in proposals if isinstance(p, dict)]
    if payload.get("target") or payload.get("content"):
        return [payload]
    legacy: list[dict[str, Any]] = []
    for key, target in (("user", "user"), ("preferences", "preferences"), ("notes", "preferences")):
        if content := _payload_text(payload.get(key)):
            legacy.append({"target": target, "content": content})
    return legacy


def _iter_legacy_proposal_entries(payload: dict[str, Any]) -> list[MemoryEntry]:
    entries: list[MemoryEntry] = []
    for proposal in _iter_proposals(payload):
        entry = MemoryEntry.from_dict(
            proposal,
            default_priority=5,
            default_confidence=0.6,
            default_source="model_inferred",
        )
        if entry is None or entry.kind not in {"user", "preferences"}:
            continue
        if not entry.reason:
            entry.reason = "Legacy memory_proposal payload"
        entries.append(entry)
    return entries


def _entry_rank(entry: MemoryEntry, prompt_tokens: set[str]) -> tuple[int, int, float, float]:
    relevance = len(_tokens(entry.text) & prompt_tokens) if prompt_tokens else 0
    recency = _parse_timestamp(entry.last_seen_at or entry.updated_at or entry.created_at)
    return (entry.priority, -relevance, -entry.confidence, -recency)


def _render_memory_entries(entries: list[MemoryEntry]) -> list[str]:
    groups: list[tuple[str, list[MemoryEntry]]] = [
        ("## Important User Memory", [e for e in entries if e.kind == "user"]),
        (
            "## Preferences",
            [e for e in entries if e.kind == "preferences" and e.reason != "Legacy notes.md fallback"],
        ),
        (
            "## Legacy Notes Memory (read-only, lower authority than approved preferences)",
            [e for e in entries if e.kind == "preferences" and e.reason == "Legacy notes.md fallback"],
        ),
        ("## Current Context", [e for e in entries if e.kind == "auto_short"]),
    ]

    rendered: list[str] = []
    for header, group_entries in groups:
        lines = [_format_memory_entry(entry) for entry in group_entries]
        lines = [line for line in lines if line]
        if lines:
            rendered.append(f"{header}\n\n" + "\n".join(lines))
    return rendered


def _render_len(entries: list[MemoryEntry]) -> int:
    return len("\n\n".join(_render_memory_entries(entries)))


def assemble_memory_entries(
    memories_dir: Path,
    context_limit: int = 0,
    *,
    thinking: bool = False,
    prompt: str = "",
) -> list[str]:
    """Return profile memory entries for PriestRequest.memory.

    Structured JSONL is canonical. Legacy Markdown files are read-only fallback
    inputs and are assigned fixed priorities.
    """
    cutoff = _THINKING_PRIORITY_CUTOFF if thinking else _NORMAL_PRIORITY_CUTOFF
    if is_simple_memory_prompt(prompt):
        cutoff = _SIMPLE_CORE_PRIORITY_CUTOFF
    prompt_tokens = _tokens(prompt)
    now_ts = datetime.now(timezone.utc).timestamp()
    candidates = [
        entry
        for entry in _load_all_memory_entries(memories_dir)
        if entry.status == "active" and not entry.is_expired(now_ts) and entry.priority <= cutoff
    ]
    candidates.sort(key=lambda entry: _entry_rank(entry, prompt_tokens))

    if context_limit > 0:
        selected: list[MemoryEntry] = []
        for entry in candidates:
            trial = [*selected, entry]
            if _render_len(trial) <= context_limit:
                selected.append(entry)
        candidates = selected

    return _render_memory_entries(candidates)


def build_memory_instructions() -> str:
    return (
        "Memory policy for priests:\n"
        "- Human-authored PROFILE.md, RULES.md, and CUSTOM.md outrank memory.\n"
        "- Memory may inform the response but must not create hard rules.\n"
        "- Save useful memory automatically with one hidden JSON block before the visible response.\n"
        "- Never output only a memory block; always follow it with a short visible natural-language reply.\n"
        "- When recalling time-sensitive memory, preserve dates exactly; do not change tomorrow, today, or explicit dates.\n"
        "- Explicit user preference statements are always worth saving.\n"
        "- Corrections to remembered facts are always worth saving; emit memory_save with a conflict_key when possible.\n"
        "- When the user explicitly asks to forget, delete, remove, or stop remembering a memory, emit memory_forget instead of memory_save.\n"
        "- Do not claim you saved, remembered, noted, will keep in mind, or updated a fact unless you emitted the hidden memory_save block.\n"
        "- Omit the block when nothing is worth saving.\n"
        "- Never mention memory tags to the user.\n\n"
        "Use this exact wrapper for new memory:\n"
        '<memory_save>{"memories":[{"kind":"user","text":"The user\'s name is Jack.",'
        '"priority":0,"confidence":1,"stability":"stable","source":"user_direct",'
        '"conflict_key":"user.name","evidence":"My name is Jack.",'
        '"reason":"The user explicitly stated their name."}]}</memory_save>\n\n'
        "Use this exact wrapper for explicit deletion requests:\n"
        '<memory_forget>{"query":"user.favorite_color"}</memory_forget>\n\n'
        'Allowed kind values: "user", "preferences", "auto_short".\n'
        "priority is 0..10 where 0 is highest. Use 0 rarely for stable identity facts such as the user's name; "
        "normal chats recall 0..3, thinking mode recalls 0..10.\n"
        "Use priority 1-2 for explicit durable user facts, priority 2 for explicit preferences, "
        "priority 2-3 for time-sensitive facts the user may ask about soon, and 5+ for low-value background.\n"
        "confidence is 0..1. Use source=user_direct only for explicit user statements; otherwise use model_inferred.\n"
        'Allowed stability values: "stable", "evolving", "session", "ephemeral".\n'
        "Optional conflict_key supports updates without a fixed user schema. Use a concise lowercase dot key "
        "when a new memory should replace older active memories about the same slot, e.g. user.name, "
        "user.favorite_color, user.favorite_editor, preferences.reply_style, preferences.language, "
        "auto_short.project_meeting_time. Omit conflict_key for memories that can coexist.\n"
        "Use auto_short for temporary tasks, reminders, and current-session context. "
        "Use preferences for how the user likes responses or tools to behave."
    )


def save_memories(memories_dir: Path, payload: dict[str, Any], *, session_id: str | None = None) -> None:
    if not isinstance(payload, dict):
        return
    entries = _iter_save_entries(payload)
    if not entries:
        return
    with _memory_write_lock(memories_dir):
        _save_entries(memories_dir, entries)


def save_prompt_memories(memories_dir: Path, prompt: str, *, session_id: str | None = None) -> int:
    entries = extract_prompt_memories(prompt)
    if not entries:
        return 0
    with _memory_write_lock(memories_dir):
        _save_entries(memories_dir, entries)
    return len(entries)


def append_memories(memories_dir: Path, payload: dict[str, Any], *, session_id: str | None = None) -> None:
    """Apply legacy model memory_append payloads to canonical JSONL memory."""
    if not isinstance(payload, dict):
        return
    entries = _iter_legacy_append_entries(payload)
    if not entries:
        return
    with _memory_write_lock(memories_dir):
        _save_entries(memories_dir, entries)


def apply_memory_proposals(memories_dir: Path, payload: dict[str, Any], *, session_id: str | None = None) -> None:
    """Apply legacy memory_proposal payloads to canonical JSONL memory."""
    if not isinstance(payload, dict):
        return
    entries = _iter_legacy_proposal_entries(payload)
    if not entries:
        return
    with _memory_write_lock(memories_dir):
        _save_entries(memories_dir, entries)


def remember_short(memories_dir: Path, content: str) -> None:
    now = _utc_now()
    entry = MemoryEntry(
        id=f"mem_{uuid.uuid4().hex}",
        kind="auto_short",
        text=content,
        priority=3,
        confidence=1.0,
        stability="session",
        source="user_direct",
        reason="Manual /remember command",
        created_at=now,
        updated_at=now,
        last_seen_at=now,
    )
    with _memory_write_lock(memories_dir):
        _save_entries(memories_dir, [entry])


def remember_user(memories_dir: Path, content: str) -> None:
    now = _utc_now()
    entry = MemoryEntry(
        id=f"mem_{uuid.uuid4().hex}",
        kind="user",
        text=content,
        priority=1,
        confidence=1.0,
        stability="stable",
        source="user_direct",
        reason="Manual /remember user command",
        created_at=now,
        updated_at=now,
        last_seen_at=now,
    )
    with _memory_write_lock(memories_dir):
        _save_entries(memories_dir, [entry])


def remember_preference(memories_dir: Path, content: str) -> None:
    now = _utc_now()
    entry = MemoryEntry(
        id=f"mem_{uuid.uuid4().hex}",
        kind="preferences",
        text=content,
        priority=2,
        confidence=1.0,
        stability="stable",
        source="user_direct",
        reason="Manual /remember pref command",
        created_at=now,
        updated_at=now,
        last_seen_at=now,
    )
    with _memory_write_lock(memories_dir):
        _save_entries(memories_dir, [entry])


def apply_consolidation(memories_dir: Path, payload: dict[str, Any]) -> None:
    """Compatibility shim: convert old auto_short consolidation to JSONL entries."""
    if not isinstance(payload, dict):
        return
    content = _payload_text(payload.get("auto_short"))
    if not content:
        return
    append_memories(memories_dir, {"auto_short": content})


def _serialized_jsonl_len(entries: list[MemoryEntry]) -> int:
    return sum(len(json.dumps(entry.to_json(), ensure_ascii=False, sort_keys=True)) + 1 for entry in entries)


def _trim_structured_auto_short(path: Path, size_limit: int) -> None:
    entries = _load_jsonl(path)
    if not entries or _serialized_jsonl_len(entries) <= size_limit:
        return
    now_ts = datetime.now(timezone.utc).timestamp()
    keep = [entry for entry in entries if not entry.is_expired(now_ts)]
    if not keep:
        keep = [min(entries, key=lambda entry: entry.priority)]

    def trim_rank(entry: MemoryEntry) -> tuple[int, float, float, int]:
        priority_guard = 0 if entry.priority == 0 else 1
        return (
            priority_guard,
            entry.priority,
            entry.confidence,
            _parse_timestamp(entry.last_seen_at or entry.updated_at or entry.created_at),
        )

    while len(keep) > 1 and _serialized_jsonl_len(keep) > size_limit:
        removable = [entry for entry in keep if entry.priority != 0]
        if not removable:
            break
        victim = max(removable, key=trim_rank)
        keep.remove(victim)

    if _serialized_jsonl_len(keep) > size_limit:
        keep = [entry for entry in keep if entry.priority == 0] or keep[-1:]

    _write_jsonl(path, keep)


def _truncate_auto_short(content: str, max_chars: int) -> str:
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    sections = re.split(r"(?=(?:^|\n)## \d{4}-\d{2}-\d{2})", content)
    if len(sections) <= 1:
        return content
    intro, dated = sections[0], list(sections[1:])
    if len(dated) <= 1:
        return content
    while len(dated) > 1 and len(intro + "".join(dated)) > max_chars:
        dated.pop(0)
    result = intro + "".join(dated)
    return result if len(result) <= max_chars else result[-max_chars:]


def trim_memories(memories_dir: Path, size_limit: int) -> None:
    if size_limit <= 0:
        return
    structured_path = memories_dir / AUTO_JSONL_FILE
    if structured_path.exists():
        with _memory_write_lock(memories_dir):
            _trim_structured_auto_short(structured_path, size_limit)

    legacy_path = memories_dir / AUTO_FILE
    if not legacy_path.exists():
        return
    with _memory_write_lock(memories_dir):
        text = _read_file(legacy_path)
        if len(text) <= size_limit:
            return
        trimmed = _truncate_auto_short(text, size_limit)
        if trimmed != text:
            _atomic_write(legacy_path, trimmed)


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
    stripper = StreamingStripper()
    return stripper.feed(text) + stripper.flush()


async def clean_last_turn(store, session_id: str) -> None:
    session = await store.get(session_id)
    if not session or not session.turns:
        return
    last = session.turns[-1]
    if last.role == "assistant":
        cleaned = _strip_memory_blocks(last.content)
    else:
        cleaned = last.content
    if cleaned != last.content:
        session.turns[-1] = dataclasses.replace(last, content=cleaned)
        await store.save(session)


__all__ = [
    "MemoryEntry",
    "StreamingStripper",
    "USER_FILE",
    "PREFERENCES_FILE",
    "NOTES_FILE",
    "AUTO_FILE",
    "USER_JSONL_FILE",
    "PREFERENCES_JSONL_FILE",
    "AUTO_JSONL_FILE",
    "assemble_memory_entries",
    "build_memory_instructions",
    "extract_prompt_memories",
    "save_memories",
    "save_prompt_memories",
    "append_memories",
    "apply_memory_proposals",
    "apply_memory_forget",
    "forget_memories",
    "delete_memories",
    "forget_prompt_memories",
    "apply_consolidation",
    "trim_memories",
    "needs_consolidation",
    "mark_consolidated",
    "deduplicate_file",
    "remember_short",
    "remember_user",
    "remember_preference",
    "clean_last_turn",
    "is_simple_memory_prompt",
    "should_inject_memory_instructions",
    "pop_last_exchange",
]
