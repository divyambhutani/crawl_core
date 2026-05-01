import logging

from app.config import JS_EXTRA_WAIT, JS_RENDER_TIMEOUT, JS_USER_AGENT, VIEWPORT_HEIGHT, VIEWPORT_WIDTH

logger = logging.getLogger(__name__)

BLOCKED_RESOURCES = "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,mp4,mp3}"


async def render_page(browser, url: str) -> str:
    logger.info("starting js render | url=%s", url)

    page = await browser.new_page(
        viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        user_agent=JS_USER_AGENT,
    )
    try:
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
        await page.close()
