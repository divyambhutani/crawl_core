import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
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
    app.state.kw_extractor = yake.KeywordExtractor(lan="en", n=2, top=10)
    logger.info("loaded yake | time=%.2fs mem=+%.0fMB",
                time.perf_counter() - t0, _get_rss_mb() - rss0)

    t0 = time.perf_counter()
    rss0 = _get_rss_mb()
    app.state.nlp = spacy.load("en_core_web_sm", disable=["ner", "textcat"])
    logger.info("loaded spacy en_core_web_sm | time=%.2fs mem=+%.0fMB",
                time.perf_counter() - t0, _get_rss_mb() - rss0)

    t0 = time.perf_counter()
    rss0 = _get_rss_mb()
    app.state.playwright = await async_playwright().start()
    logger.info("loaded playwright-engine | time=%.2fs mem=+%.0fMB",
                time.perf_counter() - t0, _get_rss_mb() - rss0)

    app.state.model_executor = ThreadPoolExecutor(max_workers=1)
    logger.info("created model_executor | max_workers=1")

    t0 = time.perf_counter()
    rss0 = _get_rss_mb()
    app.state.browser = await app.state.playwright.chromium.launch(
        headless=True,
        handle_sigint=False,
        handle_sigterm=False,
        handle_sighup=False,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-first-run",
            "--disable-sync",
        ])
    logger.info("loaded chromium-browser | time=%.2fs mem=+%.0fMB",
                time.perf_counter() - t0, _get_rss_mb() - rss0)

    total_time = time.perf_counter() - startup_start
    total_mem = _get_rss_mb() - startup_rss
    logger.info("=== startup complete | total_time=%.2fs total_mem=+%.0fMB services=5 ===",
                total_time, total_mem)

    yield

    shutdown_start = time.perf_counter()
    logger.info("=== shutdown begin ===")

    app.state.model_executor.shutdown(wait=False)
    logger.info("shut down model_executor")

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
        timings = {}

        t0 = time.perf_counter()
        result = await fetch(url)
        timings["fetch"] = time.perf_counter() - t0

        status = "success" if result.error is None else "error"

        render_method = "curl_cffi"
        render_reason = "default fetch via curl_cffi"
        render_error = None
        classification_error = None
        metadata = None
        content = None
        classification = None

        if result.error is None:
            t0 = time.perf_counter()
            analysis = analyze(result.html, result.resolved_url)
            timings["detect"] = time.perf_counter() - t0
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
                        t0 = time.perf_counter()
                        rendered_html = await render_page(browser, result.resolved_url)
                        timings["render"] = time.perf_counter() - t0
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

            t0 = time.perf_counter()
            metadata, content = await asyncio.gather(
                loop.run_in_executor(
                    None, parse, html_for_parse, result.resolved_url),
                loop.run_in_executor(
                    None, extract, html_for_extract, result.resolved_url),
            )
            timings["parse+extract"] = time.perf_counter() - t0

            if content and content.body_text:
                try:
                    models = {
                        "classifier": request.app.state.classifier,
                        "kw_extractor": request.app.state.kw_extractor,
                        "nlp": request.app.state.nlp,
                    }
                    t0 = time.perf_counter()
                    classification = await classify(
                        content.body_text, models, metadata,
                        executor=request.app.state.model_executor,
                    )
                    timings["classify"] = time.perf_counter() - t0
                except Exception as exc:
                    classification_error = f"classification failed: {exc}"
                    logger.warning(
                        "classification failed, returning partial results | url=%s error=%s",
                        url, exc,
                    )
            else:
                classification_error = "classification skipped: no body text extracted"

        parts = [f"{k}={v*1000:.0f}ms" for k, v in timings.items()]
        total = sum(timings.values())
        logger.info("latency | total=%dms %s | url=%s", total * 1000, " ".join(parts), url)

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
            error="; ".join(filter(None, [render_error, classification_error, result.error])) or None,
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
