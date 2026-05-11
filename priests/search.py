"""Web search helper for priests CLI.

Optional feature — requires the `search` extra:
    pip install "priests[search]"
    uv pip install "priests[search]"
"""

from __future__ import annotations

import re


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SEARCH_INTENT_RE = re.compile(
    r"("
    r"\b(search|look up|lookup|find|latest|current|today|weather|news)\b"
    r"|查|查询|搜|搜索|找一下|帮我找|帮我查|今天|今日|现在|当前|实时|天气|新闻|最新"
    r")",
    re.IGNORECASE,
)
_SEARCH_WAIT_RE = re.compile(
    r"^\s*[（(]?\s*("
    r"正在.*?(查|找|搜)|"
    r"请稍等|稍等|马上.*?(查|找|搜)|"
    r"我.*?(查|找|搜).*?(一下|看看|稍等)|"
    r"searching|looking up|let me (search|look)|please wait|one moment"
    r").{0,80}[）)]?\s*$",
    re.IGNORECASE,
)


def format_search_context(search_results: str) -> str:
    """Wrap raw search results with turn-local instructions for the model."""
    return (
        "## Web search results\n\n"
        "Use the following web search results to answer the user's current question. "
        "Do not emit another <search_query> request for this turn. "
        "If the results are insufficient or irrelevant, say what could not be confirmed.\n\n"
        f"{search_results}"
    )


def looks_like_search_intent(prompt: str) -> bool:
    """Return true when a prompt appears to ask for current/search-backed info."""
    return bool(_SEARCH_INTENT_RE.search(prompt))


def looks_like_search_wait_response(text: str) -> bool:
    """Return true for short filler responses that pretend a search is happening."""
    visible = text.strip()
    if not visible or len(visible) > 120:
        return False
    if "\n" in visible or "http://" in visible or "https://" in visible:
        return False
    return bool(_SEARCH_WAIT_RE.search(visible))


def should_fallback_to_search(prompt: str, response_text: str) -> bool:
    """Detect a model that narrated search instead of emitting <search_query>."""
    return looks_like_search_intent(prompt) and looks_like_search_wait_response(response_text)


def search(query: str, max_results: int = 5) -> str:
    """Run a DuckDuckGo web search and return a formatted text block.

    Returns a plain-text block suitable for injection into ``user_context``.
    Raises ``RuntimeError`` with a helpful install message if the optional
    dependency is not installed.
    """
    try:
        from ddgs import DDGS  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "Web search requires ddgs. "
            "Install it with: uv pip install ddgs"
        ) from exc

    region = "cn-zh" if _CJK_RE.search(query) else "us-en"
    with DDGS() as ddgs:
        results = list(ddgs.text(query, region=region, max_results=max_results))

    if not results:
        return f"Web search for {query!r} returned no results."

    lines = [f"## Web search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "").strip()
        url = r.get("href", "").strip()
        snippet = r.get("body", "").strip()
        lines.append(f"{i}. **{title}**\n   {url}\n   {snippet}\n")

    return "\n".join(lines)
