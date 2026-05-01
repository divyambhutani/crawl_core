# CLAUDE.md — BrightEdge URL Crawler & Classifier

## Project Overview

A FastAPI service that takes a single URL, crawls the page, extracts HTML metadata, and classifies it into page type + relevant topics. Handles both server-rendered (HTML-heavy) and JS-heavy (SPA/CSR) pages using a hybrid fetch strategy — fast `httpx` first, automatic Playwright fallback when skeleton pages are detected. No third-party crawling/classification services allowed; libraries are fine.

## Tech Stack

- **Language:** Python 3.12+
- **Framework:** FastAPI (async)
- **HTTP Client:** httpx (async) — primary fetch for server-rendered pages
- **JS Rendering:** Playwright (async, Chromium) — fallback for JS-heavy/SPA pages
- **HTML Parsing:** BeautifulSoup4 (bs4) with `lxml` parser
- **Body Text Extraction:** trafilatura (with BS4 fallback)
- **Keyword Extraction:** YAKE (statistical, no model needed)
- **Page Classification:** transformers + `facebook/bart-large-mnli` (zero-shot, NLI-based)
- **NER:** spaCy `en_core_web_sm`

### Why These Choices

| Component       | Choice              | Why not alternatives                                                                                                                                              |
| --------------- | ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| HTTP client     | httpx               | Async-native, follows redirects, tracks final URL. `requests` is sync-only.                                                                                       |
| JS rendering    | Playwright          | Official Python SDK, async API, resource blocking. Selenium is slower, no async.                                                                                  |
| Body extraction | trafilatura         | Purpose-built for article/content extraction, handles diverse layouts. Raw BS4 requires manual nav/footer stripping.                                              |
| Keywords        | YAKE                | Statistical (no model download), runs in microseconds, multilingual. KeyBERT is better quality but needs sentence-transformers (~400MB).                          |
| Classification  | BART-MNLI zero-shot | Classifies into any labels at runtime without training data. No fixed taxonomy needed. Runs locally — no external API calls.                                      |
| NER             | spaCy               | Fast, lightweight (`en_core_web_sm` is 12MB), extracts ORG/PERSON/GPE/MONEY/PRODUCT. transformers NER models are heavier with marginal benefit for this use case. |

## Project Structure

```
crawl_core/
├── app/
│   ├── __init__.py
│   ├── config.py             # all constants and thresholds (no .env, pure Python)
│   ├── main.py              # FastAPI app, /crawl endpoint, lifespan management
│   ├── fetcher.py            # fetch URL via curl_cffi (primary)
│   ├── detector.py           # analyze HTML to detect JS-heavy skeleton pages
│   ├── js_renderer.py        # Playwright fallback for JS-heavy pages
│   ├── parser.py             # extract metadata from HTML (BS4)
│   ├── extractor.py          # extract clean body text (trafilatura)
│   ├── classifier.py         # page type + topic classification + NER
│   └── schemas.py            # Pydantic request/response models
├── tests/
│   ├── __init__.py
│   ├── test_fetcher.py
│   ├── test_detector.py
│   ├── test_parser.py
│   ├── test_extractor.py
│   └── test_classifier.py
├── requirements.txt
├── Dockerfile
├── .env.example
├── README.md
└── claude.md
```

## API Contract

### Endpoints

```
POST /crawl              — crawl and classify a single URL
GET  /health             — healthcheck (returns model load status)
```

### Request

```
POST /crawl
Content-Type: application/json

{
  "url": "https://example.com/page"
}
```

### Response

