import asyncio
import json
import logging
import os
import re
import threading
from urllib.parse import urlparse

from app.classify.types import CONTENT_SCHEMA_TYPES, IAB_TIER1_LABELS, PAGE_TYPE_LABELS
_PAGE_TYPE_LOWER_MAP = {l.lower(): l for l in PAGE_TYPE_LABELS}
from app.config import (
    BODY_TEXT_LIMIT, CLASSIFIER_BACKEND, GEMINI_MODEL, GEMINI_TIMEOUT,
    TOP_K_KEYWORDS, TOPIC_THRESHOLD,
)
from app.schemas import ClassificationResponse, MetadataResponse, TopicScore

logger = logging.getLogger(__name__)

_TITLE_SUFFIX_RE = re.compile(r"\s*[|–—\-]\s*[^|–—\-]{3,30}$")
_TITLE_SPLIT_RE = re.compile(r"\s*[|–—]\s*")


def _flatten_structured_data(raw: list) -> list[dict]:
    result: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            graph = item.get("@graph")
            if isinstance(graph, list):
                for g in graph:
                    if isinstance(g, dict):
                        result.append(g)
            elif item.get("@type"):
                result.append(item)
        elif isinstance(item, list):
            result.extend(_flatten_structured_data(item))
    return result


def _extract_schema_types(structured_data: list[dict]) -> list[str]:
    types_seen: list[str] = []
    for item in structured_data:
        raw_type = item.get("@type")
        if not raw_type:
            continue
        item_types = raw_type if isinstance(raw_type, list) else [raw_type]
        for t in item_types:
            if t not in types_seen:
                types_seen.append(t)
    return types_seen


def _text_overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a_lower, b_lower = a.lower(), b.lower()
    shorter, longer = (a_lower, b_lower) if len(a_lower) <= len(b_lower) else (b_lower, a_lower)
    if shorter in longer and len(shorter) / len(longer) >= 0.7:
        return 1.0
    a_words = set(a_lower.split())
    b_words = set(b_lower.split())
    if not a_words or not b_words:
        return 0.0
    intersection = a_words & b_words
    return len(intersection) / min(len(a_words), len(b_words))


def _build_classifier_text(
    body_text: str, metadata: MetadataResponse | None, url: str,
    structured_data: list[dict] | None = None,
) -> str:
    signals: list[str] = []

    if structured_data:
        schema_types = _extract_schema_types(structured_data)
        if schema_types:
            signals.append(f"schema_types: {', '.join(schema_types)}")

    url_path = urlparse(url).path if url else ""
    if url_path and url_path != "/":
        signals.append(f"url_path: {url_path}")

    if metadata:
        og_type = (metadata.open_graph.og_type or "") if metadata.open_graph else ""
        if og_type:
            signals.append(f"og_type: {og_type}")

        title = (metadata.title or "").strip()
        if title:
            signals.append(f"title: {title}")

        h1_texts = metadata.headings.get("h1", []) if metadata.headings else []
        for h1 in h1_texts[:1]:
            h1 = h1.strip()
            if h1 and _text_overlap(h1, title) < 0.8:
                signals.append(f"h1: {h1}")

        desc = (metadata.description or "").strip()
        if desc and _text_overlap(desc, title) < 0.8:
            signals.append(f"description: {desc}")

    header = "\n".join(signals)
    if header and body_text:
        combined = f"{header}\n---\n{body_text}"
    elif header:
        combined = header
    else:
        combined = body_text

    return combined[:BODY_TEXT_LIMIT]


# ── Gemini Flash backend ──

_GEMINI_PROMPT_TEMPLATE = """\
Classify this web page using ALL the signals provided (schema types, URL path, OG type, metadata, and body text).

PAGE TYPES (pick exactly one):
{page_types}

TOPIC CATEGORIES (score each 0.0-1.0, only include if >= 0.75):
{topic_categories}

KEYWORDS: Extract up to 10 keywords/phrases that best describe the page content. Rank by relevance.

SUMMARY: Write one descriptive sentence summarizing what this page is about.

Return JSON only:
{{"page_type": "...", "page_type_confidence": 0.95, "topics": [{{"topic": "...", "relevance_score": 0.85}}], "keywords": ["keyword1", "keyword2"], "summary": "One descriptive sentence."}}

PAGE SIGNALS AND CONTENT:
{text}"""

_gemini_client = None
_gemini_lock = threading.Lock()


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        with _gemini_lock:
            if _gemini_client is None:
                from google import genai
                _gemini_client = genai.Client(
                    vertexai=True,
                    project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                    location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
                )
    return _gemini_client


