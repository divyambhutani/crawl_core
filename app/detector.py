import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
from selectolax.lexbor import LexborHTMLParser

from app.config import MAX_SCRIPT_RATIO, MIN_BODY_LENGTH, MIN_CONTENT_ELEMENTS, MIN_SCRIPT_TAG_COUNT

logger = logging.getLogger(__name__)

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

    try:
        return _analyze_selectolax(html, url)
    except Exception as exc:
        logger.warning("selectolax analysis failed, falling back to bs4 | url=%s error=%s", url, exc)
        return _analyze_bs4(html, url)


# ── Selectolax primary path ──────────────────────────────────────────────────


def _analyze_selectolax(html: str, url: str) -> FetchAnalysis:
    tree = LexborHTMLParser(html)

    body_length = _sl_get_body_text_length(tree)
    content_count = _sl_count_content_elements(tree)
    scripts = tree.css("script")
    script_ratio = _sl_calc_script_ratio(scripts, len(html))
    has_skeleton, skeleton_detail = _sl_has_skeleton_markers(tree)
    frameworks = _has_framework_fingerprints(html)
    meta_available = _sl_check_meta_available(tree)

    return _build_result(
        body_length, content_count, len(scripts), script_ratio,
        has_skeleton, skeleton_detail, frameworks, meta_available, url,
        parser_name="selectolax",
    )


def _sl_get_body_text_length(tree: LexborHTMLParser) -> int:
    if tree.body is None:
        return 0
    return len(tree.body.text(strip=True))


def _sl_count_content_elements(tree: LexborHTMLParser) -> int:
    return len(tree.css(CONTENT_TAGS_CSS))


def _sl_calc_script_ratio(scripts: list, html_length: int) -> float:
    if not html_length:
        return 0.0
    script_size = sum(len(s.text() or "") for s in scripts)
    return round(script_size / html_length, 3)


def _sl_has_skeleton_markers(tree: LexborHTMLParser) -> tuple[bool, str]:
    for div in tree.css("div[id]"):
        div_id = div.attributes.get("id")
        if div_id in SKELETON_IDS:
            div_text = div.text(strip=True)
            if not div_text:
                return True, f'skeleton <div id="{div_id}"> (no content structure)'

    if tree.body:
        body_text = tree.body.text(strip=True)[:500]
        if LOADING_PATTERNS.match(body_text):
            return True, f"loading/JS indicator: {body_text[:80]}"

    for noscript in tree.css("noscript"):
        text = noscript.text()
        if NOSCRIPT_PATTERNS.search(text):
            return True, f"noscript requires JS: {text[:50]}"

    return False, ""


def _sl_check_meta_available(tree: LexborHTMLParser) -> bool:
    title_tag = tree.css_first("title")
    if not title_tag or len(title_tag.text(strip=True)) <= 10:
        return False

    desc_tag = tree.css_first('meta[name="description"]')
    if not desc_tag or not desc_tag.attributes.get("content"):
        return False

    return True


# ── BS4 fallback path (existing code, untouched) ────────────────────────────


def _analyze_bs4(html: str, url: str) -> FetchAnalysis:
    soup = BeautifulSoup(html, "lxml")

    body_length = _get_body_text_length(soup)
    content_count = _count_content_elements(soup)
    scripts = soup.find_all("script")
    script_ratio = _calc_script_ratio(scripts, len(html))
    has_skeleton, skeleton_detail = _has_skeleton_markers(soup)
    frameworks = _has_framework_fingerprints(html)
    meta_available = _check_meta_available(soup)

    return _build_result(
        body_length, content_count, len(scripts), script_ratio,
        has_skeleton, skeleton_detail, frameworks, meta_available, url,
        parser_name="bs4",
    )


def _get_body_text_length(soup: BeautifulSoup) -> int:
    if soup.body is None:
        return 0
    return len(soup.body.get_text(strip=True))


def _count_content_elements(soup: BeautifulSoup) -> int:
    return len(soup.find_all(list(CONTENT_TAGS)))


def _calc_script_ratio(scripts: list, html_length: int) -> float:
    if not html_length:
        return 0.0
    script_size = sum(len(s.string or "") for s in scripts)
    return round(script_size / html_length, 3)


def _has_skeleton_markers(soup: BeautifulSoup) -> tuple[bool, str]:
    for div in soup.find_all("div", id=True):
        div_id = div.get("id")
        if div_id in SKELETON_IDS:
            div_text = div.get_text(strip=True)
            div_content = sum(len(div.find_all(tag)) for tag in CONTENT_TAGS)
            if not div_text:
                return True, f"skeleton <div id=\"{div_id}\"> (no content structure)"

    if soup.body:
        body_text = soup.body.get_text(strip=True)[:500]
        if LOADING_PATTERNS.match(body_text):
            return True, f"loading/JS indicator: {body_text[:80]}"

    for noscript in soup.find_all("noscript"):
        text = noscript.get_text()
        if NOSCRIPT_PATTERNS.search(text):
            return True, f"noscript requires JS: {text[:50]}"

    return False, ""


def _check_meta_available(soup: BeautifulSoup) -> bool:
    title_tag = soup.find("title")
    if not title_tag or len(title_tag.get_text(strip=True)) <= 10:
        return False

    desc_tag = soup.find("meta", {"name": "description"})
    if not desc_tag or not desc_tag.get("content"):
        return False

    return True


# ── Shared helpers ───────────────────────────────────────────────────────────


def _has_framework_fingerprints(html: str) -> list[str]:
    found = []
    for framework, markers in FRAMEWORK_SIGNATURES.items():
        if any(marker in html for marker in markers):
            found.append(framework)
    return found


def _build_result(
    body_length: int,
    content_count: int,
    script_tag_count: int,
    script_ratio: float,
    has_skeleton: bool,
    skeleton_detail: str,
    frameworks: list[str],
    meta_available: bool,
    url: str,
    parser_name: str,
) -> FetchAnalysis:
    body_sufficient = body_length >= MIN_BODY_LENGTH and content_count >= MIN_CONTENT_ELEMENTS
    body_sparse = body_length < MIN_BODY_LENGTH
    script_heavy = script_ratio > MAX_SCRIPT_RATIO or script_tag_count >= MIN_SCRIPT_TAG_COUNT

    if body_sufficient:
        needs_js = False
        reason = f"body has {body_length} chars and {content_count} content elements"
    elif has_skeleton:
        needs_js = True
        reason = f"skeleton page detected: {skeleton_detail}"
    elif script_heavy:
        needs_js = True
        reason = f"high script ratio ({script_ratio:.2f}, {script_tag_count} tags) with insufficient content"
    elif content_count == 0 and body_length > 0:
        needs_js = True
        reason = f"body has {body_length} chars but zero content elements (nav shell)"
    elif body_sparse:
        needs_js = False
        reason = f"body is sparse ({body_length} chars) but not script-heavy (likely thin page)"
    else:
        needs_js = False
        reason = f"body has {body_length} chars and {content_count} content elements (below threshold but not skeleton)"

    if frameworks:
        reason += f" [frameworks: {', '.join(frameworks)}]"

    logger.info(
        "analysis complete (%s) | url=%s needs_js=%s meta_available=%s reason=%s",
        parser_name, url, needs_js, meta_available, reason,
    )

    return FetchAnalysis(needs_js_render=needs_js, reason=reason, meta_available=meta_available)
