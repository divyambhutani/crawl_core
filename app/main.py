import logging
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import LOG_FORMAT, LOG_LEVEL
from app.fetcher import fetch
from app.parser import parse
from app.extractor import extract
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
async def crawl(request: CrawlRequest) -> CrawlResponse:
    url = str(request.url)
    logger.info("POST /crawl received | url=%s", url)

    try:
        result = await fetch(url)

        status = "success" if result.error is None else "error"
        logger.info("crawl complete | status=%s url=%s", status, url)

        metadata = None
        content = None
        if result.error is None:
            logger.info("starting metadata parse | url=%s", url)
            metadata = parse(result.html, result.resolved_url)
            logger.info("metadata parse complete | url=%s", url)

            logger.info("starting content extraction | url=%s", url)
            content = extract(result.html, result.resolved_url)
            logger.info("content extraction complete | url=%s", url)

        return CrawlResponse(
            status=status,
            url=url,
            resolved_url=result.resolved_url,
            crawled_at=datetime.now(timezone.utc).isoformat(),
            render_method="httpx",
            render_reason="default fetch via curl_cffi",
            status_code=result.status_code,
            content_length=len(result.html),
            metadata=metadata,
            content=content,
            error=result.error,
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
