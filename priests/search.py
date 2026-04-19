"""Web search helper for priests CLI.

Optional feature — requires the `search` extra:
    pip install "priests[search]"
    uv pip install "priests[search]"
"""

from __future__ import annotations


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
            "Web search requires the 'search' extra. "
            "Install it with: uv pip install \"priests[search]\""
        ) from exc

    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))

    if not results:
        return f"Web search for {query!r} returned no results."

    lines = [f"## Web search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "").strip()
        url = r.get("href", "").strip()
        snippet = r.get("body", "").strip()
        lines.append(f"{i}. **{title}**\n   {url}\n   {snippet}\n")

    return "\n".join(lines)
