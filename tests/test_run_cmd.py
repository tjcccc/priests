"""Tests for priests/cli/run_cmd.py helpers and priests/search.py."""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# _build_memory_context compatibility wrapper
# ---------------------------------------------------------------------------


def test_build_memory_context_returns_write_policy_not_memory_content(tmp_path):
    from priests.cli.run_cmd import _build_memory_context

    (tmp_path / "user.md").write_text("user fact")
    (tmp_path / "preferences.md").write_text("pref fact")
    (tmp_path / "auto_short.md").write_text("# Short Memories\n\n## 2026-01-01\n\nauto fact\n")

    result = _build_memory_context(tmp_path, 50000, 0, False)

    assert "Memory policy for priests" in result
    assert "memory_append" in result
    assert "memory_proposal" in result
    assert "user fact" not in result
    assert "auto fact" not in result


def test_assemble_memory_entries_holds_loaded_memory(tmp_path):
    from priests.memory.extractor import assemble_memory_entries

    (tmp_path / "user.md").write_text("user fact")
    (tmp_path / "preferences.md").write_text("pref fact")
    (tmp_path / "notes.md").write_text("legacy fact")
    (tmp_path / "auto_short.md").write_text("# Short Memories\n\n## 2026-01-01\n\nauto fact\n")

    entries = assemble_memory_entries(tmp_path)

    assert "user fact" in entries[0]
    assert "pref fact" in entries[1]
    assert "legacy fact" in entries[2]
    assert "auto fact" in entries[3]


def test_assemble_memory_entries_context_limit(tmp_path):
    from priests.memory.extractor import assemble_memory_entries

    (tmp_path / "user.md").write_text("u" * 50)
    (tmp_path / "preferences.md").write_text("p" * 50)
    auto_lines = ["# Short Memories\n"]
    for day in range(1, 6):
        auto_lines.append(f"\n## 2026-01-{day:02d}\n\n{'x' * 200}\n")
    (tmp_path / "auto_short.md").write_text("".join(auto_lines))

    context_limit = 50 + 50 + 250

    result = "\n".join(assemble_memory_entries(tmp_path, context_limit))

    assert "u" * 50 in result
    assert "p" * 50 in result
    sections = re.findall(r"## 2026-01-\d+", result)
    assert len(sections) < 5


def test_build_memory_context_non_consolidation_no_consolidation_block(tmp_path):
    """Non-consolidation output must not contain <memory_consolidation> instructions."""
    from priests.cli.run_cmd import _build_memory_context

    (tmp_path / "user.md").write_text("user fact")

    result = _build_memory_context(tmp_path, 50000, 0, False)

    assert "<memory_consolidation>" not in result
    assert "memory_consolidation" not in result


# ---------------------------------------------------------------------------
# priests/search.py
# ---------------------------------------------------------------------------


def _make_mock_ddgs(results: list) -> tuple[MagicMock, MagicMock]:
    """Return a DDGS context manager mock that yields the given text results."""
    instance = MagicMock()
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)
    instance.text = MagicMock(return_value=results)
    cls = MagicMock(return_value=instance)
    return cls, instance


def test_search_formats_results():
    """search() formats DDGS results into a readable text block."""
    from priests.search import search

    fake_results = [
        {"title": "Result One", "href": "https://example.com/1", "body": "Snippet one."},
        {"title": "Result Two", "href": "https://example.com/2", "body": "Snippet two."},
    ]
    cls, instance = _make_mock_ddgs(fake_results)

    # Patch at the source module so the lazy `from ddgs import DDGS` is intercepted.
    with patch("ddgs.DDGS", cls):
        result = search("test query", max_results=2)

    assert "test query" in result
    assert "Result One" in result
    assert "https://example.com/1" in result
    assert "Snippet one." in result
    assert "Result Two" in result
    instance.text.assert_called_once_with("test query", max_results=2)


def test_search_empty_results():
    """search() returns a 'no results' message when DDGS returns nothing."""
    from priests.search import search

    cls, _ = _make_mock_ddgs([])

    with patch("ddgs.DDGS", cls):
        result = search("noresults")

    assert "no results" in result.lower()
    assert "noresults" in result


def test_search_missing_extra_raises_runtime_error():
    """search() raises RuntimeError with install hint when ddgs is not installed."""
    import sys
    import importlib

    # Temporarily hide the ddgs module
    original = sys.modules.get("ddgs")
    sys.modules["ddgs"] = None  # type: ignore[assignment]
    try:
        # Reload search so the lazy import re-runs
        import priests.search as search_mod
        importlib.reload(search_mod)
        try:
            search_mod.search("anything")
            assert False, "Expected RuntimeError"
        except RuntimeError as exc:
            assert "ddgs" in str(exc)
    finally:
        if original is None:
            sys.modules.pop("ddgs", None)
        else:
            sys.modules["ddgs"] = original
        importlib.reload(search_mod)


# ---------------------------------------------------------------------------
# Profile model resolution
# ---------------------------------------------------------------------------


def test_resolve_provider_model_uses_profile_pair(tmp_path):
    from priests.config.model import AppConfig
    from priests.profile.config import resolve_provider_model

    profile_dir = tmp_path / "profiles" / "coder"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.toml").write_text('provider = "bailian"\nmodel = "qwen-plus"\n')
    config = AppConfig.model_validate({
        "default": {"provider": "ollama", "model": "llama3"},
        "paths": {"profiles_dir": str(tmp_path / "profiles")},
    })

    assert resolve_provider_model(config, "coder") == ("bailian", "qwen-plus")


def test_resolve_provider_model_falls_back_to_default_when_profile_unset(tmp_path):
    from priests.config.model import AppConfig
    from priests.profile.config import resolve_provider_model

    profile_dir = tmp_path / "profiles" / "plain"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.toml").write_text("memories = true\n")
    config = AppConfig.model_validate({
        "default": {"provider": "ollama", "model": "llama3"},
        "paths": {"profiles_dir": str(tmp_path / "profiles")},
    })

    assert resolve_provider_model(config, "plain") == ("ollama", "llama3")


def test_resolve_provider_model_explicit_args_win_over_profile_pair(tmp_path):
    from priests.config.model import AppConfig
    from priests.profile.config import resolve_provider_model

    profile_dir = tmp_path / "profiles" / "coder"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.toml").write_text('provider = "bailian"\nmodel = "qwen-plus"\n')
    config = AppConfig.model_validate({
        "default": {"provider": "ollama", "model": "llama3"},
        "paths": {"profiles_dir": str(tmp_path / "profiles")},
    })

    assert resolve_provider_model(config, "coder", "openai", "gpt-4.1") == ("openai", "gpt-4.1")
