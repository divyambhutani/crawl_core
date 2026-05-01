from pydantic import BaseModel, HttpUrl


class CrawlRequest(BaseModel):
    url: HttpUrl


class OpenGraphResponse(BaseModel):
    og_title: str | None = None
    og_description: str | None = None
    og_image: str | None = None
    og_type: str | None = None
    og_site_name: str | None = None


class TwitterCardResponse(BaseModel):
    twitter_card: str | None = None
    twitter_title: str | None = None
    twitter_description: str | None = None
    twitter_image: str | None = None


class MetadataResponse(BaseModel):
    title: str | None = None
    description: str | None = None
    canonical_url: str | None = None
    language: str | None = None
    favicon: str | None = None
    open_graph: OpenGraphResponse | None = None
    twitter_card: TwitterCardResponse | None = None
    structured_data: list = []
    headings: dict[str, list[str]] = {"h1": [], "h2": []}


class ContentResponse(BaseModel):
    body_text: str
    word_count: int
    reading_time_minutes: float


class TopicScore(BaseModel):
    topic: str
    relevance_score: float


class Entity(BaseModel):
    text: str
    label: str


class ClassificationResponse(BaseModel):
    page_type: str
    page_type_confidence: float
    topics: list[TopicScore] = []
    keywords: list[str] = []
    entities: list[Entity] = []
    summary: str


class CrawlResponse(BaseModel):
    status: str
    url: str
    resolved_url: str
    crawled_at: str | None = None
    render_method: str = "httpx"
    render_reason: str = "default fetch via curl_cffi"
    status_code: int
    content_length: int
    metadata: MetadataResponse | None = None
    content: ContentResponse | None = None
    classification: ClassificationResponse | None = None
    error: str | None = None
