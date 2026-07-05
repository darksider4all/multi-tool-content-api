"""
RapidAPI Service — Multi-endpoint API for monetisation on RapidAPI marketplace.
Deploys on Adrian's homelab via Docker + Cloudflare tunnel.
SECURITY: SSRF protection, API key auth, docs disabled in production.
"""

import os
import time
import asyncio
import re
import gzip
import socket
import ipaddress
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import feedparser
import trafilatura
import requests as http_requests
from lxml import etree
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Header, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn

# --- Init ---

API_KEY = os.environ.get("API_GATEWAY_KEY", "")
RAPIDAPI_PROXY_SECRET = os.environ.get("RAPIDAPI_PROXY_SECRET", "")
ENABLE_DOCS = os.environ.get("ENABLE_DOCS", "false").lower() == "true"

app = FastAPI(
    title="Multi-Tool Content API",
    description="RSS parsing, content extraction, sitemap crawling, llms.txt generation, and Romanian business directory search.",
    version="2.0.0",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting (simple in-memory, per IP)
RATE_LIMIT = {}
RATE_WINDOW = 60  # seconds
RATE_MAX = int(os.environ.get("RATE_MAX", "60"))  # requests per minute

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# --- Models ---

class RSSRequest(BaseModel):
    feed_url: str = Field(..., description="RSS/Atom feed URL")
    max_items: int = Field(10, ge=1, le=100)
    extract_content: bool = Field(True, description="Extract full article content")

class ContentRequest(BaseModel):
    url: str = Field(..., description="URL to extract content from")
    include_links: bool = Field(True)
    include_tables: bool = Field(True)

class SitemapRequest(BaseModel):
    sitemap_url: str = Field(..., description="Sitemap XML URL")
    max_urls: int = Field(20, ge=1, le=200)
    extract_content: bool = Field(True)
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)

class LLMTxtRequest(BaseModel):
    url: str = Field(..., description="Website URL to generate llms.txt for")
    max_pages: int = Field(10, ge=1, le=50)

class ROBusinessRequest(BaseModel):
    search_term: str = Field(..., description="Business name or keyword to search")
    county: Optional[str] = Field(None, description="Filter by county (e.g., Bucuresti)")
    max_results: int = Field(10, ge=1, le=50)

class ProductHuntRequest(BaseModel):
    topic: str = Field("", description="Topic or keyword to filter products (empty for all)")
    max_results: int = Field(10, ge=1, le=50)

# --- Security: SSRF Protection ---

# Allowed schemes only
ALLOWED_SCHEMES = {"http", "https"}

# Blocked CIDR ranges (private, loopback, link-local, etc.)
BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("::1/128"),         # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),        # IPv6 private
    ipaddress.ip_network("fe80::/10"),       # IPv6 link-local
]

def _is_url_safe(url: str) -> tuple[bool, str]:
    """Validate URL is safe from SSRF. Returns (is_safe, reason)."""
    try:
        parsed = urlparse(url)

        # Scheme check
        if parsed.scheme not in ALLOWED_SCHEMES:
            return False, f"Blocked scheme: {parsed.scheme}"

        hostname = parsed.hostname
        if not hostname:
            return False, "No hostname in URL"

        # Block if hostname is a raw IP in private range
        try:
            ip = ipaddress.ip_address(hostname)
            for network in BLOCKED_NETWORKS:
                if ip in network:
                    return False, f"Blocked internal IP: {ip}"
        except ValueError:
            pass  # Not an IP, it's a hostname — check DNS below

        # DNS resolution check — resolve hostname and verify no private IPs
        try:
            addrinfos = socket.getaddrinfo(hostname, None)
            for addrinfo in addrinfos:
                ip = ipaddress.ip_address(addrinfo[4][0])
                for network in BLOCKED_NETWORKS:
                    if ip in network:
                        return False, f"Blocked DNS resolution: {hostname} → {ip}"
        except socket.gaierror:
            return False, f"DNS resolution failed: {hostname}"

        # Block common metadata endpoints
        if hostname in ["metadata.google.internal", "169.254.169.254"]:
            return False, "Blocked cloud metadata endpoint"

        return True, "OK"

    except Exception as e:
        return False, f"URL validation error: {e}"

# --- Security: API Key Auth Middleware ---

# Paths that don't require auth
PUBLIC_PATHS = {"/health", "/", "/favicon.ico"}

