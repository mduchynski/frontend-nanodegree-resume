"""Web search and page reading.

Uses the Brave Search API free tier (2,000 queries/month, no card) when a key
is present, and falls back to scraping DuckDuckGo's HTML endpoint when it is
not. Both cost nothing; Brave is markedly better.
"""
from __future__ import annotations

import urllib.parse

import httpx2 as httpx
from selectolax.parser import HTMLParser

from core.config import cfg

from .base import Tool, ToolError

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JarvisAssistant/1.0"
_TIMEOUT = httpx.Timeout(20.0, connect=8.0)


class WebSearchTool(Tool):
    name = "web_search"
    description = """
    Search the web and get back ranked results with titles, URLs and snippets.
    Use for anything time-sensitive, factual, or outside your training data.
    Snippets are short -- follow up with `read_webpage` on the most promising
    result when the answer needs real detail.
    """
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "count": {
                "type": "integer",
                "description": "Number of results, 1-10. Default 6.",
            },
        },
        "required": ["query"],
    }

    async def run(self, query: str, count: int = 6) -> str:
        count = max(1, min(int(count or 6), 10))
        try:
            if cfg.brave_api_key:
                results = await _brave(query, count)
            else:
                results = await _duckduckgo(query, count)
        except httpx.HTTPError as exc:
            raise ToolError(f"Search failed: {exc}") from exc

        if not results:
            return f"No results for {query!r}."

        lines = [f"Search results for {query!r}:", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
        return "\n".join(lines)


async def _brave(query: str, count: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": count},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": cfg.brave_api_key,
            },
        )
        if resp.status_code == 429:
            raise ToolError("Brave Search rate limit reached (free tier is 1 query/sec).")
        resp.raise_for_status()
        data = resp.json()

    out = []
    for item in (data.get("web", {}) or {}).get("results", [])[:count]:
        out.append(
            {
                "title": item.get("title", "").strip(),
                "url": item.get("url", ""),
                "snippet": _strip_tags(item.get("description", "")),
            }
        )
    return out


async def _duckduckgo(query: str, count: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": _UA},
        )
        resp.raise_for_status()

    tree = HTMLParser(resp.text)
    out = []
    for node in tree.css("div.result")[: count * 2]:
        link = node.css_first("a.result__a")
        if not link:
            continue
        href = link.attributes.get("href", "")
        # DDG wraps outbound links in a redirector.
        if "uddg=" in href:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = (parsed.get("uddg") or [href])[0]
        snippet_node = node.css_first("a.result__snippet") or node.css_first(".result__snippet")
        out.append(
            {
                "title": link.text(strip=True),
                "url": href,
                "snippet": snippet_node.text(strip=True) if snippet_node else "",
            }
        )
        if len(out) >= count:
            break
    return out


class ReadWebpageTool(Tool):
    name = "read_webpage"
    description = """
    Fetch a URL and return its readable text content. Use after `web_search`
    when a snippet is not enough, or when the user gives you a link. Content is
    truncated to keep the conversation cheap -- ask for a specific section if
    you need more.
    """
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The full URL to fetch."},
            "max_chars": {
                "type": "integer",
                "description": "Character cap on the extracted text. Default 6000.",
            },
        },
        "required": ["url"],
    }

    async def run(self, url: str, max_chars: int = 6000) -> str:
        if not url.startswith(("http://", "https://")):
            raise ToolError("URL must start with http:// or https://")
        max_chars = max(500, min(int(max_chars or 6000), 20000))

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": _UA})
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError(f"Could not fetch {url}: {exc}") from exc

        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            raise ToolError(f"{url} is {ctype or 'an unknown type'}, not readable text.")

        text = _extract_text(resp.text)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[truncated at {max_chars} characters]"
        return f"Content of {url}:\n\n{text}"


def _extract_text(html: str) -> str:
    tree = HTMLParser(html)
    for tag in ("script", "style", "nav", "header", "footer", "aside", "noscript", "form"):
        for node in tree.css(tag):
            node.decompose()
    body = tree.css_first("article") or tree.css_first("main") or tree.body
    if body is None:
        return ""
    lines = [ln.strip() for ln in body.text(separator="\n").splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _strip_tags(s: str) -> str:
    return HTMLParser(s).text(strip=True) if s else ""
