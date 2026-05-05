# crawl_core

A FastAPI service that crawls a URL, extracts metadata and body text, and classifies the page into a page type + relevant topics. Handles both server-rendered and JS-heavy (SPA/CSR) pages using a hybrid fetch strategy.

## Features

- **Hybrid fetching** — fast `curl_cffi` with browser TLS fingerprinting (~200ms), automatic Playwright fallback for JS-heavy pages
- **Anti-bot evasion** — browser-grade TLS impersonation, stealth Playwright contexts, Chrome 120 security headers
- **Rich metadata extraction** — title, description, canonical URL, Open Graph, Twitter Card, JSON-LD structured data, heading hierarchy
- **Clean body text** — trafilatura-based extraction with nav/footer/review stripping and site-specific pruning (e.g., Amazon review sections)
- **Page classification** — 22 page types, 32 IAB topic categories, keyword extraction, and one-line summaries
- **Dual classification backend** — local BART-MNLI zero-shot inference or cloud Gemini Flash via Vertex AI
- **Bearer token auth** — protects public deployments from abuse

## Architecture

```
POST /crawl { url }
  │
  ├─ 1. fetch(url)            → HTML + resolved_url           curl_cffi ~200ms
  │
  ├─ 2. analyze(html, url)    → needs JS render?
  │      ├─ No                → use HTML as-is
  │      └─ Yes               → Playwright fallback            ~3-8s
  │
  ├─ 3. parse(html)           → metadata (OG, Twitter, JSON-LD, headings)
  │
  ├─ 4. extract(html)         → clean body text + word count
  │
  ├─ 5. classify(text, ...)   → page_type + topics + keywords + summary
  │      ├─ Local backend     → BART-MNLI zero-shot + 4-tier keyword extraction
  │      └─ Vertex backend    → single Gemini Flash call (all-in-one)
  │
  └─ 6. Return JSON
```

## Tech Stack

| Component | Choice | Why |
|-----------|--------|-----|
| HTTP client | curl_cffi | Browser-grade TLS fingerprint. `httpx` gets blocked by bot detection. |
| JS rendering | Playwright + stealth | Async API, resource blocking. Selenium is slower, no async. |
| HTML parsing | Selectolax (lexbor) | 10-30x faster than BS4. BS4+lxml fallback for edge cases. |
| Body extraction | trafilatura | Purpose-built for content extraction. Handles diverse layouts. |
| Keywords (local) | 4-tier hybrid | JSON-LD > spaCy noun chunks > OG tags > YAKE statistical fallback |
| Classification (local) | BART-MNLI zero-shot | Any labels at runtime, no training data needed, runs locally. |
| Classification (cloud) | Gemini 2.5 Flash | Fast, cheap. Single call replaces BART + keyword pipeline. |

## Quick Start

### Local (BART-MNLI backend)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-local.txt

# Download models
python -m spacy download en_core_web_sm
playwright install chromium

# Run
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Cloud (Vertex AI backend)

```bash
# Set environment
export CLASSIFIER_BACKEND=vertex
export GOOGLE_CLOUD_PROJECT=your-project
export GOOGLE_CLOUD_LOCATION=us-central1
export API_TOKEN=your-secret-token

# Build and deploy
gcloud builds submit --tag us-central1-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/crawl-core/crawl-core:vertex
gcloud run deploy crawl-core \
  --image us-central1-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/crawl-core/crawl-core:vertex \
  --region us-central1 \
  --set-env-vars "CLASSIFIER_BACKEND=vertex,GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT,GOOGLE_CLOUD_LOCATION=us-central1,API_TOKEN=$API_TOKEN" \
  --memory 2Gi --cpu 2 --timeout 300s \
  --min-instances 1 --max-instances 1 \
  --allow-unauthenticated
```

## API

### `POST /crawl`

```bash
curl -X POST \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.amazon.com/dp/B009GQ034C"}' \
  http://localhost:8000/crawl
```

Response:

```json
{
  "status": "success",
  "url": "https://www.amazon.com/dp/B009GQ034C",
  "resolved_url": "https://www.amazon.com/Cuisinart-CPT-122.../dp/B009GQ034C/",
  "render_method": "curl_cffi",
  "status_code": 200,
  "metadata": {
    "title": "Cuisinart CPT-122 2-Slice Compact Plastic Toaster...",
    "structured_data": [],
    "headings": { "h1": ["..."], "h2": ["..."] }
  },
  "content": {
    "body_text": "...",
    "word_count": 2003,
    "reading_time_minutes": 10.0
  },
  "classification": {
    "page_type": "product page",
    "page_type_confidence": 0.98,
    "topics": [
      { "topic": "Shopping", "relevance_score": 1.0 },
      { "topic": "Home, Garden", "relevance_score": 0.9 }
    ],
    "iab_categories": ["Shopping", "Home, Garden"],
    "keywords": ["Cuisinart CPT-122 Toaster", "2-Slice Toaster", "Kitchen Appliance"],
    "summary": "This page details the Cuisinart CPT-122 2-slice compact toaster..."
  }
}
```

### `GET /health`

```json
{ "status": "ok", "models_loaded": true }
```

## Configuration

All constants live in `app/config.py`. Key environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CLASSIFIER_BACKEND` | `local` | `local` (BART-MNLI) or `vertex` (Gemini Flash) |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Vertex AI model (only when backend=vertex) |
| `GOOGLE_CLOUD_PROJECT` | — | GCP project ID (only when backend=vertex) |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | GCP region (only when backend=vertex) |
| `API_TOKEN` | — | Bearer token for auth. Unset = no auth. |

## Project Structure

```
app/
├── main.py              # FastAPI app, routes
├── lifespan.py          # startup/shutdown, model loading
├── pipeline.py          # crawl orchestration, latency tracking
├── config.py            # all constants and thresholds
├── schemas.py           # Pydantic request/response models
├── auth.py              # Bearer token middleware
├── fetch/
│   ├── fetcher.py       # curl_cffi HTTP fetch
│   ├── detector.py      # JS-heavy page detection
│   └── renderer.py      # Playwright fallback
├── parse/
│   ├── parser.py        # metadata extraction (selectolax + BS4)
│   └── extractor.py     # body text extraction (trafilatura)
└── classify/
    ├── types.py         # label taxonomies and constants
    └── classifier.py    # classification + keyword extraction
```

## Dual Backend Design

The classifier supports two backends, switched via `CLASSIFIER_BACKEND`:

| | Local (BART-MNLI) | Cloud (Vertex AI Gemini) |
|---|---|---|
| **Image size** | ~4 GB (includes torch) | ~1.5 GB |
| **RAM at runtime** | ~3 GB (model weights) | ~512 MB |
| **Classification** | Two NLI passes (page type + topics) | Single Gemini API call |
| **Keywords** | 4-tier hybrid extraction | Gemini extracts inline |
| **Summary** | Template-based from structured data | Gemini generates |
| **Latency** | ~800ms (GPU) / ~5s+ (CPU) | ~1-2s |
| **Cost** | Free (local compute) | ~$0.001/request |
| **Dependencies** | torch, transformers, spacy, yake | google-genai |
