import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import psutil

import spacy
import yake
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright
from transformers import pipeline as hf_pipeline

from app.classifier import classify
from app.config import LOG_FORMAT, LOG_LEVEL
from app.detector import analyze
from app.extractor import extract
from app.fetcher import fetch
from app.js_renderer import render_page
from app.parser import parse
from app.schemas import CrawlRequest, CrawlResponse

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


def _get_rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_start = time.perf_counter()
    startup_rss = _get_rss_mb()
    logger.info("=== startup begin ===")

    t0 = time.perf_counter()
    rss0 = _get_rss_mb()
    app.state.classifier = hf_pipeline(
        "zero-shot-classification", model="facebook/bart-large-mnli")
    logger.info("loaded bart-mnli | time=%.2fs mem=+%.0fMB",
                time.perf_counter() - t0, _get_rss_mb() - rss0)

    t0 = time.perf_counter()
    rss0 = _get_rss_mb()
    app.state.nlp = spacy.load("en_core_web_lg")
    logger.info("loaded spacy-en | time=%.2fs mem=+%.0fMB",
                time.perf_counter() - t0, _get_rss_mb() - rss0)

    t0 = time.perf_counter()
    rss0 = _get_rss_mb()
    app.state.kw_extractor = yake.KeywordExtractor(lan="en", n=2, top=10)
    logger.info("loaded yake | time=%.2fs mem=+%.0fMB",
                time.perf_counter() - t0, _get_rss_mb() - rss0)

    t0 = time.perf_counter()
    rss0 = _get_rss_mb()
    app.state.playwright = await async_playwright().start()
    logger.info("loaded playwright-engine | time=%.2fs mem=+%.0fMB",
                time.perf_counter() - t0, _get_rss_mb() - rss0)

    t0 = time.perf_counter()
    rss0 = _get_rss_mb()
    app.state.browser = await app.state.playwright.chromium.launch(
        headless=True,
        handle_sigint=False,
        handle_sigterm=False,
        handle_sighup=False,
        args=["--no-sandbox", "--disable-dev-shm-usage"])
    logger.info("loaded chromium-browser | time=%.2fs mem=+%.0fMB",
                time.perf_counter() - t0, _get_rss_mb() - rss0)

    total_time = time.perf_counter() - startup_start
    total_mem = _get_rss_mb() - startup_rss
    logger.info("=== startup complete | total_time=%.2fs total_mem=+%.0fMB services=5 ===",
                total_time, total_mem)

    yield

    shutdown_start = time.perf_counter()
    logger.info("=== shutdown begin ===")

    t0 = time.perf_counter()
    try:
        if app.state.browser.is_connected():
            await app.state.browser.close()
            logger.info("closed chromium-browser | time=%.2fs",
                        time.perf_counter() - t0)
        else:
            logger.info(
                "chromium-browser already terminated | time=%.2fs", time.perf_counter() - t0)
    except Exception as exc:
        logger.warning("chromium-browser close failed | error=%s", exc)

    t0 = time.perf_counter()
    try:
        await app.state.playwright.stop()
        logger.info("closed playwright-engine | time=%.2fs",
                    time.perf_counter() - t0)
    except Exception as exc:
        logger.warning("playwright-engine close failed | error=%s", exc)

    logger.info("=== shutdown complete | total_time=%.2fs ===",
                time.perf_counter() - shutdown_start)


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[
                   "*"], allow_methods=["*"], allow_headers=["*"])

# health check for crawlers


@app.get("/health")
async def health(request: Request) -> dict:
    models_loaded = all(hasattr(request.app.state, attr)
                        for attr in ("classifier", "nlp", "kw_extractor", "browser"))
    return {"status": "ok", "models_loaded": models_loaded}


@app.post("/crawl", response_model=CrawlResponse)
async def crawl(body: CrawlRequest, request: Request) -> CrawlResponse:
    url = str(body.url)
    logger.info("POST /crawl received | url=%s", url)

    try:
        result = await fetch(url)

        status = "success" if result.error is None else "error"
        logger.info("crawl complete | status=%s url=%s", status, url)

        render_method = "curl_cffi"
        render_reason = "default fetch via curl_cffi"
        render_error = None
        metadata = None
        content = None
        classification = None

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
                        " (browser not initialized, using curl_cffi)"
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
                        render_method = "curl_cffi"
                        render_reason += " (playwright timed out, using curl_cffi)"
                        render_error = "JS rendering timed out; partial results from static HTML"
                    except Exception as exc:
                        logger.warning(
                            "playwright render failed, using curl_cffi HTML | url=%s error=%s",
                            url, exc,
                        )
                        render_method = "curl_cffi"
                        render_reason += " (playwright fallback failed)"
            else:
                render_reason = analysis.reason

            loop = asyncio.get_running_loop()

            logger.info("starting parse + extract (parallel) | url=%s", url)
            metadata, content = await asyncio.gather(
                loop.run_in_executor(
                    None, parse, html_for_parse, result.resolved_url),
                loop.run_in_executor(
                    None, extract, html_for_extract, result.resolved_url),
            )
            logger.info("parse + extract complete | url=%s", url)

            classification = None
            if content and content.body_text:
                models = {
                    "classifier": request.app.state.classifier,
                    "nlp": request.app.state.nlp,
                    "kw_extractor": request.app.state.kw_extractor,
                }
                classification = await classify(content.body_text, models, metadata)

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
            classification=classification,
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


# curl -X POST localhost:8000/crawl -H "Content-Type: application/json" -d '{"url": "https://www.nike.in/nike-downshifter-14-men-s-road-running-shoe/p/24928858"}'

# curl -X POST localhost:8000/crawl -H "Content-Type: application/json" -d '{"url": "https://www.airbnb.co.in/rooms/985240485996289865?check_in=2026-05-08&check_out=2026-05-10&photo_id=1744922689&source_impression_id=p3_1777804742_P3Q59jRkTBhOn8-3&previous_page_section_name=1000"}'
