import locale as _locale
import os
from pathlib import Path

# ── Classifier Backend ──
CLASSIFIER_BACKEND = os.environ.get("CLASSIFIER_BACKEND", "local")
if CLASSIFIER_BACKEND not in ("local", "vertex"):
    raise ValueError(f"Invalid CLASSIFIER_BACKEND: {CLASSIFIER_BACKEND!r} (must be 'local' or 'vertex')")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_TIMEOUT = 15

# ── HTTP ──
REQUEST_TIMEOUT = 15
MAX_REDIRECTS = 5
IMPERSONATE = "chrome120"
FETCH_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# ── Robots.txt ──
ROBOTS_USER_AGENT = "CrawlCore/1.0"
ROBOTS_CACHE_TTL = 3600
ROBOTS_FETCH_TIMEOUT = 5

# ── Extractor ──
READING_SPEED_WPM = 200

PRUNE_SELECTORS = [
    ".reviews", ".review", ".comments", ".comment", ".ratings",
    "[data-component='reviews']", "[data-hook='review']",
    ".related-products", ".recommendations", ".a-carousel",
    ".cookie-banner", ".newsletter-signup",
    "[role='complementary']",
    ".breadcrumb", ".pagination",
]

PRUNE_TAGS = ["aside", "footer", "nav"]

SITE_PRUNE_SELECTORS: dict[str, list[str]] = {
    "amazon.": [
        "#cm_cr-review_list", "#customer-reviews", "#reviewsMedley",
        "#similarities", "#sp_detail", "#anonCarousel",
        "#navFooter", "#rhf",
        "[data-cel-widget*='review']",
        "[data-cel-widget*='similarities']",
        "[data-cel-widget*='sponsor']",
    ],
}

PRUNE_XPATH = [
    '//div[contains(@id, "review")]',
    '//section[contains(@class, "review")]',
    '//div[contains(@id, "comment")]',
    '//section[contains(@class, "comment")]',
]

# ── Classifier ──
BODY_TEXT_LIMIT = 2800
TOPIC_THRESHOLD = 0.75
TOP_K_KEYWORDS = 10

# ── Detector ──
MIN_BODY_LENGTH = 200
MIN_CONTENT_ELEMENTS = 5
MAX_SCRIPT_RATIO = 0.5
MIN_SCRIPT_TAG_COUNT = 15

# ── JS Renderer ──
JS_RENDER_TIMEOUT = 30000
JS_EXTRA_WAIT = 2000
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720
JS_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _detect_timezone() -> str:
    tz = os.environ.get("TZ")
    if tz:
        return tz
    try:
        return Path("/etc/timezone").read_text().strip()
    except (FileNotFoundError, PermissionError):
        return "America/New_York"


def _detect_locale() -> str:
    lang, _ = _locale.getlocale()
    if lang and lang not in ("C", "POSIX"):
        return lang.replace("_", "-")
    return "en-US"


JS_LOCALE = _detect_locale()
JS_TIMEZONE_ID = _detect_timezone()

# ── Logging ──
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
