PAGE_TYPE_LABELS = [
    "product page", "blog post", "news article",
    "landing page", "documentation", "forum discussion", "other",
]

IAB_TIER1_LABELS = [
    "Arts & Entertainment", "Automotive", "Business & Finance",
    "Careers", "Education", "Family & Parenting",
    "Food & Drink", "Health & Fitness", "Hobbies & Interests",
    "Home & Garden", "Law, Government & Politics", "News",
    "Personal Finance", "Pets", "Real Estate",
    "Religion & Spirituality", "Science", "Shopping",
    "Society", "Sports", "Style & Fashion",
    "Technology & Computing", "Travel",
]

SCHEMA_TYPE_TO_PAGE_TYPE: dict[str, tuple[str, float] | None] = {
    "Product": ("product page", 1.0),
    "IndividualProduct": ("product page", 1.0),
    "ProductGroup": ("product page", 1.0),
    "ItemPage": ("product page", 0.9),
    "BlogPosting": ("blog post", 0.95),
    "LiveBlogPosting": ("blog post", 0.95),
    "Article": ("blog post", 0.9),
    "Recipe": ("blog post", 0.9),
    "NewsArticle": ("news article", 0.95),
    "ReportageNewsArticle": ("news article", 0.95),
    "TechArticle": ("documentation", 0.95),
    "HowTo": ("documentation", 0.9),
    "FAQPage": ("documentation", 0.9),
    "APIReference": ("documentation", 0.95),
    "DiscussionForumPosting": ("forum discussion", 0.95),
    "QAPage": ("forum discussion", 0.9),
    "Question": ("forum discussion", 0.9),
    "CollectionPage": ("landing page", 0.85),
    "SearchResultsPage": ("landing page", 0.85),
    "WebPage": None,
    "WebSite": None,
    "Corporation": None,
    "Organization": None,
}

PAGE_LEVEL_TYPES = {
    "BlogPosting", "LiveBlogPosting", "Article", "NewsArticle",
    "ReportageNewsArticle", "TechArticle", "Recipe", "HowTo", "FAQPage",
    "APIReference", "DiscussionForumPosting", "QAPage", "Question",
}
