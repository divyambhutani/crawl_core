import logging
import re
from urllib.parse import urlparse

import trafilatura
from bs4 import BeautifulSoup

from app.config import (
    PRUNE_SELECTORS,
    PRUNE_TAGS,
    PRUNE_XPATH,
    READING_SPEED_WPM,
    SITE_PRUNE_SELECTORS,
)
from app.schemas import ContentResponse

logger = logging.getLogger(__name__)

STRIP_TAGS = {"script", "style", "nav", "footer", "header", "aside"}


def extract(html: str, url: str) -> ContentResponse:
    """Extract clean body text from HTML with word count and reading time."""
    logger.info("starting extraction | url=%s html_length=%d", url, len(html))

    pruned = _prune_html(html, url)
    method = "trafilatura"
    body_text = _extract_trafilatura(pruned, url)

    if not body_text:
        method = "bs4_fallback"
        logger.warning("trafilatura returned empty, falling back to bs4 | url=%s", url)
        body_text = _extract_bs4_fallback(pruned, url)

    if not body_text:
        logger.warning("both extractors returned empty | url=%s", url)
        return ContentResponse(body_text="", word_count=0, reading_time_minutes=0.0)

    word_count = len(body_text.split())
    reading_time = round(word_count / READING_SPEED_WPM, 1)

    logger.info(
        "extraction complete | url=%s word_count=%d reading_time=%.1f method=%s",
        url, word_count, reading_time, method,
    )

    return ContentResponse(
        body_text=body_text,
        word_count=word_count,
        reading_time_minutes=reading_time,
    )


def _prune_html(html: str, url: str) -> str:
    """Narrow HTML to main content area and strip nav/footer/site-specific noise."""
    try:
        soup = BeautifulSoup(html, "lxml")

        # narrow to <main>/<article> if it has real content — reduces noise for trafilatura
        main = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.find("article")
        if main and len(main.get_text(strip=True)) > 200:
            head = soup.find("head")
            narrowed = BeautifulSoup("<html></html>", "lxml")
            html_tag = narrowed.find("html")
            if head and html_tag:
                html_tag.append(head.extract())
            if html_tag:
                html_tag.append(main.extract())
            logger.info("narrowed to <%s> container | url=%s", main.name, url)
            soup = narrowed

        for tag_name in PRUNE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        for selector in PRUNE_SELECTORS:
            for el in soup.select(selector):
                el.decompose()

        # site-specific pruning (e.g., Amazon review sections)
        hostname = urlparse(url).hostname or ""
        for domain_pattern, selectors in SITE_PRUNE_SELECTORS.items():
            if domain_pattern in hostname:
                for selector in selectors:
                    for el in soup.select(selector):
                        el.decompose()

        logger.info("pruned html | url=%s output_length=%d", url, len(str(soup)))
        return str(soup)
    except Exception as exc:
        logger.warning("prune failed, using original html | url=%s error=%s", url, exc)
        return html


def _extract_trafilatura(html: str, url: str) -> str:
    """Run trafilatura content extraction with precision mode."""
    try:
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            deduplicate=True,
            prune_xpath=PRUNE_XPATH,
        )
        if text:
            logger.info("trafilatura extracted %d chars | url=%s", len(text), url)
            return text
        return ""
    except Exception as exc:
        logger.warning("trafilatura error, will try fallback | url=%s error=%s", url, exc)
        return ""


def _extract_bs4_fallback(html: str, url: str) -> str:
    """Strip non-content tags and return body text as plain string (BS4 fallback)."""
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all(STRIP_TAGS):
            tag.decompose()

        if not soup.body:
            logger.warning("no <body> tag found | url=%s", url)
            return ""

        text = soup.body.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()

        if text:
            logger.info("bs4 fallback extracted %d chars | url=%s", len(text), url)
        return text
    except Exception as exc:
        logger.warning("bs4 fallback error | url=%s error=%s", url, exc)
        return ""
