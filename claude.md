# CLAUDE.md — BrightEdge URL Crawler & Classifier

## Project Overview

A FastAPI service that takes a single URL, crawls the page, extracts HTML metadata, and classifies it into page type + relevant topics. Handles both server-rendered (HTML-heavy) and JS-heavy (SPA/CSR) pages using a hybrid fetch strategy — fast `curl_cffi` first, automatic Playwright fallback when skeleton pages are detected. No third-party crawling/classification services allowed; libraries are fine.

## Tech Stack

- **Language:** Python 3.12+
- **Framework:** FastAPI (async)
- **HTTP Client:** curl_cffi (async) — primary fetch with browser TLS fingerprinting
- **JS Rendering:** Playwright (async, Chromium) + playwright-stealth — fallback for JS-heavy/SPA pages, with anti-bot evasion
- **HTML Parsing:** Selectolax (lexbor) primary, BeautifulSoup4 (bs4) + `lxml` fallback
- **Body Text Extraction:** trafilatura (with BS4 fallback)
- **Keyword Extraction:** 4-tier hybrid — JSON-LD → spaCy noun chunks → OG tags → YAKE statistical fallback
- **NLP (keywords):** spaCy `en_core_web_sm` — noun-chunk extraction for Title/H1 keyword seeding
- **Page Classification:** transformers + `facebook/bart-large-mnli` (zero-shot, NLI-based) — all signals fed to model (schema types, URL path, OG type, metadata, body text)

### Why These Choices

| Component       | Choice              | Why not alternatives                                                                                                                                              |
| --------------- | ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| HTTP client     | curl_cffi            | Browser-grade TLS fingerprint via `impersonate`, async, follows redirects. `httpx` gets blocked by bot detection on major sites.                                   |
| JS rendering    | Playwright          | Official Python SDK, async API, resource blocking. Selenium is slower, no async.                                                                                  |
| Body extraction | trafilatura         | Purpose-built for article/content extraction, handles diverse layouts. Raw BS4 requires manual nav/footer stripping.                                              |
| Keywords        | 4-tier hybrid        | JSON-LD/metadata tiers provide high-signal keywords without model cost. YAKE body fallback fills gaps statistically. spaCy noun-chunks split compound titles.     |
| NLP (keywords)  | spaCy `en_core_web_sm` | Lightweight (12MB), only noun_chunks used for title/H1 splitting. NER and textcat disabled at load.                                                             |
| HTML parsing    | Selectolax (lexbor)  | ~10-30x faster DOM queries than BS4. BS4+lxml fallback for strict-parse errors and tree mutation (extractor.py).                                                  |
| Classification  | BART-MNLI zero-shot | Classifies into any labels at runtime without training data. No fixed taxonomy needed. Runs locally — no external API calls. JSON-LD `@type` can bypass for page type. |

## Project Structure

```
crawl_core/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, routes (~30 lines)
│   ├── lifespan.py          # startup/shutdown, model loading
│   ├── pipeline.py          # crawl orchestration, latency tracking
│   ├── auth.py              # TokenAuthMiddleware for Cloud Run deployment
│   ├── config.py            # all constants, thresholds, dynamic locale/timezone detection
│   ├── schemas.py           # Pydantic request/response models
│   ├── fetch/
│   │   ├── __init__.py      # re-exports: fetch, analyze, render_page, RobotsCache
│   │   ├── fetcher.py       # fetch URL via curl_cffi (primary)
│   │   ├── detector.py      # analyze HTML to detect JS-heavy skeleton pages
│   │   ├── renderer.py      # Playwright fallback for JS-heavy pages
│   │   ├── constants.py     # shared constants for detector + renderer
│   │   └── robots.py        # robots.txt compliance with in-memory TTL cache
│   ├── parse/
│   │   ├── __init__.py      # re-exports: parse, extract
│   │   ├── parser.py        # extract metadata from HTML (selectolax + BS4 fallback)
│   │   └── extractor.py     # extract clean body text (trafilatura)
│   └── classify/
│       ├── __init__.py      # re-exports: classify
│       ├── classifier.py    # page type + topic classification
│       └── types.py         # PAGE_TYPE_LABELS, IAB_TIER1_LABELS, CONTENT_SCHEMA_TYPES
├── requirements.txt
├── Dockerfile
├── .env.example
├── README.md
└── claude.md
```

