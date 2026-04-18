from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from napyclaw.tools.base import Tool

if TYPE_CHECKING:
    import httpx


class SearchBackend(ABC):
    name: str

    @abstractmethod
    async def search(self, query: str, count: int = 5) -> list[dict]:
        """Return a list of {title, url, snippet} dicts. Raises on failure."""
        ...


class BraveBackend(SearchBackend):
    name = "brave"

    def __init__(self, api_key: str, http_client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._http = http_client

    async def search(self, query: str, count: int = 5) -> list[dict]:
        resp = await self._http.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": self._api_key,
            },
            params={"q": query, "count": count},
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("description", ""),
            }
            for r in data.get("web", {}).get("results", [])[:count]
        ]


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


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web. Tries configured providers in order, falls back automatically. "
        "Returns top results with title, URL, and snippet."
    )
    injection_source = "web_search"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
        },
        "required": ["query"],
    }

    def __init__(self, backends: list[SearchBackend]) -> None:
        if not backends:
            raise ValueError("WebSearchTool requires at least one search backend")
        self._backends = backends

    async def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        if not query:
            return "Error: query is required."

        last_error: str = ""
        for backend in self._backends:
            try:
                results = await backend.search(query)
                if results:
                    lines = [
                        f"**{r['title']}**\n{r['url']}\n{r['snippet']}"
                        for r in results
                    ]
                    body = "\n\n".join(lines)
                    return f"<!-- SEARCH_RESULTS -->\n{body}\n<!-- /SEARCH_RESULTS -->"
            except Exception as exc:
                last_error = f"{backend.name}: {exc}"
                continue

        return f"Error: all search backends failed. Last error — {last_error}"