def _is_authorized(request: Request) -> bool:
    """Check if request has valid API key via RapidAPI proxy or direct key."""
    # If no API key configured, allow all (dev mode)
    if not API_KEY:
        return True

    # Check RapidAPI proxy. When RAPIDAPI_PROXY_SECRET is set, require the
    # secret header RapidAPI adds to every proxied request — the user-supplied
    # X-RapidAPI-Key alone is spoofable and must not grant access by itself.
    if RAPIDAPI_PROXY_SECRET:
        if request.headers.get("X-RapidAPI-Proxy-Secret", "") == RAPIDAPI_PROXY_SECRET:
            return True
    elif request.headers.get("X-RapidAPI-Key", ""):
        return True  # legacy mode: no proxy secret configured

    # Check direct API key
    direct_key = request.headers.get("X-API-Key", "")
    if direct_key and direct_key == API_KEY:
        return True

    # Check Authorization Bearer
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:] == API_KEY:
        return True

    return False

# --- Middleware ---

@app.middleware("http")
async def auth_and_rate_limit(request: Request, call_next):
    path = request.url.path
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    # Rate limiting (applies to all)
    if client_ip not in RATE_LIMIT:
        RATE_LIMIT[client_ip] = []

    RATE_LIMIT[client_ip] = [t for t in RATE_LIMIT[client_ip] if now - t < RATE_WINDOW]

    if len(RATE_LIMIT[client_ip]) >= RATE_MAX:
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded. Try again in a minute."}
        )

    RATE_LIMIT[client_ip].append(now)

    # Auth check (skip for public paths)
    if path not in PUBLIC_PATHS and not path.startswith("/mcp"):
        if not _is_authorized(request):
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized. Provide X-RapidAPI-Key or X-API-Key header."}
            )

    return await call_next(request)

# --- Helpers ---