## API Contract

```
POST /crawl    — accepts {"url": "..."}, returns CrawlResponse
GET  /health   — returns {"status": "ok", "models_loaded": true/false}
```

Response shape defined in `schemas.py: CrawlResponse`. Key nested models: `MetadataResponse`, `ContentResponse`, `ClassificationResponse`.

Error handling: unhandled exceptions return HTTP 200 with `{"status": "error", "error": "<message>"}` — intentional for batch processing (HTTP layer should not retry).

---

## Module Specifications

### fetcher.py

Primary HTTP fetch via `curl_cffi.requests.AsyncSession`. Always attempted first (~200ms).

- Signature: `async fetch(url: str, session: AsyncSession, robots_cache: RobotsCache | None = None) -> CrawlResult`
- Config: timeout 15s, `impersonate="chrome120"`, `max_redirects=5`, `FETCH_HEADERS` (Chrome 120 security headers from `config.py`)
- Pre-fetch: checks robots.txt compliance (returns error if disallowed)
- Post-fetch: enforces `MAX_RESPONSE_SIZE` (10MB) — two-layer guard via Content-Length header then actual body size
- Tracks `resolved_url` after redirects. Returns structured error for 4xx/5xx.

### robots.py

robots.txt compliance layer. Checks if crawling is allowed before fetching.

- Uses Python stdlib `urllib.robotparser.RobotFileParser`
- In-memory per-domain cache with TTL (`ROBOTS_CACHE_TTL` = 3600s)
- User-agent: `ROBOTS_USER_AGENT` ("CrawlCore/1.0")
- Fetches robots.txt via the shared `AsyncSession` (reuses TLS fingerprint)
- On fetch failure (timeout, 5xx): defaults to allowing crawl

### detector.py

Determines if page needs JS rendering. Signature: `analyze(html: str, url: str) -> FetchAnalysis`.

| Signal | Threshold | Result |
|--------|-----------|--------|
| Body text length | < `MIN_BODY_LENGTH` (200 chars) | Suspicious |
| Content elements (`p`, `h1-3`, `article`, `section`) | < `MIN_CONTENT_ELEMENTS` (5) | Suspicious |
| Script-to-content ratio | > `MAX_SCRIPT_RATIO` (0.5) or >= 15 script tags | JS-heavy |
| Skeleton markers | Empty `#root`/`#app`/`#__next`, "Loading..." text | JS-heavy |

Decision: sufficient body content → `needs_js_render=False`. Empty body + high script ratio or skeleton markers → `needs_js_render=True`. Sets `meta_available=True` if `<head>` has useful title/description even when body is empty.

### renderer.py

Playwright fallback. Only called when `needs_js_render=True`. Signature: `async render_page(browser, url: str) -> str`.

- Browser launched once at startup (`app.state.browser`). Each request creates isolated context with stealth.
- Stealth config uses dynamically detected `JS_LOCALE` and `JS_TIMEZONE_ID` (from `config.py`).
- Blocks images/fonts/media. Waits for `networkidle` + `JS_EXTRA_WAIT` (2s). Timeout: `JS_RENDER_TIMEOUT` (30s).
- If `meta_available=True`: only Playwright body merged with curl_cffi `<head>` metadata.

### parser.py

Extracts all metadata from HTML. Signature: `parse(html: str, url: str) -> MetadataResponse`.

- Primary: Selectolax `LexborHTMLParser`. Fallback: BS4 + lxml on parse errors.
- Extracts all fields defined in `MetadataResponse` (title, description, canonical, OG, Twitter Card, JSON-LD structured data, headings).
- All fields handle missing/malformed tags gracefully — returns `None`, never raises.

