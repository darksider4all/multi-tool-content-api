# 🛠️ Multi-Tool Content API

[![Try on RapidAPI](https://img.shields.io/badge/RapidAPI-Try%20Now-blue)](https://rapidapi.com/oaidaadrian/api/multi-tool-content)
[![Apify Actors](https://img.shields.io/badge/Apify-Actors-orange)](https://apify.com/darknezz)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

One API for all your content processing needs — RSS parsing, article extraction, sitemap crawling, AI-readable file generation, and B2B lead search.

## 🚀 Quick Start

```python
import requests

url = "https://multi-tool-content.p.rapidapi.com/rss/parse"
headers = {
    "X-RapidAPI-Key": "YOUR_KEY",
    "X-RapidAPI-Host": "multi-tool-content.p.rapidapi.com",
    "Content-Type": "application/json"
}
payload = {"feed_url": "https://feeds.feedburner.com/TheHackersNews", "max_items": 10}

response = requests.post(url, json=payload, headers=headers)
print(response.json())
```

## 📋 Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/rss/parse` | POST | Parse RSS/Atom feeds with full article extraction |
| `/content/extract` | POST | Extract clean text & metadata from any URL |
| `/sitemap/crawl` | POST | Crawl sitemap.xml and extract structured content |
| `/llms-txt/generate` | POST | Generate AI-readable llms.txt files |
| `/ro-businesses/search` | POST | Search Romanian business directory for B2B leads |

## 💰 Pricing

| Tier | Price | Requests/mo |
|---|---|---|
| Free | $0 | 100 |
| Basic | $10/mo | 5,000 |
| Pro | $29/mo | 25,000 |

## 🔗 Links

- **RapidAPI:** [multi-tool-content](https://rapidapi.com/oaidaadrian/api/multi-tool-content)
- **Apify Actors:** [darknezz](https://apify.com/darknezz)
- **Live Docs:** [api.adrianhomelab.com/docs](https://api.adrianhomelab.com/docs)

## 📖 Articles & Tutorials

- [5 APIs Every Developer Needs](https://dev.to/oaida_adrian_afa2428f63d0/5-apis-every-developer-needs-for-content-processing-rss-extraction-sitemaps-ai-2630)
- [RSS Aggregator with Full Content Extraction](https://dev.to/oaida_adrian_afa2428f63d0/i-built-an-rss-aggregator-that-extracts-full-article-content-not-just-summaries-ifl)
- [Make Any Website AI-Readable with llms.txt](https://dev.to/oaida_adrian_afa2428f63d0/make-any-website-ai-readable-generating-llmstxt-files-with-python-3jop)
- [Scraping Romanian Business Data](https://dev.to/oaida_adrian_afa2428f63d0/scraping-187000-romanian-businesses-building-a-b2b-lead-generation-tool-176n)
- [Extract Content From Sitemaps](https://dev.to/oaida_adrian_afa2428f63d0/how-to-extract-clean-content-from-any-website-sitemap-for-seo-audits-ai-training-15a9)

## 🏗️ Architecture

Built with FastAPI, deployed on homelab infrastructure behind Cloudflare tunnel.

```
Client → RapidAPI Proxy → Cloudflare Tunnel → Caddy → FastAPI (Docker)
```

## 📝 License

MIT
