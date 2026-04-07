"""Microbenchmarks for priests/memory/extractor.py.

Run with:
    uv run pytest tests/bench_memory.py --benchmark-only -v
"""

import json
import time

from priests.memory.extractor import (
    StreamingStripper,
    append_memories,
    needs_consolidation,
    trim_memories,
)

# ---------------------------------------------------------------------------
# Shared inputs
# ---------------------------------------------------------------------------

_APPEND_PAYLOAD = json.dumps(
    {
        "user": "Prefers dark mode. Uses Neovim.",
        "notes": "Working on priests v0.2 memory system.",
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

# A consolidation turn: model rewrites all memory files
_CONSOLIDATION_PAYLOAD = json.dumps(
    {
        "user": "Prefers dark mode. Uses Neovim. Runs macOS.",
        "notes": "Actively developing priests v0.2 memory and benchmark infrastructure.",
        "auto_short": "## 2026-04-07\n\nBenchmarked StreamingStripper and file I/O.",
    }
)
_STREAM_WITH_CONSOLIDATION = (
    f"<memory_consolidation>{_CONSOLIDATION_PAYLOAD}</memory_consolidation>"
    "I have updated your memory files with the latest context. " * 20
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


def test_bench_stripper_consolidation_block(benchmark):
    """<memory_consolidation> block at front, 32-byte chunks."""

    def run():
        s = StreamingStripper()
        for i in range(0, len(_STREAM_WITH_CONSOLIDATION), 32):
            s.feed(_STREAM_WITH_CONSOLIDATION[i : i + 32])
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
# needs_consolidation — called every turn
# ---------------------------------------------------------------------------


def test_bench_needs_consolidation_no_sentinel(benchmark, tmp_path):
    """Sentinel absent — returns True immediately (fast path)."""
    benchmark(needs_consolidation, tmp_path)


def test_bench_needs_consolidation_up_to_date(benchmark, tmp_path):
    """All files older than sentinel — steady-state check cost."""
    (tmp_path / "user.md").write_text("Prefers Neovim.")
    (tmp_path / "notes.md").write_text("Working on v0.2.")
    (tmp_path / "auto_short.md").write_text("# Short Memories\n")
    time.sleep(0.02)
    (tmp_path / ".last_consolidated").touch()

    benchmark(needs_consolidation, tmp_path)


def test_bench_needs_consolidation_stale(benchmark, tmp_path):
    """One file newer than sentinel — returns True after full stat scan."""
    (tmp_path / ".last_consolidated").touch()
    time.sleep(0.02)
    (tmp_path / "user.md").write_text("Updated fact.")
    (tmp_path / "notes.md").write_text("Notes.")
    (tmp_path / "auto_short.md").write_text("# Short Memories\n")

    benchmark(needs_consolidation, tmp_path)


# ---------------------------------------------------------------------------
# append_memories — file I/O
# ---------------------------------------------------------------------------


def test_bench_append_memories_all_fields(benchmark, tmp_path):
    """Append to all three memory files."""
    payload = {
        "user": "Prefers dark mode.",
        "notes": "v0.2 work ongoing.",
        "auto_short": "Discussed benchmarking.",
    }
    benchmark(append_memories, tmp_path, payload)


def test_bench_append_memories_auto_short_only(benchmark, tmp_path):
    """Append only auto_short — most frequent write (every turn that saves)."""
    payload = {"auto_short": "Resolved import error in engine_factory.py."}
    benchmark(append_memories, tmp_path, payload)


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
