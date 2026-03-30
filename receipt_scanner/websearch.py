from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup


@dataclass
class WebResult:
    title: str
    url: str
    snippet: Optional[str] = None


def ddg_search(query: str, *, max_results: int = 5, timeout_s: int = 12) -> list[WebResult]:
    q = (query or "").strip()
    if not q:
        return []

    # DuckDuckGo HTML 结果页（无需 key）
    resp = requests.get(
        "https://duckduckgo.com/html/",
        params={"q": q},
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
        },
        timeout=timeout_s,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    out: list[WebResult] = []

    # 结构可能变化：尽量宽松选择
    for r in soup.select(".result"):
        a = r.select_one("a.result__a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        url = a.get("href") or ""
        snippet_el = r.select_one(".result__snippet")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else None
        if title and url:
            out.append(WebResult(title=title, url=url, snippet=snippet))
        if len(out) >= max_results:
            break

    return out

