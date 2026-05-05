import logging
import time
from dataclasses import dataclass

from curl_cffi.requests import AsyncSession, exceptions

from app.config import MAX_REDIRECTS
from app.fetch.robots import RobotsCache, is_allowed

logger = logging.getLogger(__name__)


@dataclass
class CrawlResult:
    html: str
    resolved_url: str
    status_code: int
    error: str | None = None


async def fetch(url: str, session: AsyncSession, robots_cache: RobotsCache | None = None) -> CrawlResult:
    """Fetch a URL via curl_cffi and return HTML with status metadata."""
    logger.info("starting fetch | url=%s", url)
    start = time.monotonic()

    if robots_cache is not None:
        if not await is_allowed(url, session, robots_cache):
            logger.warning("blocked by robots.txt | url=%s", url)
            return CrawlResult(
                html="", resolved_url=url, status_code=0,
                error="robots.txt disallows crawling this URL",
            )

    try:
        logger.info("request sent, awaiting response | url=%s", url)
        response = await session.get(url)

        elapsed = round(time.monotonic() - start, 3)
        resolved = str(response.url)
        status = response.status_code
        content_length = len(response.text)

        logger.info(
            "response received | status=%d resolved_url=%s content_length=%d elapsed=%.3fs",
            status, resolved, content_length, elapsed,
        )

        if status >= 400:
            error_msg = f"HTTP {status}"
            logger.warning("client/server error | status=%d url=%s", status, url)
            return CrawlResult(
                html=response.text,
                resolved_url=resolved,
                status_code=status,
                error=error_msg,
            )

        logger.info("fetch successful | url=%s elapsed=%.3fs", url, elapsed)
        return CrawlResult(
            html=response.text,
            resolved_url=resolved,
            status_code=status,
        )

    except exceptions.ConnectTimeout:
        elapsed = round(time.monotonic() - start, 3)
        logger.error("connect timeout after %.3fs | url=%s", elapsed, url)
        return CrawlResult(html="", resolved_url=url, status_code=0, error="Connection timed out")

    except exceptions.ReadTimeout:
        elapsed = round(time.monotonic() - start, 3)
        logger.error("read timeout after %.3fs | url=%s", elapsed, url)
        return CrawlResult(html="", resolved_url=url, status_code=0, error="Read timed out")

    except exceptions.Timeout:
        elapsed = round(time.monotonic() - start, 3)
        logger.error("timeout after %.3fs | url=%s", elapsed, url)
        return CrawlResult(html="", resolved_url=url, status_code=0, error="Request timed out")

    except exceptions.ConnectionError as exc:
        elapsed = round(time.monotonic() - start, 3)
        logger.error("connection failed after %.3fs | url=%s error=%s", elapsed, url, exc)
        return CrawlResult(html="", resolved_url=url, status_code=0, error=f"Connection failed: {exc}")

    except exceptions.TooManyRedirects:
        elapsed = round(time.monotonic() - start, 3)
        logger.error("too many redirects after %.3fs | url=%s", elapsed, url)
        return CrawlResult(html="", resolved_url=url, status_code=0, error=f"Too many redirects (>{MAX_REDIRECTS})")

    except exceptions.RequestException as exc:
        elapsed = round(time.monotonic() - start, 3)
        logger.error("unexpected request error after %.3fs | url=%s error=%s", elapsed, url, exc)
        return CrawlResult(html="", resolved_url=url, status_code=0, error=str(exc))
