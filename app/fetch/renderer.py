import logging

from playwright_stealth import Stealth

from app.config import (
    JS_EXTRA_WAIT,
    JS_LOCALE,
    JS_RENDER_TIMEOUT,
    JS_TIMEZONE_ID,
    JS_USER_AGENT,
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTH,
)
from app.fetch.constants import BLOCKED_RESOURCES

_stealth = Stealth()

logger = logging.getLogger(__name__)


async def render_page(browser, url: str) -> str:
    """Render a JS-heavy page with Playwright and return the final HTML."""
    logger.info("starting js render | url=%s", url)

    # stealth: viewport, user-agent, locale, timezone all mimic a real desktop browser
    context = await browser.new_context(
        viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        user_agent=JS_USER_AGENT,
        locale=JS_LOCALE,
        timezone_id=JS_TIMEZONE_ID,
    )
    try:
        page = await context.new_page()
        await _stealth.apply_stealth_async(context)
        # stealth: block non-essential resources to reduce fingerprint + speed up render
        await page.route(BLOCKED_RESOURCES, lambda route: route.abort())
        await page.goto(url, wait_until="networkidle", timeout=JS_RENDER_TIMEOUT)
        await page.wait_for_timeout(JS_EXTRA_WAIT)

        html = await page.content()
        logger.info("js render complete | url=%s html_length=%d", url, len(html))
        return html
    except Exception as exc:
        logger.error("js render failed | url=%s error=%s", url, exc)
        raise
    finally:
        await context.close()