### extractor.py

Extracts clean body text. Signature: `extract(html: str, url: str) -> ContentResponse`.

- Pipeline: `_prune_html()` (narrow to `<main>`/`<article>`, remove nav/footer/aside, site-specific selectors) → trafilatura (`favor_precision=True`, `deduplicate=True`) → BS4 fallback if trafilatura returns empty.
- Calculates `word_count` and `reading_time_minutes` (200 wpm).

### classifier.py

Runs classification + keyword extraction. Signature: `async def classify(body_text: str, models: dict, metadata=None, url="", executor=None) -> ClassificationResponse`. Uses `executor` (ThreadPoolExecutor) to run BART-MNLI inference off the event loop.

**Signal-rich input:** Model receives ALL available signals as structured context: schema_types (from JSON-LD @type), url_path, og_type, title, h1, description, then body_text — truncated to `BODY_TEXT_LIMIT` (2800 chars). No static mapping bypasses; model always decides page type from full context. YAKE receives raw `body_text` only.

**1. Keywords (4-tier hybrid extraction):**

```
Priority cascade — stops when TOP_K_KEYWORDS (10) reached:

Tier 1: JSON-LD structured data
  └─ Product: name, brand, category
  └─ Article: headline, keywords array
  
Tier 2: Title + H1 (spaCy noun chunks)
  └─ Splits "Cuisinart CPT-122 Compact 2-Slice Toaster" → individual noun phrases
  └─ Fallback: regex split on separators when spaCy unavailable

Tier 3: Open Graph fields
  └─ og:title, og:description parsed into keywords
  └─ Skipped when og:type == "website" (too generic)

Tier 4: YAKE statistical (body text)
  └─ yake.KeywordExtractor(lan="en", n=2, top=10)
  └─ Only triggered when tiers 1-3 yield < TOP_K_KEYWORDS
```

**2. Page Type + Topics (always ML inference):**

Model input includes all available signals as structured context:
```
schema_types: Product, ItemPage
url_path: /dp/B009GQ034C/
og_type: product
title: ...
h1: ...
description: ...
---
[body text truncated to BODY_TEXT_LIMIT]
```

```python
# Page type: 22 candidate labels, zero-shot NLI
candidate_labels = PAGE_TYPE_LABELS  # 22 page types
hypothesis_template = "This is a {}."

# Topics: 32 single-concept labels, multi-label NLI
candidate_labels = IAB_TIER1_LABELS  # 32 flattened IAB categories
hypothesis_template = "This text is about {}."
# Return: all labels with score > TOPIC_THRESHOLD (0.75), sorted by score descending
```

**4. Summary (structured data aware, no LLM):**

```
Fallback chain for page name:
  1. Schema.org structured data → headline/name field
  2. HTML title → cleaned (suffixes stripped, truncated to 80 chars)
  3. Generic fallback

Output: f"{page_type}: {name}, about {top_3_topics}"
```

**Model loading:** All models load once during FastAPI lifespan startup. Store in `app.state`. Never reload per request. BART-MNLI is ~1.6GB.

### main.py

- Endpoints: `POST /crawl` (full pipeline), `GET /health` (model status)
- Playwright timeout → return partial curl_cffi results with error field
- CORS: `allow_origins=["*"]`

---

## Pipeline Flow

