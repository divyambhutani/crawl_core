import json
import logging
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from selectolax.lexbor import LexborHTMLParser

from app.schemas import MetadataResponse, OpenGraphResponse, TwitterCardResponse

logger = logging.getLogger(__name__)


def parse(html: str, url: str) -> MetadataResponse:
    """Extract all metadata (title, OG, Twitter, JSON-LD, headings) from HTML."""
    logger.info("starting parse | url=%s html_length=%d", url, len(html))
    try:
        return _parse_selectolax(html, url)
    except Exception as exc:
        logger.warning("selectolax parse failed, falling back to bs4 | url=%s error=%s", url, exc)
        return _parse_bs4(html, url)


# ── Selectolax primary path ──────────────────────────────────────────────────


def _parse_selectolax(html: str, url: str) -> MetadataResponse:
    """Parse all metadata fields using the selectolax parser."""
    tree = LexborHTMLParser(html)

    title = _sl_extract_title(tree, url)
    description = _sl_extract_description(tree, url)
    canonical_url = _sl_extract_canonical(tree, url)
    language = _sl_extract_language(tree, url)
    favicon = _sl_extract_favicon(tree, url)
    open_graph = _sl_extract_open_graph(tree, url)
    twitter_card = _sl_extract_twitter_card(tree, url)
    structured_data = _sl_extract_structured_data(tree, url)
    headings = _sl_extract_headings(tree, url)

    fields = {
        "title": title,
        "description": description,
        "canonical_url": canonical_url,
        "language": language,
        "favicon": favicon,
    }
    found = sum(1 for v in fields.values() if v is not None)
    missing = len(fields) - found

    logger.info(
        "parse complete (selectolax) | url=%s fields_found=%d fields_missing=%d",
        url, found, missing,
    )

    return MetadataResponse(
        **fields,
        open_graph=open_graph,
        twitter_card=twitter_card,
        structured_data=structured_data,
        headings=headings,
    )


def _sl_extract_title(tree: LexborHTMLParser, url: str) -> str | None:
    """Return text content of <title> tag, or None if missing."""
    tag = tree.css_first("title")
    if tag is None:
        logger.warning("title tag not found | url=%s", url)
        return None
    text = tag.text(strip=True)
    if not text:
        logger.warning("title tag found but empty | url=%s", url)
        return None
    logger.info("extracted title | title=%s", text)
    return text


def _sl_extract_description(tree: LexborHTMLParser, url: str) -> str | None:
    """Return content of meta description tag, or None if missing."""
    tag = tree.css_first('meta[name="description"]')
    if tag is None:
        logger.warning("meta description tag not found | url=%s", url)
        return None
    content = tag.attributes.get("content")
    if not content:
        logger.warning(
            "meta description tag found but content attr missing | url=%s", url)
        return None
    logger.info("extracted description | length=%d", len(content))
    return content


def _sl_extract_canonical(tree: LexborHTMLParser, url: str) -> str | None:
    """Return href of canonical link tag, or None if missing."""
    tag = tree.css_first('link[rel="canonical"]')
    if tag is None:
        logger.warning("canonical link not found | url=%s", url)
        return None
    href = tag.attributes.get("href")
    if not href:
        logger.warning(
            "canonical link found but href attr missing | url=%s", url)
        return None
    logger.info("extracted canonical_url | canonical_url=%s", href)
    return href


def _sl_extract_language(tree: LexborHTMLParser, url: str) -> str | None:
    """Return language from html[lang] or meta http-equiv, or None."""
    html_tag = tree.css_first("html")
    if html_tag:
        lang = html_tag.attributes.get("lang")
        if lang:
            logger.info(
                "extracted language from html lang attr | language=%s", lang)
            return lang

    meta_tag = tree.css_first('meta[http-equiv="content-language"]')
    if meta_tag:
        content = meta_tag.attributes.get("content")
        if content:
            logger.info(
                "extracted language from meta http-equiv | language=%s", content)
            return content

    logger.warning(
        "language not found in html tag or meta http-equiv | url=%s", url)
    return None


