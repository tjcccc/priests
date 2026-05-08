"""Microbenchmarks for priests/memory/extractor.py.

Run with:
    uv run pytest tests/bench_memory.py --benchmark-only -v
"""

import json

from priests.memory.extractor import (
    StreamingStripper,
    append_memories,
    apply_memory_proposals,
    needs_consolidation,
    trim_memories,
)

# ---------------------------------------------------------------------------
# Shared inputs
# ---------------------------------------------------------------------------

_APPEND_PAYLOAD = json.dumps(
    {
        "user": "Prefers dark mode. Uses Neovim.",
        "preferences": "Prefers concise technical explanations.",
        "auto_short": "Discussed benchmark tooling for extractor.py.",
    }
)

# Typical memory-save turn: block at front, prose after
_STREAM_WITH_BLOCK = (
    f"<memory_append>{_APPEND_PAYLOAD}</memory_append>"
    "Here is the answer to your question about benchmarking. "
    "The key insight is that pytest-benchmark handles warmup automatically. " * 50
)

# Most common case: no memory tags at all
_STREAM_NO_TAGS = (
    "Here is a detailed response about your code. "
    "pytest-benchmark measures min, mean, max, and stddev per function. " * 100
)

# A proposal turn: model suggests durable memory for later approval
_PROPOSAL_PAYLOAD = json.dumps(
    {
        "user": "Uses Neovim on macOS.",
        "preferences": "Prefers concise technical explanations.",
    }
)
_STREAM_WITH_PROPOSAL = (
    f"<memory_proposal>{_PROPOSAL_PAYLOAD}</memory_proposal>"
    "I have prepared durable memory proposals for review. " * 20
)


def _large_auto_short(days: int = 30) -> str:
    """Build a realistic auto_short.md with `days` dated sections."""
    lines = ["# Short Memories\n"]
    for day in range(1, days + 1):
        lines.append(
            f"\n## 2026-03-{day:02d}\n\n"
            f"Discussed topic {day} in detail. Made progress on task {day}.\n"
        )
    return "".join(lines)


# ---------------------------------------------------------------------------
# StreamingStripper — no tags (hot path, runs every turn)
# ---------------------------------------------------------------------------


def test_bench_stripper_no_tags_small_chunks(benchmark):
    """Pure prose, 32-byte chunks — most common case, tightest loop."""

    def run():
        s = StreamingStripper()
        for i in range(0, len(_STREAM_NO_TAGS), 32):
            s.feed(_STREAM_NO_TAGS[i : i + 32])
        s.flush()

    benchmark(run)


def test_bench_stripper_no_tags_large_chunks(benchmark):
    """Pure prose, 256-byte chunks — some providers stream larger."""

    def run():
        s = StreamingStripper()
        for i in range(0, len(_STREAM_NO_TAGS), 256):
            s.feed(_STREAM_NO_TAGS[i : i + 256])
        s.flush()

    benchmark(run)


# ---------------------------------------------------------------------------
# StreamingStripper — with memory blocks
# ---------------------------------------------------------------------------


def test_bench_stripper_append_block(benchmark):
    """<memory_append> block at front, 32-byte chunks."""

    def run():
        s = StreamingStripper()
        for i in range(0, len(_STREAM_WITH_BLOCK), 32):
            s.feed(_STREAM_WITH_BLOCK[i : i + 32])
        s.flush()

    benchmark(run)


def test_bench_stripper_proposal_block(benchmark):
    """<memory_proposal> block at front, 32-byte chunks."""

    def run():
        s = StreamingStripper()
        for i in range(0, len(_STREAM_WITH_PROPOSAL), 32):
            s.feed(_STREAM_WITH_PROPOSAL[i : i + 32])
        s.flush()

    benchmark(run)


def test_bench_stripper_block_split_across_chunks(benchmark):
    """Tag boundary lands on a chunk edge — worst-case for the state machine."""

    def run():
        s = StreamingStripper()
        for ch in _STREAM_WITH_BLOCK:
            s.feed(ch)
        s.flush()

    benchmark(run)


# ---------------------------------------------------------------------------
# needs_consolidation — compatibility no-op
# ---------------------------------------------------------------------------


def test_bench_needs_consolidation_disabled(benchmark, tmp_path):
    """Durable consolidation is disabled in the current chat memory policy."""
    benchmark(needs_consolidation, tmp_path)


# ---------------------------------------------------------------------------
# append_memories — file I/O
# ---------------------------------------------------------------------------


def test_bench_append_memories_auto_and_pending(benchmark, tmp_path):
    """Append short-term memory and route durable fields to pending proposals."""
    payload = {
        "user": "Prefers dark mode.",
        "preferences": "Prefers concise answers.",
        "auto_short": "Discussed benchmarking.",
    }
    benchmark(append_memories, tmp_path, payload)


def test_bench_append_memories_auto_short_only(benchmark, tmp_path):
    """Append only auto_short — most frequent write (every turn that saves)."""
    payload = {"auto_short": "Resolved import error in engine_factory.py."}
    benchmark(append_memories, tmp_path, payload)


def test_bench_apply_memory_proposals(benchmark, tmp_path):
    """Write one pending proposal Markdown file per durable memory target."""
    proposals = {
        "user": "Uses Neovim on macOS.",
        "preferences": "Prefers concise technical explanations.",
    }
    benchmark(apply_memory_proposals, tmp_path, proposals)


# ---------------------------------------------------------------------------
# trim_memories — scales with file size
# ---------------------------------------------------------------------------


def test_bench_trim_memories_small_file(benchmark, tmp_path):
    """5 dated sections — typical early use."""
    content = _large_auto_short(days=5)
    limit = len(content) // 2
    auto_short = tmp_path / "auto_short.md"

    def run():
        auto_short.write_text(content)
        trim_memories(tmp_path, limit)

    benchmark(run)


def test_bench_trim_memories_large_file(benchmark, tmp_path):
    """30 dated sections — ~1 month of daily use."""
    content = _large_auto_short(days=30)
    limit = len(content) // 2
    auto_short = tmp_path / "auto_short.md"

    def run():
        auto_short.write_text(content)
        trim_memories(tmp_path, limit)

    benchmark(run)


def test_bench_trim_memories_already_under_limit(benchmark, tmp_path):
    """File already under limit — should return early with no writes."""
    content = _large_auto_short(days=5)
    auto_short = tmp_path / "auto_short.md"
    auto_short.write_text(content)
    limit = len(content) * 10  # well over size

    benchmark(trim_memories, tmp_path, limit)
