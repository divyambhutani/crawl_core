import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.auth import TokenAuthMiddleware
from app.lifespan import lifespan
from app.pipeline import run_pipeline
from app.schemas import CrawlRequest, CrawlResponse

logger = logging.getLogger(__name__)

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[
                   "*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(TokenAuthMiddleware)


@app.get("/health")
async def health(request: Request) -> dict:
    """Return service status and whether all models are loaded."""
    models_loaded = all(hasattr(request.app.state, attr)
                        for attr in ("classifier", "nlp", "kw_extractor", "browser"))
    return {"status": "ok", "models_loaded": models_loaded}


@app.post("/crawl", response_model=CrawlResponse)
async def crawl(body: CrawlRequest, request: Request) -> CrawlResponse:
    """Accept a URL and run the full crawl-parse-classify pipeline."""
    url = str(body.url)
    logger.info("POST /crawl received | url=%s", url)
    return await run_pipeline(url, request.app.state)