def _fetch(url: str) -> Optional[bytes]:
    # SSRF check before any network request
    is_safe, reason = _is_url_safe(url)
    if not is_safe:
        return None

    try:
        resp = http_requests.get(url, headers=HTTP_HEADERS, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        content = resp.content
        if content[:2] == b'\x1f\x8b':
            content = gzip.decompress(content)
        return content
    except Exception:
        return None

def _fetch_post(url: str, data: dict, extra_headers: dict = None) -> Optional[bytes]:
    """Fetch a URL via POST with form data."""
    # SSRF check
    is_safe, reason = _is_url_safe(url)
    if not is_safe:
        return None

    try:
        headers = {**HTTP_HEADERS, **(extra_headers or {})}
        resp = http_requests.post(url, headers=headers, data=data, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        content = resp.content
        if content[:2] == b'\x1f\x8b':
            content = gzip.decompress(content)
        return content
    except Exception:
        return None

def _extract_content(html: str, url: str, include_links=True, include_tables=True) -> dict:
    extracted = trafilatura.extract(
        html, include_links=include_links, include_tables=include_tables,
        output_format="txt", with_metadata=True,
    )
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.find("title")
    meta_desc = soup.find("meta", attrs={"name": "description"})

    return {
        "url": url,
        "title": title_el.get_text(strip=True) if title_el else "",
        "content": extracted or "",
        "wordCount": len(extracted.split()) if extracted else 0,
        "metaDescription": meta_desc.get("content", "") if meta_desc else "",
        "extractedAt": datetime.now(timezone.utc).isoformat(),
    }

def _parse_sitemap(xml_bytes: bytes) -> tuple[list[dict], list[str]]:
    urls, child_sitemaps = [], []
    try:
        root = etree.fromstring(xml_bytes)
    except Exception:
        try:
            root = etree.fromstring(xml_bytes.decode("utf-8", errors="ignore").encode())
        except Exception:
            return [], []

    tag = root.tag.lower()
    if "}" in tag:
        tag = tag.split("}")[1]

    if "sitemapindex" in tag:
        for sitemap in root:
            for child in sitemap:
                ct = child.tag.lower()
                if "}" in ct:
                    ct = ct.split("}")[1]
                if "loc" in ct and child.text:
                    child_sitemaps.append(child.text.strip())
    elif "urlset" in tag:
        for url_elem in root:
            loc, lastmod = None, None
            for child in url_elem:
                ct = child.tag.lower()
                if "}" in ct:
                    ct = ct.split("}")[1]
                if "loc" in ct:
                    loc = child.text
                elif "lastmod" in ct:
                    lastmod = child.text
            if loc:
                urls.append({"url": loc.strip(), "lastmod": lastmod})
    return urls, child_sitemaps

def _fetch_all_sitemap_urls(sitemap_url: str, max_urls: int) -> list[dict]:
    all_urls, sitemaps_to_process, processed = [], [sitemap_url], set()
    while sitemaps_to_process and len(all_urls) < max_urls:
        current = sitemaps_to_process.pop(0)
        if current in processed:
            continue
        processed.add(current)
        xml_bytes = _fetch(current)
        if not xml_bytes:
            continue
        urls, child_sitemaps = _parse_sitemap(xml_bytes)
        all_urls.extend(urls)
        for child in child_sitemaps:
            if child not in processed and len(all_urls) < max_urls:
                sitemaps_to_process.append(child)
    return all_urls[:max_urls]

# --- Endpoints ---

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.post("/rss/parse")
async def parse_rss(req: RSSRequest):
    """Parse an RSS/Atom feed and optionally extract full article content."""
    try:
        raw = _fetch(req.feed_url)
        if not raw:
            raise HTTPException(status_code=502, detail="Failed to fetch feed.")
        feed = feedparser.parse(raw)
        items = []
        for entry in feed.entries[:req.max_items]:
            item = {
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "summary": entry.get("summary", ""),
                "author": entry.get("author", ""),
            }
            if req.extract_content and item["link"]:
                html_bytes = await asyncio.get_event_loop().run_in_executor(None, _fetch, item["link"])
                if html_bytes:
                    html = html_bytes.decode("utf-8", errors="ignore")
                    data = _extract_content(html, item["link"])
                    item["content"] = data["content"]
                    item["wordCount"] = data["wordCount"]
                else:
                    item["content"] = ""
                    item["wordCount"] = 0
            items.append(item)
        return {
            "feedTitle": feed.feed.get("title", ""),
            "feedUrl": req.feed_url,
            "itemCount": len(items),
            "items": items,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/content/extract")
async def extract_content(req: ContentRequest):
    """Extract clean content from any URL."""
    try:
        html_bytes = await asyncio.get_event_loop().run_in_executor(None, _fetch, req.url)
        if not html_bytes:
            raise HTTPException(status_code=502, detail="Failed to fetch URL.")
        html = html_bytes.decode("utf-8", errors="ignore")
        data = _extract_content(html, req.url, req.include_links, req.include_tables)
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/sitemap/crawl")
async def crawl_sitemap(req: SitemapRequest):
    """Crawl a sitemap and extract content from each page."""
    try:
        all_urls = _fetch_all_sitemap_urls(req.sitemap_url, req.max_urls * 2)
        if not all_urls:
            raise HTTPException(status_code=404, detail="No URLs found in sitemap.")

        filtered = []
        for entry in all_urls:
            url = entry["url"]
            if req.include_patterns and not any(re.search(p, url) for p in req.include_patterns):
                continue
            if req.exclude_patterns and any(re.search(p, url) for p in req.exclude_patterns):
                continue
            filtered.append(entry)
        filtered = filtered[:req.max_urls]

        results = []
        loop = asyncio.get_event_loop()
        for entry in filtered:
            record = {"url": entry["url"], "lastmod": entry.get("lastmod", "")}
            if req.extract_content:
                html_bytes = await loop.run_in_executor(None, _fetch, entry["url"])
                if html_bytes:
                    html = html_bytes.decode("utf-8", errors="ignore")
                    data = _extract_content(html, entry["url"])
                    record.update(data)
                else:
                    record.update({"content": "", "wordCount": 0, "title": ""})
            results.append(record)

        return {"sitemapUrl": req.sitemap_url, "pageCount": len(results), "pages": results}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/llms-txt/generate")
async def generate_llms_txt(req: LLMTxtRequest):
    """Generate an llms.txt file for a website by extracting content from its pages."""
    try:
        parsed = urlparse(req.url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        # Try sitemap first, fall back to page crawling
        sitemap_url = f"{base_url}/sitemap.xml"
        all_urls = _fetch_all_sitemap_urls(sitemap_url, req.max_pages)

        if not all_urls:
            # Fallback: just process the main page
            all_urls = [{"url": req.url, "lastmod": ""}]

        results = []
        loop = asyncio.get_event_loop()
        for entry in all_urls[:req.max_pages]:
            html_bytes = await loop.run_in_executor(None, _fetch, entry["url"])
            if html_bytes:
                html = html_bytes.decode("utf-8", errors="ignore")
                data = _extract_content(html, entry["url"])
                results.append(data)

        # Generate llms.txt format
        lines = [f"# {base_url}", "", f"> {base_url}", ""]
        for r in results:
            if r.get("title"):
                path = urlparse(r["url"]).path or "/"
                lines.append(f"## {r['title']}")
                lines.append(f"- [Full content]({r['url']})")
                if r.get("metaDescription"):
                    lines.append(f"- {r['metaDescription'][:150]}")
                lines.append("")

        return {
            "url": req.url,
            "pagesProcessed": len(results),
            "llmsTxt": "\n".join(lines),
            "pages": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ro-businesses/search")
async def search_ro_businesses(req: ROBusinessRequest):
    """Search Romanian business directory (listafirme.eu)."""
    try:
        search_url = "https://listafirme.eu/search.asp"
        post_data = {"searchfor": req.search_term}
        extra_headers = {
            "Referer": "https://listafirme.eu/",
            "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        html_bytes = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _fetch_post(search_url, post_data, extra_headers)
        )
        if not html_bytes:
            raise HTTPException(status_code=502, detail="Failed to fetch directory.")

        html = html_bytes.decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html, "lxml")

        results = []
        container = soup.select_one("div.lf-search-page__container")
        if not container:
            container = soup  # fallback to whole page

        rows = container.select("table tr")
        for row in rows[1:req.max_results + 1]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            name_cell = cells[1] if len(cells) > 1 else cells[0]
            name_link = name_cell.find("a")
            if not name_link:
                continue

            company_name = name_link.get_text(strip=True)
            href = name_link.get("href", "")
            detail_url = f"https://listafirme.eu{href}" if href else ""

            cell_text = name_cell.get_text(separator=" ", strip=True)
            address = cell_text.replace(company_name, "", 1).strip().strip(",").strip()

            cui = ""
            if href:
                cui_match = re.search(r"(\d+)/?$", href)
                if cui_match:
                    cui = cui_match.group(1)

            results.append({
                "companyName": company_name,
                "detailUrl": detail_url,
                "cui": cui,
                "address": address,
                "sourceUrl": search_url,
            })

        return {
            "searchTerm": req.search_term,
            "county": req.county,
            "resultCount": len(results),
            "businesses": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Product Hunt ---

@app.post("/producthunt/search")
async def search_producthunt(req: ProductHuntRequest):
    """Search Product Hunt launches via the Atom feed, optionally filtered by topic."""
    try:
        feed_url = "https://www.producthunt.com/feed"
        raw = await asyncio.get_event_loop().run_in_executor(None, _fetch, feed_url)
        if not raw:
            raise HTTPException(status_code=502, detail="Failed to fetch Product Hunt feed.")

        feed = feedparser.parse(raw)
        topic_lower = req.topic.strip().lower() if req.topic else ""
        products = []

        for entry in feed.entries:
            title = entry.get("title", "Untitled")
            link = entry.get("link", "")
            summary = entry.get("summary", "")

            tagline = ""
            if summary:
                soup = BeautifulSoup(summary, "html.parser")
                tagline = soup.get_text(strip=True)[:300]

            votes = 0
            vote_match = re.search(r'(\d+)\s*upvotes?', summary, re.I)
            if vote_match:
                votes = int(vote_match.group(1))

            topics = []
            if summary:
                topic_matches = re.findall(r'#(\w+)', summary)
                topics = list(set(t.lower() for t in topic_matches))[:5]

            if topic_lower:
                searchable = f"{title} {tagline} {' '.join(topics)}".lower()
                if topic_lower not in searchable:
                    continue

            products.append({
                "name": title,
                "tagline": tagline,
                "url": link,
                "votes": votes,
                "topics": topics,
            })

            if len(products) >= req.max_results:
                break

        return {
            "topic": req.topic,
            "resultCount": len(products),
            "products": products,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- MCP Server ---

from fastapi_mcp import FastApiMCP

mcp_server = FastApiMCP(
    fastapi=app,
    name='Multi-Tool Content API',
    description='RSS parsing, content extraction, sitemap crawling, llms.txt generation, B2B leads, Product Hunt tracking',
)
mcp_server.mount()

# --- Entry point ---

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8100")),
        workers=int(os.environ.get("WORKERS", "2")),
    )
