"""Shared constants for the fetch layer (detector + renderer)."""

import re

CONTENT_TAGS = {"p", "h1", "h2", "h3", "article", "section"}
CONTENT_TAGS_CSS = "p, h1, h2, h3, article, section"
SKELETON_IDS = {"root", "app", "__next"}
LOADING_PATTERNS = re.compile(
    r"(loading|please wait|enable javascript|without javascript|javascript is disabled|javascript must be enabled)",
    re.IGNORECASE,
)
NOSCRIPT_PATTERNS = re.compile(
    r"(enable javascript|requires javascript|without javascript|javascript is disabled)",
    re.IGNORECASE,
)

FRAMEWORK_SIGNATURES = {
    "react": ["data-reactroot", "__NEXT_DATA__", "_reactFiber"],
    "angular": ["ng-app", "<app-root>"],
    "vue": ["data-v-", "__NUXT__", "__VUE__"],
}

# ── Renderer ──
BLOCKED_RESOURCES = "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,mp4,mp3}"
