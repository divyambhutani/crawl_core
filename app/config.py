# ── HTTP ──
REQUEST_TIMEOUT = 15
MAX_REDIRECTS = 5
IMPERSONATE = "chrome120"

# ── Extractor ──
READING_SPEED_WPM = 200

PRUNE_SELECTORS = [
    # User-generated content
    ".reviews", ".review", ".comments", ".comment", ".ratings",
    "[data-component='reviews']", "[data-hook='review']",
    # Cross-sell / related
    ".related-products", ".recommendations", ".a-carousel",
    # Boilerplate
    ".cookie-banner", ".newsletter-signup",
    "[role='complementary']",
    # Navigation noise
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

# ── Detector ──
MIN_BODY_LENGTH = 200
MIN_CONTENT_ELEMENTS = 5
MAX_SCRIPT_RATIO = 0.5

# ── JS Renderer ──
JS_RENDER_TIMEOUT = 30000
JS_EXTRA_WAIT = 2000
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720
JS_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ── Logging ──
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