async def _classify_via_gemini(
    text: str,
) -> tuple[str, float, list[TopicScore], list[str], str]:
    try:
        from google.genai import types

        client = _get_gemini_client()
        prompt = _GEMINI_PROMPT_TEMPLATE.format(
            page_types=", ".join(PAGE_TYPE_LABELS),
            topic_categories=", ".join(IAB_TIER1_LABELS),
            text=text,
        )

        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            ),
            timeout=GEMINI_TIMEOUT,
        )

        if not response.text:
            raise ValueError("Gemini returned empty response")
        result = json.loads(response.text)

        raw_page_type = result.get("page_type", "other")
        page_type = _PAGE_TYPE_LOWER_MAP.get(raw_page_type.lower(), "other")
        confidence = result.get("page_type_confidence", 0.0)

        raw_topics = result.get("topics", [])
        topics = [
            TopicScore(
                topic=t.get("topic", "unknown"),
                relevance_score=round(t.get("relevance_score", 0), 3),
            )
            for t in raw_topics
            if t.get("relevance_score", 0) >= TOPIC_THRESHOLD
        ]
        topics.sort(key=lambda t: t.relevance_score, reverse=True)

        keywords = [str(k) for k in result.get("keywords", [])][:10]
        summary = str(result.get("summary", ""))

        return page_type, confidence, topics, keywords, summary

    except Exception as exc:
        logger.warning("gemini classification failed: %s", exc)
        return "other", 0.0, [], [], ""


async def classify(
    body_text: str, models: dict, metadata: MetadataResponse | None = None,
    url: str = "", executor=None,
) -> ClassificationResponse:
    logger.info("starting classification | text_length=%d", len(body_text))
    loop = asyncio.get_running_loop()

    structured_data = _flatten_structured_data(metadata.structured_data if metadata else [])
    classifier_text = _build_classifier_text(body_text, metadata, url, structured_data)

    if CLASSIFIER_BACKEND == "vertex":
        page_type, confidence, topics, keywords, summary = await _classify_via_gemini(
            classifier_text)
    else:
        keywords_future = loop.run_in_executor(
            None, _extract_keywords_hybrid, body_text, metadata, models, structured_data)
        classification_future = loop.run_in_executor(
            executor, _classify_page_and_topics, classifier_text, models["classifier"])
        keywords, (page_type, confidence, topics) = await asyncio.gather(
            keywords_future, classification_future)
        summary = _build_summary(page_type, topics, structured_data, metadata)

    iab_categories = [t.topic for t in topics[:3]]

    logger.info(
        "classification complete | page_type=%s confidence=%.2f topics=%d keywords=%d",
        page_type, confidence, len(topics), len(keywords),
    )

    return ClassificationResponse(
        page_type=page_type,
        page_type_confidence=round(confidence, 3),
        topics=topics,
        iab_categories=iab_categories,
        keywords=keywords,
        summary=summary,
    )


def _extract_keywords_hybrid(
    body_text: str, metadata: MetadataResponse | None, models: dict,
    structured_data: list[dict] | None = None,
) -> list[str]:
    keywords: list[str] = []

    if metadata:
        sd = structured_data if structured_data is not None else _flatten_structured_data(metadata.structured_data)
        _add_tier1_jsonld(keywords, sd)
        _add_tier2_title_h1(keywords, metadata, models.get("nlp"))
        _add_tier3_og(keywords, metadata)

    if len(keywords) < TOP_K_KEYWORDS:
        _add_tier4_yake(keywords, body_text, models["kw_extractor"])

    return keywords[:TOP_K_KEYWORDS]


def _add_tier1_jsonld(keywords: list[str], structured_data: list[dict]) -> None:
    for item in structured_data:
        schema_type = item.get("@type")
        if not schema_type:
            continue
        types = schema_type if isinstance(schema_type, list) else [schema_type]
        type_set = set(types)

        if type_set & {"Product", "IndividualProduct", "ProductGroup"}:
            name = item.get("name", "")
            if isinstance(name, str) and name:
                _add_unique(keywords, name)
            brand = item.get("brand")
            if isinstance(brand, dict):
                brand_name = brand.get("name", "")
                if isinstance(brand_name, str) and brand_name and brand_name != name:
                    _add_unique(keywords, brand_name)
            elif isinstance(brand, str) and brand:
                _add_unique(keywords, brand)
            category = item.get("category")
            if isinstance(category, str) and category:
                _add_unique(keywords, category)

        if type_set & {"BlogPosting", "NewsArticle", "Article", "TechArticle"}:
            headline = item.get("headline") or item.get("name", "")
            if isinstance(headline, str) and headline:
                _add_unique(keywords, headline)

        jsonld_keywords = item.get("keywords")
        if isinstance(jsonld_keywords, str):
            for kw in jsonld_keywords.split(","):
                kw = kw.strip()
                if kw:
                    _add_unique(keywords, kw)
        elif isinstance(jsonld_keywords, list):
            for kw in jsonld_keywords:
                if isinstance(kw, str) and kw.strip():
                    _add_unique(keywords, kw.strip())


