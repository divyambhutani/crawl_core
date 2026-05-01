import json
import logging
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.schemas import MetadataResponse, OpenGraphResponse, TwitterCardResponse

logger = logging.getLogger(__name__)


def parse(html: str, url: str) -> MetadataResponse:
    logger.info("starting parse | url=%s html_length=%d", url, len(html))
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
        "parse complete | url=%s fields_found=%d fields_missing=%d",
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
    for rel in ("icon", "shortcut icon"):
        tag = soup.find("link", {"rel": rel})
        if tag:
            href = tag.get("href")
            if href:
                logger.info("extracted favicon | rel=%s favicon=%s", rel, href)
                return href

    parsed = urlparse(url)
    fallback = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
    logger.warning(
        "no favicon link found, using domain fallback | url=%s favicon=%s", url, fallback)
    return fallback


def _extract_open_graph(soup: BeautifulSoup, url: str) -> OpenGraphResponse | None:
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


def _extract_headings(soup: BeautifulSoup, url: str) -> dict[str, list[str]]:
    h1s = [tag.get_text(strip=True)
           for tag in soup.find_all("h1") if tag.get_text(strip=True)]
    h2s = [tag.get_text(strip=True)
           for tag in soup.find_all("h2") if tag.get_text(strip=True)]

    logger.info("extracted headings | h1_count=%d h2_count=%d",
                len(h1s), len(h2s))
    return {"h1": h1s, "h2": h2s}
