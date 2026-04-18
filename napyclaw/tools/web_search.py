from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from napyclaw.tools.base import Tool

if TYPE_CHECKING:
    import httpx

# Per-backend guidance surfaced to the LLM in the tool description.
_BACKEND_DESCRIPTIONS = {
    "searxng": (
        "searxng — self-hosted meta-search aggregating Google, Bing, and DuckDuckGo. "
        "Best for: current events, news, general web queries, anything that benefits "
        "from broad index coverage."
    ),
    "exa": (
        "exa — neural/semantic search optimised for LLM use. "
        "Best for: research papers, technical documentation, conceptual questions, "
        "finding authoritative sources on a topic, queries phrased as natural language."
    ),
    "tavily": (
        "tavily — AI-native search with structured result summaries. "
        "Best for: fact-finding, comparisons, queries where a clean summary matters "
        "more than raw link lists."
    ),
}

_MULTI_SOURCE_HINT = (
    "For research or analysis tasks, searching multiple providers gives better coverage — "
    "call this tool once per provider with providers=[name] rather than a single call with all providers, "
    "so you can see each source's perspective separately."
)


def _build_description(available: list[str]) -> str:
    lines = ["Search the web using one or more configured providers.\n"]
    lines.append("Available providers and when to use each:")
    for name in available:
        if name in _BACKEND_DESCRIPTIONS:
            lines.append(f"  - {_BACKEND_DESCRIPTIONS[name]}")
    lines.append(f"\n{_MULTI_SOURCE_HINT}")
    return "\n".join(lines)


class SearchBackend(ABC):
    name: str

    @abstractmethod
    async def search(self, query: str, count: int = 5) -> list[dict]:
        """Return a list of {title, url, snippet} dicts. Raises on failure."""
        ...


class SearXNGBackend(SearchBackend):
    name = "searxng"

    def __init__(self, base_url: str, http_client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http_client

    async def search(self, query: str, count: int = 5) -> list[dict]:
        resp = await self._http.get(
            f"{self._base_url}/search",
            params={"q": query, "format": "json", "pageno": 1},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            for r in data.get("results", [])[:count]
        ]


class TavilyBackend(SearchBackend):
    name = "tavily"

    def __init__(self, api_key: str, http_client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._http = http_client

    async def search(self, query: str, count: int = 5) -> list[dict]:
        resp = await self._http.post(
            "https://api.tavily.com/search",
            json={"api_key": self._api_key, "query": query, "max_results": count},
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            for r in data.get("results", [])[:count]
        ]


class ExaBackend(SearchBackend):
    name = "exa"

    def __init__(self, api_key: str, http_client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._http = http_client

    async def search(self, query: str, count: int = 5) -> list[dict]:
        resp = await self._http.post(
            "https://api.exa.ai/search",
            json={"query": query, "numResults": count, "contents": {"text": {"maxCharacters": 500}}},
            headers={"x-api-key": self._api_key, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": (r.get("contents") or {}).get("text") or r.get("summary", ""),
            }
            for r in data.get("results", [])[:count]
        ]


class WebSearchTool(Tool):
    name = "web_search"
    injection_source = "web_search"

    def __init__(self, backends: list[SearchBackend]) -> None:
        if not backends:
            raise ValueError("WebSearchTool requires at least one search backend")
        self._backends = {b.name: b for b in backends}
        self.description = _build_description(list(self._backends))
        self.parameters = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "providers": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(self._backends)},
                    "description": (
                        "Which providers to query. Omit to use all available providers in parallel. "
                        "Specify one to target a specific source."
                    ),
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        if not query:
            return "Error: query is required."

        requested = kwargs.get("providers") or list(self._backends)
        targets = [self._backends[n] for n in requested if n in self._backends]
        if not targets:
            return f"Error: none of the requested providers are available: {requested}"

        if len(targets) == 1:
            return await self._run_single(query, targets[0])

        # Multiple providers — run in parallel, merge, deduplicate by URL
        tasks = [self._fetch(query, backend) for backend in targets]
        results_per_backend = await asyncio.gather(*tasks)

        seen_urls: set[str] = set()
        sections: list[str] = []
        for backend, (results, error) in zip(targets, results_per_backend):
            if error:
                sections.append(f"[{backend.name}: failed — {error}]")
                continue
            if not results:
                sections.append(f"[{backend.name}: no results]")
                continue
            lines = []
            for r in results:
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    lines.append(f"**{r['title']}**\n{r['url']}\n{r['snippet']}")
            if lines:
                sections.append(f"### {backend.name}\n" + "\n\n".join(lines))

        if not sections:
            return "No results found across any provider."

        body = "\n\n".join(sections)
        return f"<!-- SEARCH_RESULTS -->\n{body}\n<!-- /SEARCH_RESULTS -->"

    async def _run_single(self, query: str, backend: SearchBackend) -> str:
        results, error = await self._fetch(query, backend)
        if error:
            return f"Error: {backend.name} failed — {error}"
        if not results:
            return "No results found."
        lines = [f"**{r['title']}**\n{r['url']}\n{r['snippet']}" for r in results]
        body = "\n\n".join(lines)
        return f"<!-- SEARCH_RESULTS -->\n{body}\n<!-- /SEARCH_RESULTS -->"

    async def _fetch(self, query: str, backend: SearchBackend) -> tuple[list[dict], str]:
        try:
            results = await backend.search(query)
            return results, ""
        except Exception as exc:
            return [], str(exc)