def _add_tier2_title_h1(
    keywords: list[str], metadata: MetadataResponse, nlp,
) -> None:
    texts: list[str] = []

    title = metadata.title or ""
    if title:
        segments = _TITLE_SPLIT_RE.split(title)
        texts.extend(seg.strip() for seg in segments if seg.strip())

    h1_texts = metadata.headings.get("h1", []) if metadata.headings else []
    for h1 in h1_texts[:2]:
        h1 = h1.strip()
        if h1:
            texts.append(h1)

    for text in texts:
        if nlp and len(text) > 5:
            doc = nlp(text)
            for chunk in doc.noun_chunks:
                phrase = chunk.text.strip()
                if len(phrase) > 2:
                    _add_unique(keywords, phrase)
        else:
            parts = re.split(r"\s*[,:\-]\s*", text)
            for part in parts:
                part = part.strip()
                if len(part) > 2:
                    _add_unique(keywords, part)


def _add_tier3_og(keywords: list[str], metadata: MetadataResponse) -> None:
    title = (metadata.title or "").lower()
    og = metadata.open_graph
    if not og:
        return

    if (og.og_type or "").lower() == "website":
        return
    og_title = (og.og_title or "").strip()
    if og_title and _text_overlap(og_title, title) < 0.8:
        parts = _TITLE_SPLIT_RE.split(og_title)
        for part in parts:
            part = part.strip()
            if len(part) > 2:
                _add_unique(keywords, part)

    og_desc = (og.og_description or "").strip()
    if og_desc and _text_overlap(og_desc, title) < 0.8:
        words = og_desc.split()
        if len(words) <= 15:
            _add_unique(keywords, og_desc)


def _add_tier4_yake(
    keywords: list[str], body_text: str, kw_extractor,
) -> None:
    results = kw_extractor.extract_keywords(body_text)
    sorted_results = sorted(results, key=lambda x: x[1])
    for kw, _ in sorted_results:
        if len(kw) > 2:
            _add_unique(keywords, kw)
        if len(keywords) >= TOP_K_KEYWORDS:
            break


def _add_unique(keywords: list[str], candidate: str) -> None:
    if len(keywords) >= TOP_K_KEYWORDS:
        return
    candidate_lower = candidate.lower().strip()
    if len(candidate_lower) < 3:
        return
    for existing in keywords:
        existing_lower = existing.lower()
        if candidate_lower == existing_lower:
            return
        if candidate_lower in existing_lower or existing_lower in candidate_lower:
            return
        if _text_overlap(candidate, existing) > 0.8:
            return
    keywords.append(candidate)


def _run_topic_classification(text: str, classifier) -> list[TopicScore]:
    result = classifier(
        text,
        candidate_labels=IAB_TIER1_LABELS,
        hypothesis_template="This text is about {}.",
        multi_label=True,
    )
    return [
        TopicScore(topic=label, relevance_score=round(score, 3))
        for label, score in zip(result["labels"], result["scores"])
        if score >= TOPIC_THRESHOLD
    ]


def _classify_page_and_topics(
    text: str, classifier,
) -> tuple[str, float, list[TopicScore]]:
    page_result = classifier(
        text,
        candidate_labels=PAGE_TYPE_LABELS,
        hypothesis_template="This is a {}.",
    )
    topics = _run_topic_classification(text, classifier)
    return page_result["labels"][0], page_result["scores"][0], topics


def _extract_name_from_structured_data(structured_data: list[dict]) -> str | None:
    for item in structured_data:
        raw_type = item.get("@type")
        if not raw_type:
            continue
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        if any(t in CONTENT_SCHEMA_TYPES for t in types):
            name = item.get("headline") or item.get("name")
            if name and len(name) > 3:
                return name
    return None


def _clean_title(title: str) -> str:
    cleaned = _TITLE_SUFFIX_RE.sub("", title).strip()
    if len(cleaned) > 80:
        trunc = cleaned[:80]
        last_comma = trunc.rfind(",")
        if last_comma > 30:
            cleaned = trunc[:last_comma].strip()
        else:
            last_space = trunc.rfind(" ")
            if last_space > 0:
                cleaned = trunc[:last_space].strip()
    return cleaned


def _build_summary(
    page_type: str,
    topics: list[TopicScore],
    structured_data: list[dict],
    metadata: MetadataResponse | None,
) -> str:
    topic_part = ", ".join(t.topic for t in topics[:3]) or "general content"

    sd_name = _extract_name_from_structured_data(structured_data)
    if sd_name:
        return f"{sd_name} — a {page_type} about {topic_part}."

    title = (metadata.title if metadata else None) or ""
    if title:
        cleaned = _clean_title(title)
        if cleaned and len(cleaned) > 5:
            return f"{cleaned} — a {page_type} about {topic_part}."

    return f"A {page_type} about {topic_part}."
