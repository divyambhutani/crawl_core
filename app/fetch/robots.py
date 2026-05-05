"""robots.txt compliance layer with in-memory TTL cache."""

import logging
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from curl_cffi.requests import AsyncSession

from app.config import ROBOTS_CACHE_TTL, ROBOTS_FETCH_TIMEOUT, ROBOTS_USER_AGENT

logger = logging.getLogger(__name__)


class RobotsCache:
    """Per-origin cache of parsed RobotFileParser instances with TTL expiry."""

    def __init__(self, ttl: int = ROBOTS_CACHE_TTL) -> None:
        self._ttl = ttl
        self._cache: dict[str, tuple[RobotFileParser, float]] = {}

    def get(self, origin: str) -> RobotFileParser | None:
        entry = self._cache.get(origin)
        if entry is None:
            return None
        parser, expiry = entry
        if time.monotonic() > expiry:
            del self._cache[origin]
            return None
        return parser

    def put(self, origin: str, parser: RobotFileParser) -> None:
        self._cache[origin] = (parser, time.monotonic() + self._ttl)


def _origin(url: str) -> str:
    """Extract scheme://host[:port] origin, stripping any credentials."""
    parsed = urlparse(url)
    host = parsed.hostname or parsed.netloc
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


async def _fetch_robots_txt(origin: str, session: AsyncSession) -> RobotFileParser:
    """Fetch and parse robots.txt. Returns allow-all parser on any failure (RFC 9309)."""
    robots_url = f"{origin}/robots.txt"
    parser = RobotFileParser()

    try:
        response = await session.get(robots_url, timeout=ROBOTS_FETCH_TIMEOUT)

        if response.status_code >= 500:
            logger.warning("robots.txt server error (HTTP %d) | origin=%s", response.status_code, origin)
            parser.allow_all = True
            return parser

        if response.status_code >= 400:
            logger.info("robots.txt not found (HTTP %d) | origin=%s", response.status_code, origin)
            parser.allow_all = True
            return parser

        parser.parse(response.text.splitlines())
        logger.info("robots.txt parsed | origin=%s", origin)
        return parser

    except Exception as exc:
        logger.warning("robots.txt fetch failed, allowing crawl | origin=%s error=%s", origin, exc)
        parser.allow_all = True
        return parser


async def is_allowed(url: str, session: AsyncSession, cache: RobotsCache) -> bool:
    """Check whether ROBOTS_USER_AGENT may fetch the given URL."""
    origin = _origin(url)
    parser = cache.get(origin)

    if parser is None:
        parser = await _fetch_robots_txt(origin, session)
        cache.put(origin, parser)

    allowed = parser.can_fetch(ROBOTS_USER_AGENT, url)
    if not allowed:
        logger.info("robots.txt disallows crawling | url=%s user_agent=%s", url, ROBOTS_USER_AGENT)
    return allowed
