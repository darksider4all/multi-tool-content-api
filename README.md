# Multi-Tool Content API

One self-hostable API for the content-processing tasks that keep coming up in
AI and data pipelines: full-text RSS parsing, clean article extraction,
sitemap crawling, and llms.txt generation.

Single Python file + Docker. No accounts, no API keys, no external services —
`docker compose up` and it's running on localhost.

## Quick start (self-hosted)

```bash
git clone https://github.com/darksider4all/multi-tool-content-api.git
cd multi-tool-content-api
docker compose up -d
curl http://localhost:8100/health
```

Parse a feed with full article extraction:

```bash
curl -X POST http://localhost:8100/rss/parse \
  -H "Content-Type: application/json" \
  -d '{"feed_url": "https://hnrss.org/frontpage", "max_items": 5}'
```

## Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/rss/parse` | POST | Parse RSS/Atom feeds; follows each link and extracts the **full article text**, not just the feed summary |
| `/content/extract` | POST | Clean text + metadata from any URL (boilerplate removed via trafilatura) |
| `/sitemap/crawl` | POST | Walk a sitemap.xml and extract structured content from every page |
| `/llms-txt/generate` | POST | Crawl a site and generate an [llms.txt](https://llmstxt.org/) file |
| `/ro-businesses/search` | POST | Search the Romanian business directory (listafirme.ro) for B2B leads |

Interactive docs: set `ENABLE_DOCS=true` and open `http://localhost:8100/docs`.

## Configuration

All optional — the defaults give you an open instance on localhost.

| Env var | Default | Purpose |
|---|---|---|
| `PORT` | `8100` | Listen port |
| `WORKERS` | `2` | Uvicorn workers |
| `RATE_MAX` | `60` | Requests per minute per client IP |
| `API_GATEWAY_KEY` | *(empty)* | If set, requests must send `X-API-Key` or `Authorization: Bearer` with this value. Leave empty for open mode. |
| `RAPIDAPI_PROXY_SECRET` | *(empty)* | Only for deployments behind RapidAPI — validates the `X-RapidAPI-Proxy-Secret` header RapidAPI adds to proxied requests |
| `ENABLE_DOCS` | `false` | Expose Swagger UI at `/docs` |

If you expose the service beyond localhost, set `API_GATEWAY_KEY`.

## How it works

- **FastAPI** service, one file: [`app/main.py`](app/main.py)
- **trafilatura** for article/content extraction (with lxml + BeautifulSoup for
  the structural bits)
- **feedparser** for RSS/Atom
- Per-IP rate limiting and optional key auth in a small middleware

## Limitations

- No JavaScript rendering — extraction works on server-side HTML, so
  SPA-heavy sites extract poorly.
- Extraction quality is whatever trafilatura manages on a given page; it's
  very good on articles and docs, weaker on heavily templated pages.
- The in-memory rate limiter is per-process — fine for a single instance,
  not a distributed deployment.

## Hosted options

If you'd rather not self-host: the same API is available on
[RapidAPI](https://rapidapi.com/oaidaadrian/api/multi-tool-content) (free tier
available), and the individual tools are published as standalone actors on the
[Apify store](https://apify.com/darknezz).

## License

MIT
