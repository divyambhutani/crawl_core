import asyncio
import logging
import time
from datetime import datetime, timezone

from playwright.async_api import TimeoutError as PlaywrightTimeout

from app.fetch import fetch, analyze, render_page
from app.parse import parse, extract
from app.classify import classify
from app.schemas import CrawlResponse

logger = logging.getLogger(__name__)


async def run_pipeline(url: str, app_state) -> CrawlResponse:
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
                browser = getattr(app_state, "browser", None)
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
                        "classifier": app_state.classifier,
                        "kw_extractor": app_state.kw_extractor,
                        "nlp": app_state.nlp,
                    }
                    t0 = time.perf_counter()
                    classification = await classify(
                        content.body_text, models, metadata,
                        executor=app_state.model_executor,
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
