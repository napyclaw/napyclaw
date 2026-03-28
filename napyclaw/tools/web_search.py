from __future__ import annotations

from typing import TYPE_CHECKING

from napyclaw.tools.base import Tool

if TYPE_CHECKING:
    import httpx


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web using Brave Search. Returns top 5 results with title, URL, and snippet."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
        },
        "required": ["query"],
    }

    def __init__(self, brave_api_key: str, http_client: httpx.AsyncClient) -> None:
        self._api_key = brave_api_key
        self._http = http_client

    async def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        if not query:
            return "Error: query is required."

        try:
            resp = await self._http.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": self._api_key,
                },
                params={"q": query, "count": 5},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return f"Error: web search failed — {exc}"

        results = data.get("web", {}).get("results", [])
        if not results:
            return "No results found."

        lines = []
        for r in results[:5]:
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = r.get("description", "")
            lines.append(f"**{title}**\n{url}\n{snippet}")

        return "\n\n".join(lines)