def _sl_extract_favicon(tree: LexborHTMLParser, url: str) -> str | None:
    """Return favicon URL from link tag, falling back to /favicon.ico."""
    for rel in ("icon", "shortcut icon"):
        tag = tree.css_first(f'link[rel="{rel}"]')
        if tag:
            href = tag.attributes.get("href")
            if href:
                if href.startswith("data:"):
                    continue
                logger.info("extracted favicon | rel=%s favicon=%s", rel, href)
                return href

    parsed = urlparse(url)
    fallback = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
    logger.warning(
        "no favicon link found, using domain fallback | url=%s favicon=%s", url, fallback)
    return fallback


def _sl_extract_open_graph(tree: LexborHTMLParser, url: str) -> OpenGraphResponse | None:
    """Collect og:* meta tags into an OpenGraphResponse."""
    og = {}
    for tag in tree.css("meta[property]"):
        prop = tag.attributes.get("property", "")
        if prop.startswith("og:"):
            content = tag.attributes.get("content")
            if content:
                og[prop] = content

    if not og:
        logger.warning("no open graph tags found | url=%s", url)
        return None

    logger.info("extracted open_graph | keys=%s", list(og.keys()))
    return OpenGraphResponse(
        og_title=og.get("og:title"),
        og_description=og.get("og:description"),
        og_image=og.get("og:image"),
        og_type=og.get("og:type"),
        og_site_name=og.get("og:site_name"),
    )


def _sl_extract_twitter_card(tree: LexborHTMLParser, url: str) -> TwitterCardResponse | None:
    """Collect twitter:* meta tags into a TwitterCardResponse."""
    tc = {}
    for tag in tree.css("meta[name]"):
        name = tag.attributes.get("name", "")
        if name.startswith("twitter:"):
            content = tag.attributes.get("content")
            if content:
                tc[name] = content

    if not tc:
        logger.warning("no twitter card tags found | url=%s", url)
        return None

    logger.info("extracted twitter_card | keys=%s", list(tc.keys()))
    return TwitterCardResponse(
        twitter_card=tc.get("twitter:card"),
        twitter_title=tc.get("twitter:title"),
        twitter_description=tc.get("twitter:description"),
        twitter_image=tc.get("twitter:image"),
    )


def _sl_extract_structured_data(tree: LexborHTMLParser, url: str) -> list[dict]:
    """Parse all JSON-LD script blocks into a list of dicts."""
    results = []
    scripts = tree.css('script[type="application/ld+json"]')

    for i, script in enumerate(scripts):
        text = script.text()
        if not text:
            logger.warning(
                "ld+json script #%d has no content | url=%s", i, url)
            continue
        try:
            data = json.loads(text)
            results.append(data)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "malformed ld+json script #%d, skipping | url=%s error=%s", i, url, exc)

    if results:
        logger.info("extracted structured_data | count=%d", len(results))
    else:
        logger.info("no ld+json structured data found | url=%s", url)
    return results


_SL_SKIP_PARENTS = {"nav", "footer", "header", "aside"}
_SL_SKIP_PATTERNS = {"skip to", "keyboard shortcut", "product summary presents"}


def _sl_extract_headings(tree: LexborHTMLParser, url: str) -> dict[str, list[str]]:
    """Extract h1-h3 text, filtering out nav/footer junk headings."""
    headings: dict[str, list[str]] = {"h1": [], "h2": [], "h3": []}
    for tag in tree.css("h1, h2, h3"):
        text = tag.text(strip=True)
        if text and not _sl_is_junk_heading(tag, text):
            headings[tag.tag].append(text)

    logger.info("extracted headings | h1=%d h2=%d h3=%d",
                len(headings["h1"]), len(headings["h2"]), len(headings["h3"]))
    return headings