```json
{
    "status": "success | error",
    "url": "original url",
    "resolved_url": "final url after redirects",
    "crawled_at": "ISO 8601",
    "render_method": "httpx | playwright",
    "render_reason": "why this method was chosen",
    "metadata": {
        "title": "",
        "description": "",
        "canonical_url": "",
        "language": "",
        "favicon": "",
        "open_graph": {
            "og:title": "",
            "og:description": "",
            "og:image": "",
            "og:type": "",
            "og:site_name": ""
        },
        "twitter_card": {
            "twitter:card": "",
            "twitter:title": "",
            "twitter:description": "",
            "twitter:image": ""
        },
        "structured_data": [],
        "headings": {
            "h1": [],
            "h2": []
        }
    },
    "content": {
        "body_text": "cleaned main content",
        "word_count": 0,
        "reading_time_minutes": 0.0
    },
    "classification": {
        "page_type": "product_page | blog_post | news_article | landing_page | documentation | forum | other",
        "page_type_confidence": 0.0,
        "topics": [{ "topic": "", "relevance_score": 0.0 }],
        "keywords": [""],
        "entities": [
            {
                "text": "",
                "label": "ORG | PERSON | GPE | MONEY | DATE | PRODUCT"
            }
        ],
        "summary": ""
    },
    "error": null
}
```

---

## Module Specifications

### fetcher.py

Primary HTTP fetch. Always attempted first (~200ms).

- Use `curl_cffi.requests.AsyncSession` with:
    - Timeout: 15 seconds
    - `impersonate="chrome120"` (sets UA + TLS fingerprint)
    - `allow_redirects=True`, `max_redirects=5`
- Track final `resolved_url` from `response.url` after redirects.
- Respect HTTP status codes: return structured error for 4xx/5xx.
- Return: raw HTML string, final URL, status code.

```python
@dataclass
class CrawlResult:
    html: str
    resolved_url: str
    status_code: int
    error: str | None = None
```

### detector.py

Analyzes raw HTML from `fetcher.py` to determine if the page is a JS-heavy skeleton needing Playwright. Returns a `FetchAnalysis` dataclass.

```python
@dataclass
class FetchAnalysis:
    needs_js_render: bool       # True = trigger Playwright
    reason: str                 # human-readable explanation
    meta_available: bool        # True = <head> has useful meta even if body is empty
```

**Detection signals (check in order):**

1. **Body content length:** Extract visible text from `<body>` via `soup.body.get_text(strip=True)`. If length < 200 chars → suspicious.
2. **Content element count:** Count `<p>`, `<h1>`, `<h2>`, `<h3>`, `<article>`, `<section>` tags. If < 5 → suspicious.
3. **Script-to-content ratio:** Calculate `total_script_size / total_html_size`. If > 0.5 → JS-heavy.
4. **Skeleton markers:** Check for:
    - Empty root divs: `<div id="root"></div>`, `<div id="app"></div>`, `<div id="__next"></div>` with no text content inside
    - Loading indicators: body text starts with "Loading", "Please wait", "Enable JavaScript"
    - `<noscript>` tags containing "enable javascript" or "requires javascript"
5. **JS framework fingerprints (supporting signal, not decisive):**
    - React: `data-reactroot`, `__NEXT_DATA__`, `_reactFiber`
    - Angular: `ng-app`, `<app-root>`
    - Vue: `data-v-`, `__NUXT__`, `__VUE__`
    - Note: Next.js / Nuxt.js use SSR — framework presence alone does NOT mean skeleton. Always check body content first.
6. **Meta availability check:** Even if body is empty, check if `<head>` has meaningful `<title>` (length > 10) and `<meta name="description">`. If yes, set `meta_available=True` — we can keep `<head>` metadata from httpx and only use Playwright for body text.

**Decision logic:**

- Body has sufficient content (>= 200 chars AND >= 5 content elements) → `needs_js_render=False`
- Body is empty + script ratio > 0.5 → `needs_js_render=True`
- Body is empty + skeleton markers found → `needs_js_render=True`
- Body is sparse but not script-heavy (genuinely thin page like a 404) → `needs_js_render=False`

### js_renderer.py

Playwright-based fallback for JS-heavy pages. Only called when `detector.py` returns `needs_js_render=True`.

**Browser lifecycle:**

