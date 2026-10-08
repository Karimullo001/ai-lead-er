from __future__ import annotations
import html
import json
import logging
import os
import re
import socket
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any, Dict, List

log = logging.getLogger("agentos.research")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
SSL_CTX = ssl._create_unverified_context()


class DuckDuckGoParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results: List[Dict[str, str]] = []
        self.in_title = False
        self.in_snippet = False
        self.curr_href = ""
        self.curr_title: List[str] = []
        self.curr_snippet: List[str] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        cls = d.get("class", "")
        if tag == "a" and ("result__a" in cls or "result__url" in cls):
            self.in_title = True
            href = d.get("href", "")
            # Clean DDG redirect URL if present
            if "uddg=" in href:
                try:
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    href = parsed.get("uddg", [href])[0]
                except Exception:
                    pass
            self.curr_href = href
            self.curr_title = []
        elif (tag in ("a", "div")) and "result__snippet" in cls:
            self.in_snippet = True
            self.curr_snippet = []

    def handle_data(self, data):
        if self.in_title:
            self.curr_title.append(data)
        elif self.in_snippet:
            self.curr_snippet.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.in_title:
            self.in_title = False
        elif (tag in ("a", "div")) and self.in_snippet:
            self.in_snippet = False
            title = html.unescape(" ".join("".join(self.curr_title).split()))
            snippet = html.unescape(" ".join("".join(self.curr_snippet).split()))
            if title and self.curr_href:
                self.results.append({
                    "title": title,
                    "url": self.curr_href,
                    "snippet": snippet,
                })
                self.curr_title = []
                self.curr_snippet = []
                self.curr_href = ""


def _search_google_news(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Fetch live headlines via Google News RSS."""
    try:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
            "q": query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        })
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=6) as resp:
            root = ET.fromstring(resp.read())
            results = []
            for item in root.findall(".//item")[:max_results]:
                title = item.find("title").text if item.find("title") is not None else ""
                link = item.find("link").text if item.find("link") is not None else ""
                pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                desc = item.find("description").text if item.find("description") is not None else ""
                clean_desc = re.sub(r"<[^>]+>", "", desc or "")
                if title:
                    results.append({
                        "title": title,
                        "url": link,
                        "snippet": f"({pub_date}) {clean_desc}".strip(),
                    })
            return results
    except Exception as e:
        log.debug("Google news RSS query failed: %s", e)
        return []


def _search_wikipedia(query: str, max_results: int = 3) -> List[Dict[str, str]]:
    """Fetch factual data from Wikipedia."""
    try:
        url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "utf8": "1",
        })
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = []
            for item in data.get("query", {}).get("search", [])[:max_results]:
                title = item.get("title", "")
                snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
                link = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
                results.append({"title": f"Wikipedia: {title}", "url": link, "snippet": snippet})
            return results
    except Exception as e:
        log.debug("Wikipedia query failed: %s", e)
        return []


def search_web(query: str, max_results: int = 6) -> Dict[str, Any]:
    """Multi-source live web search with automatic fallback."""
    q = str(query).strip()
    if not q:
        return {"query": "", "results": [], "error": "empty query"}

    results: List[Dict[str, str]] = []

    # 1. Primary: DuckDuckGo HTML
    try:
        data = urllib.parse.urlencode({"q": q}).encode("utf-8")
        req = urllib.request.Request("https://html.duckduckgo.com/html/", data=data, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=8) as r:
            parser = DuckDuckGoParser()
            parser.feed(r.read().decode("utf-8", "ignore"))
            results.extend(parser.results[:max_results])
    except Exception as e:
        log.warning("DuckDuckGo HTML search failed: %s", e)

    # 2. Secondary: If few results, complement with Google News RSS
    if len(results) < 3:
        news = _search_google_news(q, max_results=3)
        for n in news:
            if not any(r["title"] == n["title"] for r in results):
                results.append(n)

    # 3. Tertiary: Wikipedia if still empty
    if not results:
        wiki = _search_wikipedia(q, max_results=3)
        results.extend(wiki)

    seen = set()
    deduped = []
    for r in results:
        t = r["title"].strip()
        if t and t not in seen:
            seen.add(t)
            deduped.append(r)
        if len(deduped) >= max_results:
            break

    return {"query": q, "results": deduped, "count": len(deduped)}


def research(query: str, max_results: int = 6) -> str:
    """Format live search findings into structured markdown context."""
    res = search_web(query, max_results=max_results)
    items = res.get("results", [])
    if not items:
        return f"Internetdan '{query}' bo'yicha ma'lumot qidirildi, biroq to'g'ridan-to'g'ri natijalar topilmadi."

    lines = [f"🌐 **Internetdan jonli natijalar ({res['query']}):**"]
    for i, x in enumerate(items, 1):
        lines.append(f"[{i}] [{x['title']}]({x['url']})\n{x['snippet']}")
    return "\n\n".join(lines)