def _sl_is_junk_heading(tag, text: str) -> bool:
    """Return True if heading is inside nav/footer or matches skip patterns."""
    node = tag.parent
    while node is not None:
        if node.tag in _SL_SKIP_PARENTS:
            return True
        node = node.parent
    lower = text.lower()
    return any(p in lower for p in _SL_SKIP_PATTERNS)


# ── BS4 fallback path (existing code, untouched) ────────────────────────────


def _parse_bs4(html: str, url: str) -> MetadataResponse:
    """Parse all metadata fields using BeautifulSoup (fallback path)."""
    soup = BeautifulSoup(html, "lxml")

    title = _extract_title(soup, url)
    description = _extract_description(soup, url)
    canonical_url = _extract_canonical(soup, url)
    language = _extract_language(soup, url)
    favicon = _extract_favicon(soup, url)
    open_graph = _extract_open_graph(soup, url)
    twitter_card = _extract_twitter_card(soup, url)
    structured_data = _extract_structured_data(soup, url)
    headings = _extract_headings(soup, url)

    fields = {
        "title": title,
        "description": description,
        "canonical_url": canonical_url,
        "language": language,
        "favicon": favicon,
    }
    found = sum(1 for v in fields.values() if v is not None)
    missing = len(fields) - found

    logger.info(
        "parse complete (bs4 fallback) | url=%s fields_found=%d fields_missing=%d",
        url, found, missing,
    )

    return MetadataResponse(
        **fields,
        open_graph=open_graph,
        twitter_card=twitter_card,
        structured_data=structured_data,
        headings=headings,
    )


def _extract_title(soup: BeautifulSoup, url: str) -> str | None:
    """Return text content of <title> tag, or None if missing (BS4 path)."""
    tag = soup.find("title")
    if tag is None:
        logger.warning("title tag not found | url=%s", url)
        return None
    text = tag.get_text(strip=True)
    if not text:
        logger.warning("title tag found but empty | url=%s", url)
        return None
    logger.info("extracted title | title=%s", text)
    return text


def _extract_description(soup: BeautifulSoup, url: str) -> str | None:
    """Return content of meta description tag, or None if missing (BS4 path)."""
    tag = soup.find("meta", {"name": "description"})
    if tag is None:
        logger.warning("meta description tag not found | url=%s", url)
        return None
    content = tag.get("content")
    if not content:
        logger.warning(
            "meta description tag found but content attr missing | url=%s", url)
        return None
    logger.info("extracted description | length=%d", len(content))
    return content


def _extract_canonical(soup: BeautifulSoup, url: str) -> str | None:
    """Return href of canonical link tag, or None if missing (BS4 path)."""
    tag = soup.find("link", {"rel": "canonical"})
    if tag is None:
        logger.warning("canonical link not found | url=%s", url)
        return None
    href = tag.get("href")
    if not href:
        logger.warning(
            "canonical link found but href attr missing | url=%s", url)
        return None
    logger.info("extracted canonical_url | canonical_url=%s", href)
    return href


def _extract_language(soup: BeautifulSoup, url: str) -> str | None:
    """Return language from html[lang] or meta http-equiv, or None (BS4 path)."""
    html_tag = soup.find("html")
    if html_tag:
        lang = html_tag.get("lang")
        if lang:
            logger.info(
                "extracted language from html lang attr | language=%s", lang)
            return lang

    meta_tag = soup.find("meta", {"http-equiv": "content-language"})
    if meta_tag:
        content = meta_tag.get("content")
        if content:
            logger.info(
                "extracted language from meta http-equiv | language=%s", content)
            return content

    logger.warning(
        "language not found in html tag or meta http-equiv | url=%s", url)
    return None


