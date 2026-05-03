import asyncio
import logging

from app.schemas import ClassificationResponse, Entity, MetadataResponse, TopicScore

logger = logging.getLogger(__name__)

PAGE_TYPE_LABELS = [
    "product page", "blog post", "news article",
    "landing page", "documentation", "forum discussion", "other",
]

TOPIC_LABELS = [
    "technology", "business", "health", "science", "sports",
    "entertainment", "politics", "education", "food & cooking",
    "travel", "fashion", "finance", "real estate", "automotive",
    "home & garden", "outdoor recreation", "electronics",
    "software", "artificial intelligence", "e-commerce",
]

TOPIC_THRESHOLD = 0.3
BODY_TEXT_LIMIT = 512
ENTITY_TEXT_LIMIT = 10000
ENTITY_LABELS = {"ORG", "PERSON", "GPE", "MONEY", "DATE", "PRODUCT"}
METADATA_PREFIX_BUDGET = 200


def _build_classifier_text(body_text: str, metadata: MetadataResponse | None) -> str:
    if not metadata:
        return body_text
    parts = []
    if metadata.title:
        parts.append(metadata.title.strip())
    if metadata.description:
        parts.append(metadata.description.strip())
    if not parts:
        return body_text
    prefix = " | ".join(parts)
    if len(prefix) > METADATA_PREFIX_BUDGET:
        prefix = prefix[:METADATA_PREFIX_BUDGET].rsplit(" ", 1)[0]
    return f"{prefix}\n{body_text}"


async def classify(
    body_text: str, models: dict, metadata: MetadataResponse | None = None,
) -> ClassificationResponse:
    logger.info("starting classification | text_length=%d", len(body_text))
    loop = asyncio.get_running_loop()

    classifier_text = _build_classifier_text(body_text, metadata)

    keywords_future = loop.run_in_executor(
        None, _extract_keywords, body_text, models["kw_extractor"])
    classification_future = loop.run_in_executor(
        None, _classify_page_and_topics, classifier_text, models["classifier"])
    entities_future = loop.run_in_executor(
        None, _extract_entities, body_text, models["nlp"])

    keywords, (page_type, confidence, topics), entities = await asyncio.gather(
        keywords_future, classification_future, entities_future)

    summary = _build_summary(page_type, topics, entities)

    logger.info(
        "classification complete | page_type=%s confidence=%.2f topics=%d keywords=%d entities=%d",
        page_type, confidence, len(topics), len(keywords), len(entities),
    )

    return ClassificationResponse(
        page_type=page_type,
        page_type_confidence=round(confidence, 3),
        topics=topics,
        keywords=keywords,
        entities=entities,
        summary=summary,
    )


def _extract_keywords(text: str, kw_extractor) -> list[str]:
    results = kw_extractor.extract_keywords(text)
    return [kw for kw, _score in sorted(results, key=lambda x: x[1])]


def _classify_page_and_topics(text: str, classifier) -> tuple[str, float, list[TopicScore]]:
    truncated = text[:BODY_TEXT_LIMIT]

    page_result = classifier(
        truncated,
        candidate_labels=PAGE_TYPE_LABELS,
        hypothesis_template="This is a {}.",
    )
    page_type = page_result["labels"][0]
    confidence = page_result["scores"][0]

    topic_result = classifier(
        truncated,
        candidate_labels=TOPIC_LABELS,
        hypothesis_template="This text is about {}.",
        multi_label=True,
    )
    topics = [
        TopicScore(topic=label, relevance_score=round(score, 3))
        for label, score in zip(topic_result["labels"], topic_result["scores"])
        if score >= TOPIC_THRESHOLD
    ]

    return page_type, confidence, topics


def _extract_entities(text: str, nlp) -> list[Entity]:
    doc = nlp(text[:ENTITY_TEXT_LIMIT])
    doc_words_lower = {t.text for t in doc if t.is_alpha and t.text.islower()}

    seen = set()
    entities = []
    for ent in doc.ents:
        if ent.label_ not in ENTITY_LABELS:
            continue
        if "\n" in ent.text:
            continue
        normalized = ent.text.strip().rstrip(",.")
        if len(normalized) < 2 or len(normalized) > 80:
            continue
        if normalized.isupper() and len(normalized) <= 4:
            continue
        if ent.label_ == "PERSON":
            alpha_tokens = [t for t in ent if t.is_alpha]
            if alpha_tokens and all(t.text.lower() in doc_words_lower for t in alpha_tokens):
                continue
        key = (normalized, ent.label_)
        if key in seen:
            continue
        seen.add(key)
        entities.append(Entity(text=normalized, label=ent.label_))
    return entities


def _build_summary(page_type: str, topics: list[TopicScore], entities: list[Entity]) -> str:
    topic_names = ", ".join(t.topic for t in topics[:3]) or "general content"
    entity_names = ", ".join(e.text for e in entities[:3])
    if entity_names:
        return f"A {page_type} about {topic_names}, featuring {entity_names}."
    return f"A {page_type} about {topic_names}."