```
POST /crawl { url }
  │
  ├─ 1. fetcher.fetch(url, session, robots_cache)
  │      ├── robots.txt check            → reject if disallowed                   ~50ms (cached)
  │      ├── HTTP GET                    → CrawlResult (HTML + resolved_url)      ~200ms
  │      └── response size guard         → reject if > 10MB
  │
  ├─ 2. detector.analyze(html, url)      → FetchAnalysis
  │      │
  │      ├── needs_js_render=False        → use curl_cffi HTML as-is
  │      │
  │      └── needs_js_render=True
  │           │
  │           ├── meta_available=True     → keep <head> from curl_cffi
  │           │                             re-fetch body via Playwright           ~3-8s
  │           │                             merge: curl_cffi meta + Playwright body
  │           │
  │           └── meta_available=False    → full Playwright re-fetch              ~3-8s
  │                                         use Playwright HTML for everything
  │
  ├─ 3. parser.parse(final_html)         → metadata dict
  │
  ├─ 4. extractor.extract(final_html)    → body_text + word_count
  │
  ├─ 5. await classifier.classify(body_text, models, metadata, url, executor)
  │      ├── 4-tier keywords             → top 10 keywords                        ~5ms
  │      │   (JSON-LD → Title/H1 spaCy → OG → YAKE fallback)
  │      ├── page_type (BART-MNLI)       → label + confidence                     ~400ms
  │      │   (all signals: schema types, URL path, OG type, title, body)
  │      └── topics (BART-MNLI)          → 32 single-concept IAB labels           ~400ms
  │
  └─ 6. Assemble CrawlResponse           → return JSON
```

---

## Constraints

- No third-party crawling/classification **services** (Diffbot, ScrapingBee, import.io, etc.).
- Third-party **libraries** are allowed and encouraged.
- Models must load once at startup, not per request.
- Playwright browser must launch once at startup, not per request. Each request creates a new browser context (for stealth isolation) with a page inside it.
- All extraction must handle malformed/missing HTML without crashing.
- Playwright is a **fallback only** — never the default path. curl_cffi is always attempted first.
- Playwright page timeout: 30s max. If it times out, return partial results from curl_cffi HTML with a warning in the error field.
- Classifier input (structured signals header + body_text) sent to BART-MNLI is truncated to `BODY_TEXT_LIMIT` (2800 chars, in `config.py`). BART's tokenizer auto-truncates at 1024 tokens internally; 2800 chars gives the model more context while staying within that window.
- Response size limit: `MAX_RESPONSE_SIZE` (10MB). Pages exceeding this are dropped with an error before any processing.

---

## Test URLs

```
http://www.amazon.com/Cuisinart-CPT-122-Compact-2-SliceToaster/dp/B009GQ034C/ref=sr_1_1?s=kitchen&ie=UTF8&qid=1431620315&sr=1-1&keywords=toaster

http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/

https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai
```

**Expected behavior per test URL:**

| URL            | render_method | Why                                                       |
| -------------- | ------------- | --------------------------------------------------------- |
| Amazon product | `curl_cffi`   | Body has sufficient content (44k+ chars, 100+ content elements); detector returns `needs_js_render=False` |
| REI blog       | `curl_cffi`   | Fully server-rendered WordPress site                      |
| CNN article    | `curl_cffi`   | Server-rendered news page                                 |

---

## Deployment (Cloud Run Demo)

For public demo deployment on GCP Cloud Run, the app supports an alternative lightweight mode:

| Setting | Value | Why |
|---------|-------|-----|
| `CLASSIFIER_BACKEND=vertex` | Gemini Flash via Vertex AI | No torch/transformers needed → smaller image, no GPU |
| `GEMINI_MODEL` | `gemini-2.5-flash` (env-configurable) | Fast, cheap, good enough for demo |
| `GEMINI_TIMEOUT` | 15s | Prevents hung requests |
| `TokenAuthMiddleware` | Bearer token auth on all routes | Protects public URL from abuse |

**Vertex mode differences:**
- BART-MNLI and ThreadPoolExecutor are **not loaded** (saves ~1.6GB RAM)
- Single Gemini call returns ALL classification outputs: page_type, topics, keywords, and summary
- No 4-tier keyword extraction, no template summary — Gemini handles everything
- Gemini client is a thread-safe lazy singleton (initialized on first request)
- On Gemini failure: returns partial (SD page_type if available, empty arrays/string)
- `google-genai` package required; authenticates via GCP service account

**Dockerfile (vertex variant):** Sets `CLASSIFIER_BACKEND=vertex`, omits torch/transformers. Image ~1.5GB vs ~4GB local.