def _extract_favicon(soup: BeautifulSoup, url: str) -> str | None:
    """Return favicon URL from link tag, falling back to /favicon.ico (BS4 path)."""
    for rel in ("icon", "shortcut icon"):
        tag = soup.find("link", {"rel": rel})
        if tag:
            href = tag.get("href")
            if href:
                if href.startswith("data:"):
                    continue
                logger.info("extracted favicon | rel=%s favicon=%s", rel, href)
                return href

    parsed = urlparse(url)
    fallback = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
    logger.warning(
        "no favicon link found, using domain fallback | url=%s favicon=%s", url, fallback)
    return fallback


def _extract_open_graph(soup: BeautifulSoup, url: str) -> OpenGraphResponse | None:
    """Collect og:* meta tags into an OpenGraphResponse (BS4 path)."""
    og = {}
    for tag in soup.find_all("meta", attrs={"property": True}):
        prop = tag.get("property", "")
        if prop.startswith("og:"):
            content = tag.get("content")
            if content:
                og[prop] = content

    if not og:
        logger.warning("no open graph tags found | url=%s", url)
        return None

    logger.info("extracted open_graph | keys=%s", list(og.keys()))
    return OpenGraphResponse(
        og_title=og.get("og:title"),
        og_description=og.get("og:description"),
        og_image=og.get("og:image"),
        og_type=og.get("og:type"),
        og_site_name=og.get("og:site_name"),
    )


def _extract_twitter_card(soup: BeautifulSoup, url: str) -> TwitterCardResponse | None:
    """Collect twitter:* meta tags into a TwitterCardResponse (BS4 path)."""
    tc = {}
    for tag in soup.find_all("meta", attrs={"name": True}):
        name = tag.get("name", "")
        if name.startswith("twitter:"):
            content = tag.get("content")
            if content:
                tc[name] = content

    if not tc:
        logger.warning("no twitter card tags found | url=%s", url)
        return None

    logger.info("extracted twitter_card | keys=%s", list(tc.keys()))
    return TwitterCardResponse(
        twitter_card=tc.get("twitter:card"),
        twitter_title=tc.get("twitter:title"),
        twitter_description=tc.get("twitter:description"),
        twitter_image=tc.get("twitter:image"),
    )


def _extract_structured_data(soup: BeautifulSoup, url: str) -> list[dict]:
    """Parse all JSON-LD script blocks into a list of dicts (BS4 path)."""
    results = []
    scripts = soup.find_all("script", {"type": "application/ld+json"})

    for i, script in enumerate(scripts):
        text = script.string
        if not text:
            logger.warning(
                "ld+json script #%d has no content | url=%s", i, url)
            continue
        try:
            data = json.loads(text)
            results.append(data)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "malformed ld+json script #%d, skipping | url=%s error=%s", i, url, exc)

    if results:
        logger.info("extracted structured_data | count=%d", len(results))
    else:
        logger.info("no ld+json structured data found | url=%s", url)
    return results


_SKIP_PARENTS = {"nav", "footer", "header", "aside"}
_SKIP_PATTERNS = {"skip to", "keyboard shortcut", "product summary presents"}


def _extract_headings(soup: BeautifulSoup, url: str) -> dict[str, list[str]]:
    """Extract h1-h3 text, filtering out nav/footer junk headings (BS4 path)."""
    headings: dict[str, list[str]] = {"h1": [], "h2": [], "h3": []}
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(strip=True)
        if text and not _is_junk_heading(tag, text):
            headings[tag.name].append(text)

    logger.info("extracted headings | h1=%d h2=%d h3=%d",
                len(headings["h1"]), len(headings["h2"]), len(headings["h3"]))
    return headings


def _is_junk_heading(tag, text: str) -> bool:
    """Return True if heading is inside nav/footer or matches skip patterns (BS4 path)."""
    for parent in tag.parents:
        if parent.name in _SKIP_PARENTS:
            return True
    lower = text.lower()
    return any(p in lower for p in _SKIP_PATTERNS)
