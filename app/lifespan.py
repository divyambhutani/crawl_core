import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import psutil
import spacy
import yake
from fastapi import FastAPI
from playwright.async_api import async_playwright
from transformers import pipeline as hf_pipeline

from app.config import LOG_FORMAT, LOG_LEVEL

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
