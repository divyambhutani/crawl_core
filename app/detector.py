import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from app.config import MAX_SCRIPT_RATIO, MIN_BODY_LENGTH, MIN_CONTENT_ELEMENTS

logger = logging.getLogger(__name__)

CONTENT_TAGS = {"p", "h1", "h2", "h3", "article", "section"}
SKELETON_IDS = {"root", "app", "__next"}
LOADING_PATTERNS = re.compile(
    r"^\s*(loading|please wait|enable javascript)", re.IGNORECASE
)
NOSCRIPT_PATTERNS = re.compile(
    r"(enable javascript|requires javascript)", re.IGNORECASE
)

FRAMEWORK_SIGNATURES = {
    "react": ["data-reactroot", "__NEXT_DATA__", "_reactFiber"],
    "angular": ["ng-app", "<app-root>"],
    "vue": ["data-v-", "__NUXT__", "__VUE__"],
}


@dataclass
class FetchAnalysis:
    needs_js_render: bool
    reason: str
    meta_available: bool


def analyze(html: str, url: str) -> FetchAnalysis:
    logger.info("starting analysis | url=%s html_length=%d", url, len(html))

    if len(html) < 50:
        logger.info("analysis complete | url=%s needs_js=False reason=no HTML to analyze", url)
        return FetchAnalysis(needs_js_render=False, reason="no HTML to analyze", meta_available=False)

    soup = BeautifulSoup(html, "lxml")

    body_length = _get_body_text_length(soup)
    content_count = _count_content_elements(soup)
    script_ratio = _calc_script_ratio(soup, html)
    has_skeleton, skeleton_detail = _has_skeleton_markers(soup)
    frameworks = _has_framework_fingerprints(html)
    meta_available = _check_meta_available(soup)

    body_sufficient = body_length >= MIN_BODY_LENGTH and content_count >= MIN_CONTENT_ELEMENTS
    body_sparse = body_length < MIN_BODY_LENGTH
    script_heavy = script_ratio > MAX_SCRIPT_RATIO

    if body_sufficient:
        needs_js = False
        reason = f"body has {body_length} chars and {content_count} content elements"
    elif body_sparse and script_heavy:
        needs_js = True
        reason = f"body is sparse ({body_length} chars) with high script ratio ({script_ratio:.2f})"
    elif body_sparse and has_skeleton:
        needs_js = True
        reason = f"skeleton page detected: {skeleton_detail}"
    elif body_sparse:
        needs_js = False
        reason = f"body is sparse ({body_length} chars) but not script-heavy (likely thin page)"
    else:
        needs_js = False
        reason = f"body has {body_length} chars but only {content_count} content elements (below threshold)"

    if frameworks:
        reason += f" [frameworks: {', '.join(frameworks)}]"

    logger.info(
        "analysis complete | url=%s needs_js=%s meta_available=%s reason=%s",
        url, needs_js, meta_available, reason,
    )

    return FetchAnalysis(needs_js_render=needs_js, reason=reason, meta_available=meta_available)


def _get_body_text_length(soup: BeautifulSoup) -> int:
    if soup.body is None:
        return 0
    return len(soup.body.get_text(strip=True))


def _count_content_elements(soup: BeautifulSoup) -> int:
    return sum(len(soup.find_all(tag)) for tag in CONTENT_TAGS)


def _calc_script_ratio(soup: BeautifulSoup, html: str) -> float:
    if not html:
        return 0.0
    script_size = sum(len(s.string or "") for s in soup.find_all("script"))
    return round(script_size / len(html), 3)


def _has_skeleton_markers(soup: BeautifulSoup) -> tuple[bool, str]:
    for div in soup.find_all("div", id=True):
        if div.get("id") in SKELETON_IDS and not div.get_text(strip=True):
            return True, f"empty <div id=\"{div.get('id')}\">"

    if soup.body:
        body_text = soup.body.get_text(strip=True)
        if LOADING_PATTERNS.match(body_text):
            return True, f"loading indicator: {body_text[:50]}"

    for noscript in soup.find_all("noscript"):
        text = noscript.get_text()
        if NOSCRIPT_PATTERNS.search(text):
            return True, f"noscript requires JS: {text[:50]}"

    return False, ""


def _has_framework_fingerprints(html: str) -> list[str]:
    found = []
    for framework, markers in FRAMEWORK_SIGNATURES.items():
        if any(marker in html for marker in markers):
            found.append(framework)
    return found


def _check_meta_available(soup: BeautifulSoup) -> bool:
    title_tag = soup.find("title")
    if not title_tag or len(title_tag.get_text(strip=True)) <= 10:
        return False

    desc_tag = soup.find("meta", {"name": "description"})
    if not desc_tag or not desc_tag.get("content"):
        return False

    return True
