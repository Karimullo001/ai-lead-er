
from __future__ import annotations
import html, ipaddress, socket, urllib.parse, urllib.request
from html.parser import HTMLParser
from typing import Any

UA = "AgentOS/1.0"
MAX_BYTES = 300_000

class SearchParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.results=[]; self.on=False; self.href=""; self.buf=[]
    def handle_starttag(self, tag, attrs):
        d=dict(attrs)
        if tag=="a" and "result__a" in d.get("class",""):
            self.on=True; self.href=d.get("href",""); self.buf=[]
    def handle_data(self, data):
        if self.on: self.buf.append(data)
    def handle_endtag(self, tag):
        if tag=="a" and self.on:
            title=html.unescape(" ".join("".join(self.buf).split()))
            if title and self.href: self.results.append({"title":title,"url":self.href})
            self.on=False

class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in {"script","style","noscript","svg"}: self.skip+=1
    def handle_endtag(self, tag):
        if tag in {"script","style","noscript","svg"} and self.skip: self.skip-=1
    def handle_data(self,data):
        if not self.skip:
            x=" ".join(data.split())
            if x: self.parts.append(x)

def safe_url(url: str) -> str:
    u=urllib.parse.urlparse(url)
    if u.scheme not in {"http","https"} or not u.hostname: raise ValueError("Only public HTTP(S) URLs allowed")
    if u.hostname.lower() in {"localhost","localhost.localdomain"}: raise ValueError("localhost blocked")
    for info in socket.getaddrinfo(u.hostname,None):
        ip=ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("private/reserved network blocked")
    return url

def fetch(url: str, timeout=12) -> bytes:
    req=urllib.request.Request(safe_url(url),headers={"User-Agent":UA,"Accept":"text/html,*/*;q=.8"})
    with urllib.request.urlopen(req,timeout=timeout) as r: return r.read(MAX_BYTES)

def search_web(query: str, max_results=6) -> dict[str,Any]:
    q=str(query).strip()
    if not q: return {"query":"","results":[],"error":"empty query"}
    url="https://html.duckduckgo.com/html/?"+urllib.parse.urlencode({"q":q})
    try:
        p=SearchParser(); p.feed(fetch(url).decode("utf-8","ignore"))
        out=[]; seen=set()
        for x in p.results:
            href=x["url"]
            if href.startswith("//"): href="https:"+href
            if href in seen: continue
            seen.add(href); snippet=""
            try:
                tp=TextParser(); tp.feed(fetch(href,8).decode("utf-8","ignore"))
                snippet=" ".join(tp.parts)[:700]
            except Exception: pass
            out.append({"title":x["title"],"url":href,"snippet":snippet})
            if len(out)>=max_results: break
        return {"query":q,"results":out,"count":len(out)}
    except Exception as e:
        return {"query":q,"results":[],"error":f"{type(e).__name__}: {e}"}

def research(query: str, max_results=6) -> str:
    r=search_web(query,max_results)
    lines=[f"Research query: {r['query']}"]
    if r.get("error"): lines.append("Research error: "+r["error"])
    for i,x in enumerate(r.get("results",[]),1):
        lines.append(f"[{i}] {x['title']}\nURL: {x['url']}\nEvidence: {x['snippet']}")
    if not r.get("results"): lines.append("No live sources retrieved; never fabricate citations.")
    return "\n\n".join(lines)