- Launch Chromium once during FastAPI lifespan startup. Store browser instance in `app.state.browser`.
- Each request creates a new page (tab) from the shared browser — `browser.new_page()`. This avoids the ~2s browser launch cost per request.
- Close the page after extracting HTML. Browser persists.
- Shutdown browser during FastAPI lifespan shutdown.

**Page rendering logic:**

- Block unnecessary resources to speed up rendering, abort extensions (png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,mp4,mp3)
- Extra wait for late-rendering SPAs (React hydration, etc.)

```python
async def render_page(browser, url: str) -> str:
    page = await browser.new_page()
    try:
        # Block unnecessary resources to speed up rendering
        await page.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,mp4,mp3}",
            lambda route: route.abort()
        )

        await page.goto(url, wait_until="networkidle", timeout=30000)

        # Extra wait for late-rendering SPAs (React hydration, etc.)
        await page.wait_for_timeout(2000)

        html = await page.content()  # fully rendered DOM
        return html
    finally:
        await page.close()
```

**Key settings:**

- `wait_until="networkidle"` — wait until no network requests for 500ms (JS finished fetching data).
- Additional 2s wait after networkidle — catches late-rendering frameworks.
- Block images, fonts, media — we only need HTML/text. Cuts render time ~40%.
- Timeout: 30 seconds max per page.
- Set same `User-Agent` as httpx to avoid bot detection discrepancies.
- Set viewport to `1280x720` — some sites serve different content for mobile viewports.

**Partial re-fetch optimization:**

- If `detector.meta_available=True` → keep `<head>` metadata from httpx, only use Playwright HTML for body text extraction. Merge results.
- If `detector.meta_available=False` → use Playwright HTML for everything.

### parser.py

Extracts all metadata fields from HTML using BeautifulSoup.

