import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from playwright.async_api import TimeoutError as PlaywrightTimeout

from app.config import LOG_FORMAT, LOG_LEVEL
from app.detector import analyze
from app.extractor import extract
from app.fetcher import fetch
from app.js_renderer import render_page
from app.parser import parse
from app.schemas import CrawlRequest, CrawlResponse

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=[
                   "*"], allow_methods=["*"], allow_headers=["*"])

# health check for crawlers


@app.get("/health")
async def health() -> dict[str, str]:
    logger.info("health check hit")
    return {"status": "ok"}


@app.post("/crawl", response_model=CrawlResponse)
async def crawl(body: CrawlRequest, request: Request) -> CrawlResponse:
    url = str(body.url)
    logger.info("POST /crawl received | url=%s", url)

    try:
        result = await fetch(url)

        status = "success" if result.error is None else "error"
        logger.info("crawl complete | status=%s url=%s", status, url)

        render_method = "httpx"
        render_reason = "default fetch via curl_cffi"
        render_error = None
        metadata = None
        content = None

        if result.error is None:
            analysis = analyze(result.html, result.resolved_url)
            html_for_parse = result.html
            html_for_extract = result.html

            if analysis.needs_js_render:
                browser = getattr(request.app.state, "browser", None)
                if browser is None:
                    logger.warning(
                        "playwright needed but browser not available | url=%s", url)
                    render_reason = analysis.reason + \
                        " (browser not initialized, using httpx)"
                else:
                    render_method = "playwright"
                    render_reason = analysis.reason
                    try:
                        rendered_html = await render_page(browser, result.resolved_url)
                        if analysis.meta_available:
                            html_for_extract = rendered_html
                        else:
                            html_for_parse = rendered_html
                            html_for_extract = rendered_html
                    except PlaywrightTimeout:
                        logger.warning("playwright timed out | url=%s", url)
                        render_method = "httpx"
                        render_reason += " (playwright timed out, using httpx)"
                        render_error = "JS rendering timed out; partial results from static HTML"
                    except Exception as exc:
                        logger.warning(
                            "playwright render failed, using httpx HTML | url=%s error=%s",
                            url, exc,
                        )
                        render_method = "httpx"
                        render_reason += " (playwright fallback failed)"
            else:
                render_reason = analysis.reason

            logger.info("starting metadata parse | url=%s", url)
            metadata = parse(html_for_parse, result.resolved_url)
            logger.info("metadata parse complete | url=%s", url)

            logger.info("starting content extraction | url=%s", url)
            content = extract(html_for_extract, result.resolved_url)
            logger.info("content extraction complete | url=%s", url)

        return CrawlResponse(
            status=status,
            url=url,
            resolved_url=result.resolved_url,
            crawled_at=datetime.now(timezone.utc).isoformat(),
            render_method=render_method,
            render_reason=render_reason,
            status_code=result.status_code,
            content_length=len(result.html),
            metadata=metadata,
            content=content,
            error=render_error or result.error,
        )

    except Exception as exc:
        logger.exception("unexpected error during crawl | url=%s", url)
        return CrawlResponse(
            status="error",
            url=url,
            resolved_url=url,
            crawled_at=datetime.now(timezone.utc).isoformat(),
            status_code=0,
            content_length=0,
            error=str(exc),
        )


# curl -X POST localhost:8000/crawl -H "Content-Type: application/json" -d '{"url": "https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"}'

# curl -X POST localhost:8000/crawl -H "Content-Type: application/json" -d '{"url": "http://www.amazon.com/Cuisinart-CPT-122-Compact-2-Slice-Toaster/dp/B009GQ034C/ref=sr_1_1?s=kitchen&ie=UTF8&qid=1431620315&sr=1-1&keywords=toaster"}'

# curl -X POST localhost:8000/crawl -H "Content-Type: application/json" -d '{"url": "http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/"}'

# curl -X POST localhost:8000/crawl -H "Content-Type: application/json" -d '{"url": "https://www.myntra.com/handbags/caprese/caprese-colourblocked-structured-sling-bag/35480213/buy"}'


# curl -X POST localhost:8000/crawl -H "Content-Type: application/json" -d '{"url": "https://www.shopmygear.com/products/gear-bravo-16-25l-medium-water-resistant-school-bag-casual-backpack-daypack-travel-backpack-kids-bag-for-boys-girls-blue-cream-copy?variant=51512016273725&country=IN&currency=INR&utm_medium=product_sync&utm_source=google&utm_content=sag_organic&utm_campaign=sag_organic&utm_source=google_ads&utm_medium=cpc&utm_campaign=ADB_Pmax_Feed_Only_All_Products_04082025&gad_source=1&gad_campaignid=22868092978&gbraid=0AAAABAKw6MCC-B8fLNUY75LlFhn5P5ubR&gclid=CjwKCAjwntHPBhAaEiwA_Xp6RquumbL85cIqi38B_hL5_gqlq-J1Kkq5feJbgnlSXh69L_ARU6KlthoCJ8IQAvD_BwE"}'