- Parse with `BeautifulSoup(html, "lxml")`.
- **title:** `soup.find("title").get_text(strip=True)`
- **description:** `soup.find("meta", {"name": "description"})["content"]`
- **canonical_url:** `soup.find("link", {"rel": "canonical"})["href"]`
- **language:** `soup.find("html").get("lang")` or `soup.find("meta", {"http-equiv": "content-language"})["content"]`
- **favicon:** `soup.find("link", {"rel": "icon"})["href"]` or `soup.find("link", {"rel": "shortcut icon"})["href"]`, fallback to `{scheme}://{domain}/favicon.ico`
- **open_graph:** Find all `<meta property="og:*">` tags. Extract property name and content.
- **twitter_card:** Find all `<meta name="twitter:*">` tags. Extract name and content.
- **structured_data:** Find all `<script type="application/ld+json">` tags. Parse each as JSON. Return as list. Handle malformed JSON gracefully (skip, don't crash).
- **headings:** Extract text from all `<h1>` and `<h2>` tags. Return as lists.
- **All fields must handle missing/malformed tags gracefully.** Return `None` for missing fields, never raise exceptions.

### extractor.py

Extracts clean body text from HTML.

- **Primary:** `trafilatura.extract(html, include_comments=False, include_tables=False)`
- **Fallback** (if trafilatura returns None or empty string):
    1. Parse with BS4
    2. Remove all `<script>`, `<style>`, `<nav>`, `<footer>`, `<header>`, `<aside>` tags
    3. Get remaining text: `soup.body.get_text(separator=" ", strip=True)`
    4. Collapse multiple whitespace to single spaces
- **Calculate:**
    - `word_count`: `len(body_text.split())`
    - `reading_time_minutes`: `round(word_count / 200, 1)` (200 wpm average)

### classifier.py

Runs three independent classification layers on the extracted body text. All models loaded once at startup.

**1. Keywords (YAKE) — statistical, no model:**

```python
kw_extractor = yake.KeywordExtractor(lan="en", n=2, top=10)
keywords = kw_extractor.extract_keywords(body_text)
# Returns: [("compact toaster", 0.02), ("shade dial", 0.05), ...]
# Lower score = more relevant (it's a distance metric)
# Return as list of keyword strings, sorted by score ascending
```

**2. Page Type + Topics (zero-shot classification via BART-MNLI):**

How it works internally: BART-MNLI is an NLI (Natural Language Inference) model. Zero-shot classification reframes classification as entailment — "Does the page text entail the hypothesis 'This is a product page'?" The model outputs entailment probability as the classification score.

```python
classifier = pipeline("zero-shot-classification", model="facebook/bart-large-mnli")

# Page type — single label
page_type_result = classifier(
    body_text[:512],  # truncate to keep inference fast
    candidate_labels=["product page", "blog post", "news article",
                      "landing page", "documentation", "forum discussion", "other"],
    hypothesis_template="This is a {}."
)
# Return: top label + its score as confidence

# Topics — multi label
topic_result = classifier(
    body_text[:512],
    candidate_labels=["technology", "business", "health", "science", "sports",
                      "entertainment", "politics", "education", "food & cooking",
                      "travel", "fashion", "finance", "real estate", "automotive",
                      "home & garden", "outdoor recreation", "electronics",
                      "software", "artificial intelligence", "e-commerce"],
    hypothesis_template="This text is about {}.",
    multi_label=True
)
# Return: all labels with score > 0.3, sorted by score descending
```

**3. Entities (spaCy NER):**

```python
nlp = spacy.load("en_core_web_sm")
doc = nlp(body_text[:10000])  # cap input length for performance
entities = [{"text": ent.text, "label": ent.label_}
            for ent in doc.ents
            if ent.label_ in {"ORG", "PERSON", "GPE", "MONEY", "DATE", "PRODUCT"}]
# Deduplicate by (text, label) tuple
```

**4. Summary (template-based, no LLM):**

```python
summary = f"A {page_type} about {', '.join(top_3_topics)}, featuring {', '.join(top_3_entity_texts)}."
```

**Model loading:** All models load once during FastAPI lifespan startup. Store in `app.state`. Never reload per request. BART-MNLI is ~1.6GB, spaCy `en_core_web_sm` is ~12MB.

### schemas.py

Pydantic models for request/response validation.

- `CrawlRequest`: `url: HttpUrl` (Pydantic validates URL format)
- `CrawlResponse`: matches full response JSON schema above
    - `render_method`: `Literal["httpx", "playwright"]`
    - `render_reason`: `str`
    - All optional metadata fields: `Optional[str] = None`
    - `metadata`, `content`, `classification`: nested Pydantic models, all `Optional` at top level (null when status is error)
- `MetadataResponse`, `OpenGraphResponse`, `TwitterCardResponse`, `ContentResponse`, `ClassificationResponse`, `TopicScore`, `Entity`: nested models

### main.py

Application entrypoint with lifecycle management.

- **Endpoints:**
    - `POST /crawl` — full pipeline
    - `GET /health` — returns `{"status": "ok", "models_loaded": true/false}`
- **Lifespan:** AsyncContextManager that handles startup/shutdown
- **Error handling:** Wrap entire `/crawl` pipeline in try/except. Any unhandled exception returns `{"status": "error", "error": "<message>"}` with HTTP 200 (error is in the payload, not the status code — this is intentional for batch processing where the HTTP layer should not retry).
- **Playwright timeout fallback:** If Playwright times out, return partial results from httpx HTML (head metadata if available) with `error` field set to `"JS rendering timed out; partial results from static HTML"`.
- **CORS:** Add `CORSMiddleware` with `allow_origins=["*"]` for browser testing.

---

## Pipeline Flow

```
POST /crawl { url }
  │
  ├─ 1. fetcher.fetch(url)               → CrawlResult (HTML + resolved_url)     ~200ms
  │
  ├─ 2. detector.analyze(html)           → FetchAnalysis
  │      │
  │      ├── needs_js_render=False        → use httpx HTML as-is
  │      │
  │      └── needs_js_render=True
  │           │
  │           ├── meta_available=True     → keep <head> from httpx
  │           │                             re-fetch body via Playwright           ~3-8s
  │           │                             merge: httpx meta + Playwright body
  │           │
  │           └── meta_available=False    → full Playwright re-fetch              ~3-8s
  │                                         use Playwright HTML for everything
  │
  ├─ 3. parser.parse(final_html)         → metadata dict
  │
  ├─ 4. extractor.extract(final_html)    → body_text + word_count
  │
  ├─ 5. classifier.classify(body_text)
  │      ├── YAKE keywords               → top 10 keywords                        ~5ms
  │      ├── BART-MNLI page_type         → label + confidence                     ~400ms
  │      ├── BART-MNLI topics            → ranked topic list                      ~400ms
  │      └── spaCy NER                   → entities                               ~50ms
  │
  └─ 6. Assemble CrawlResponse           → return JSON
```

**Estimated total latency:**

- Server-rendered page (httpx only): **~1.1 seconds**
- JS-heavy page (httpx + Playwright): **~4-9 seconds**
- Classification dominates latency in both cases (~850ms for two BART-MNLI calls)

---

## Constraints

- No third-party crawling/classification **services** (Diffbot, ScrapingBee, import.io, etc.).
- Third-party **libraries** are allowed and encouraged.
- Models must load once at startup, not per request.
- Playwright browser must launch once at startup, not per request. Each request creates a new page (tab).
- All extraction must handle malformed/missing HTML without crashing.
- Playwright is a **fallback only** — never the default path. httpx is always attempted first.
- Playwright page timeout: 30s max. If it times out, return partial results from httpx HTML with a warning in the error field.
- `body_text` sent to BART-MNLI is truncated to 512 tokens to keep inference fast. Body text sent to spaCy is capped at 10,000 characters.

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
| Amazon product | `playwright`  | Body is JS-rendered; `<head>` has meta (partial re-fetch) |
| REI blog       | `httpx`       | Fully server-rendered WordPress site                      |
| CNN article    | `httpx`       | Server-rendered news page                                 |

---

## requirements.txt

```
fastapi>=0.110.0
uvicorn>=0.29.0
httpx>=0.27.0
beautifulsoup4>=4.12.0
lxml>=5.1.0
trafilatura>=1.8.0
yake>=0.4.8
transformers>=4.40.0
torch>=2.2.0
spacy>=3.7.0
pydantic>=2.6.0
python-dotenv>=1.0.0
playwright>=1.42.0
```

Post-install:

```bash
python -m spacy download en_core_web_sm
playwright install chromium
playwright install-deps chromium
```

---

## Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install Playwright system dependencies (Chromium needs these)
RUN apt-get update && apt-get install -y \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libxshmfence1 libxrandr2 libxcomposite1 libxdamage1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install browser and NLP model
RUN playwright install chromium
RUN python -m spacy download en_core_web_sm

# Copy application code
COPY ./app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Memory:** Playwright Chromium ~200MB + BART-MNLI ~1.6GB + spaCy ~50MB + Python runtime ~200MB = **~2.5GB minimum, 4GB recommended**.

**Image size:** ~3-5GB (Python + PyTorch + Chromium). For production, consider multi-stage build to reduce.

---

## Lifespan Management

All heavy resources load once at startup and tear down at shutdown:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from playwright.async_api import async_playwright
from transformers import pipeline
import spacy
import yake

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──
    # 1. Load ML models
    app.state.classifier = pipeline(
        "zero-shot-classification",
        model="facebook/bart-large-mnli"
    )
    app.state.nlp = spacy.load("en_core_web_sm")
    app.state.kw_extractor = yake.KeywordExtractor(lan="en", n=2, top=10)

    # 2. Launch Playwright browser (single instance, shared across requests)
    app.state.playwright = await async_playwright().start()
    app.state.browser = await app.state.playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"]  # needed in Docker
    )

    yield

    # ── Shutdown ──
    await app.state.browser.close()
    await app.state.playwright.stop()

app = FastAPI(lifespan=lifespan)
```
