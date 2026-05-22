"""
Daily Recruiting Briefing — Booth MBA (Thaiz Barthelmess)

Scrapes top business, marketing, and CPG publications; generates a structured briefing
via GitHub Models (free, uses the automatic GITHUB_TOKEN in Actions); emails it; saves locally.
"""

import os
import re
import sys
import json
import time
import logging
import smtplib
import datetime
from pathlib import Path
from urllib.parse import urljoin
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from openai import OpenAI
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

GITHUB_TOKEN       = os.getenv("GITHUB_TOKEN")
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL    = os.getenv("RECIPIENT_EMAIL", "thaizbar@gmail.com")
SAVE_DIR           = Path(os.getenv("SAVE_DIR", "./daily-summaries"))
GITHUB_MODEL       = os.getenv("GITHUB_MODEL", "gpt-4o")

GITHUB_MODELS_URL  = "https://models.inference.ai.azure.com"
MAX_SCRAPED_CHARS  = 60_000

JSEARCH_API_KEY = os.getenv("JSEARCH_API_KEY", "")
JSEARCH_BASE    = "https://jsearch.p.rapidapi.com"
MD_PATH         = Path(__file__).parent.parent / "applications" / "summer-2027-opportunities.md"

# Tracks everything already sent: article URLs + concepts from sections 6-9
SEEN_FILE = Path(__file__).parent / "seen_articles.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language":           "en-US,en;q=0.9",
    "Accept-Encoding":           "gzip, deflate, br",
    "Connection":                "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ---------------------------------------------------------------------------
# Seen-content tracking (cross-run deduplication for articles AND concepts)
# ---------------------------------------------------------------------------

def load_seen() -> dict:
    """Load the full seen-content record from disk."""
    default = {
        "urls":             set(),
        "vocab_terms":      [],
        "brand_concepts":   [],
        "insights_methods": [],
        "pl_concepts":      [],
        "topics":           [],
    }
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            default["urls"]             = set(data.get("urls", []))
            default["vocab_terms"]      = data.get("vocab_terms", [])
            default["brand_concepts"]   = data.get("brand_concepts", [])
            default["insights_methods"] = data.get("insights_methods", [])
            default["pl_concepts"]      = data.get("pl_concepts", [])
            default["topics"]           = data.get("topics", [])
            log.info(
                "Loaded seen: %d URLs, %d vocab, %d brand, %d insights, %d P&L, %d topics",
                len(default["urls"]),
                len(default["vocab_terms"]),
                len(default["brand_concepts"]),
                len(default["insights_methods"]),
                len(default["pl_concepts"]),
                len(default["topics"]),
            )
        except Exception as exc:
            log.warning("Could not load seen file: %s", exc)
    return default


def save_seen(seen: dict) -> None:
    """Persist the full seen-content record to disk."""
    urls = list(seen["urls"])
    if len(urls) > 5000:
        urls = urls[-5000:]
    SEEN_FILE.write_text(
        json.dumps({
            "urls":             urls,
            "vocab_terms":      seen["vocab_terms"],
            "brand_concepts":   seen["brand_concepts"],
            "insights_methods": seen["insights_methods"],
            "pl_concepts":      seen["pl_concepts"],
            "topics":           seen["topics"],
        }, indent=2),
        encoding="utf-8",
    )
    log.info("Saved seen file: %d URLs, %d vocab, %d brand, %d insights, %d P&L, %d topics",
             len(urls), len(seen["vocab_terms"]), len(seen["brand_concepts"]),
             len(seen["insights_methods"]), len(seen["pl_concepts"]), len(seen["topics"]))


def extract_used_concepts(briefing: str) -> dict:
    """
    Parse the generated briefing to find which concept was chosen in each
    rotating section (6-9). Returns a dict with one key per section.
    """
    def find_value(text: str, label: str) -> str:
        m = re.search(rf"^{re.escape(label)}:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    def section_text(full: str, header: str, next_header: str | None = None) -> str:
        start = full.find(header)
        if start == -1:
            return ""
        end = full.find(next_header, start) if next_header else len(full)
        return full[start:end]

    s6 = section_text(briefing, "SECTION 6", "SECTION 7")
    s7 = section_text(briefing, "SECTION 7", "SECTION 8")
    s8 = section_text(briefing, "SECTION 8", "SECTION 9")
    s9 = section_text(briefing, "SECTION 9")

    return {
        "vocab_term":      find_value(s6, "TERM"),
        "brand_concept":   find_value(s7, "CONCEPT"),
        "insights_method": find_value(s8, "METHODOLOGY"),
        "pl_concept":      find_value(s9, "CONCEPT"),
    }


def extract_topics_from_briefing(briefing: str, client: OpenAI) -> list[str]:
    """Use AI to extract a list of all topics covered in the generated briefing."""
    prompt = (
        "Read this daily recruiting briefing. Extract a comprehensive list of ALL topics "
        "covered: news stories, brand moves, company announcements, industry trends, "
        "statistics, educational concepts, vocabulary terms, frameworks, and specific facts. "
        "Be specific — write 'P&G Tide Gen Z social campaign' not just 'P&G campaign'. "
        "Return ONLY a valid JSON array of strings. No explanation, no markdown.\n\n"
        + briefing[:10_000]
    )
    try:
        response = client.chat.completions.create(
            model=GITHUB_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        topics = json.loads(raw)
        if isinstance(topics, list):
            return [str(t).strip() for t in topics if t]
    except Exception as exc:
        log.warning("Topic extraction failed: %s", exc)
    return []


def _article_key(url: str, title: str) -> str:
    """Best dedup key: URL if available, otherwise normalised title."""
    if url and url.startswith("http"):
        return url
    return title.lower()[:100]

# ---------------------------------------------------------------------------
# Target companies — watched for C-suite changes and open MBA roles
# ---------------------------------------------------------------------------

TARGET_COMPANIES: dict[str, dict] = {
    "McKinsey & Company":    {"industry": "Consulting",         "track": "Consulting",       "tier": 1, "aliases": ["McKinsey"]},
    "Boston Consulting Group":{"industry": "Consulting",        "track": "Consulting",       "tier": 1, "aliases": ["BCG"]},
    "Bain & Company":        {"industry": "Consulting",         "track": "Consulting",       "tier": 1, "aliases": ["Bain"]},
    "Deloitte":              {"industry": "Consulting",         "track": "Consulting",       "tier": 1, "aliases": []},
    "Accenture":             {"industry": "Consulting",         "track": "Consulting",       "tier": 1, "aliases": ["Accenture Strategy"]},
    "Oliver Wyman":          {"industry": "Consulting",         "track": "Consulting",       "tier": 1, "aliases": []},
    "Procter & Gamble":      {"industry": "CPG",                "track": "CPG Brand",        "tier": 2, "aliases": ["P&G", "Procter and Gamble"]},
    "Unilever":              {"industry": "CPG",                "track": "CPG Brand",        "tier": 2, "aliases": []},
    "PepsiCo":               {"industry": "CPG",                "track": "CPG Brand",        "tier": 2, "aliases": ["Pepsi"]},
    "Coca-Cola":             {"industry": "CPG",                "track": "CPG Brand",        "tier": 2, "aliases": ["Coke", "Coca Cola"]},
    "Nestlé":                {"industry": "CPG",                "track": "CPG Brand",        "tier": 2, "aliases": ["Nestle"]},
    "Kraft Heinz":           {"industry": "CPG",                "track": "CPG Brand",        "tier": 2, "aliases": ["KHC"]},
    "AB InBev":              {"industry": "CPG",                "track": "General Mgmt",     "tier": 2, "aliases": ["Anheuser-Busch", "ABI"]},
    "Mars":                  {"industry": "CPG",                "track": "CPG Brand",        "tier": 2, "aliases": []},
    "Colgate-Palmolive":     {"industry": "CPG",                "track": "CPG Brand",        "tier": 2, "aliases": ["Colgate"]},
    "Kimberly-Clark":        {"industry": "CPG",                "track": "CPG Brand",        "tier": 2, "aliases": ["K-C"]},
    "General Mills":         {"industry": "CPG",                "track": "CPG Brand",        "tier": 2, "aliases": []},
    "Mondelez":              {"industry": "CPG",                "track": "CPG Brand",        "tier": 2, "aliases": ["Mondelēz", "Mondelez International"]},
    "SC Johnson":            {"industry": "CPG",                "track": "CPG Brand",        "tier": 4, "aliases": []},
    "Henkel":                {"industry": "CPG",                "track": "CPG Brand",        "tier": 4, "aliases": []},
    "The Hershey Company":   {"industry": "CPG",                "track": "CPG Brand",        "tier": 4, "aliases": ["Hershey"]},
    "Amazon":                {"industry": "Tech",               "track": "Corp Strategy",    "tier": 2, "aliases": []},
    "Google":                {"industry": "Tech",               "track": "Corp Strategy",    "tier": 2, "aliases": ["Alphabet"]},
    "Meta":                  {"industry": "Tech",               "track": "Tech Strategy",    "tier": 2, "aliases": ["Facebook"]},
    "Spotify":               {"industry": "Tech",               "track": "Tech Strategy",    "tier": 4, "aliases": []},
    "DoorDash":              {"industry": "Tech",               "track": "Growth",           "tier": 4, "aliases": []},
    "Airbnb":                {"industry": "Tech",               "track": "Finance Strategy", "tier": 4, "aliases": []},
    "Salesforce":            {"industry": "Tech",               "track": "BD Strategy",      "tier": 4, "aliases": []},
    "Cisco":                 {"industry": "Tech",               "track": "Corp Strategy",    "tier": 4, "aliases": []},
    "Microsoft":             {"industry": "Tech",               "track": "BD Strategy",      "tier": 4, "aliases": []},
    "Uber":                  {"industry": "Tech",               "track": "Growth",           "tier": 4, "aliases": []},
    "Walt Disney":           {"industry": "Consumer / Media",   "track": "Corp Strategy",    "tier": 4, "aliases": ["Disney"]},
    "L'Oreal":               {"industry": "Consumer / Beauty",  "track": "CPG Brand",        "tier": 4, "aliases": ["L'Oréal", "Loreal"]},
    "Nike":                  {"industry": "Consumer / Apparel", "track": "Corp Strategy",    "tier": 4, "aliases": []},
    "Walmart":               {"industry": "Retail",             "track": "Corp Strategy",    "tier": 4, "aliases": []},
    "Pfizer":                {"industry": "Healthcare",         "track": "Corp Strategy",    "tier": 4, "aliases": []},
    "Johnson & Johnson":     {"industry": "Healthcare",         "track": "General Mgmt",     "tier": 4, "aliases": ["J&J", "JnJ"]},
}

# Pre-built alias → canonical name lookup
_COMPANY_ALIAS_MAP: dict[str, str] = {
    alias.lower(): canonical
    for canonical, info in TARGET_COMPANIES.items()
    for alias in info.get("aliases", [])
}

# ---------------------------------------------------------------------------
# Source definitions
# ---------------------------------------------------------------------------

SOURCES = [
    # --- Core business & strategy publications ---
    {
        "name":        "Harvard Business Review",
        "url":         "https://hbr.org",
        "article_sel": "article, .stream-item, .article-card",
        "title_sel":   "h1, h2, h3, .hed",
        "text_sel":    "p, .dek, .summary",
    },
    {
        "name":        "Fortune",
        "url":         "https://fortune.com",
        "article_sel": "article, .article-card, li[data-id]",
        "title_sel":   "h1, h2, h3, .title",
        "text_sel":    "p, .excerpt",
    },
    {
        "name":        "Bloomberg Businessweek",
        "url":         "https://www.bloomberg.com/businessweek",
        "article_sel": "article, .story-list-story, [class*='story']",
        "title_sel":   "h1, h2, h3, [class*='headline']",
        "text_sel":    "p, [class*='summary'], [class*='abstract']",
    },
    {
        "name":        "The Economist",
        "url":         "https://www.economist.com",
        "article_sel": "article, [class*='article'], [class*='teaser']",
        "title_sel":   "h1, h2, h3, [class*='headline']",
        "text_sel":    "p, [class*='standfirst'], [class*='description']",
    },
    {
        "name":        "Fast Company",
        "url":         "https://www.fastcompany.com",
        "article_sel": "article, .card, .post-card",
        "title_sel":   "h1, h2, h3, .headline",
        "text_sel":    "p, .dek, .excerpt",
    },
    {
        "name":        "Inc.",
        "url":         "https://www.inc.com",
        "article_sel": "article, .article-row, .card",
        "title_sel":   "h1, h2, h3, .title",
        "text_sel":    "p, .excerpt, .description",
    },
    # --- Brand, marketing & advertising ---
    {
        "name":        "Adweek",
        "url":         "https://www.adweek.com",
        "article_sel": "article, .post-card, .river-item",
        "title_sel":   "h1, h2, h3, .entry-title",
        "text_sel":    "p, .excerpt, .entry-summary",
    },
    {
        "name":        "Marketing Week",
        "url":         "https://www.marketingweek.com/",
        "article_sel": "article, .card, .post",
        "title_sel":   "h1, h2, h3, .entry-title",
        "text_sel":    "p, .excerpt, .description",
    },
    {
        "name":        "MarketingProfs",
        "url":         "https://www.marketingprofs.com",
        "article_sel": "article, .listing-item, .content-card",
        "title_sel":   "h1, h2, h3",
        "text_sel":    "p, .description",
    },
    {
        "name":        "Marketing Brew",
        "url":         "https://www.marketingbrew.com",
        "article_sel": "article, .card, [class*='story']",
        "title_sel":   "h1, h2, h3, [class*='headline']",
        "text_sel":    "p, [class*='dek'], [class*='description']",
    },
    {
        "name":        "AMA Marketing News",
        "url":         "https://www.ama.org/marketing-news",
        "article_sel": "article, .post-item, .content-card, .views-row",
        "title_sel":   "h1, h2, h3, .field--name-title",
        "text_sel":    "p, .field--name-body, .summary",
    },
    {
        "name":        "Forbes Marketing",
        "url":         "https://www.forbes.com/marketing/",
        "article_sel": "article, .stream-item, .card-layout",
        "title_sel":   "h2, h3, [class*='title']",
        "text_sel":    "p, [class*='description']",
    },
    # --- CPG, retail & consumer goods ---
    {
        "name":        "Food Dive",
        "url":         "https://www.fooddive.com/",
        "article_sel": "article, .feed__item, .card",
        "title_sel":   "h1, h2, h3, .headline",
        "text_sel":    "p, .deck, .summary",
    },
    {
        "name":        "Supermarket News",
        "url":         "https://www.supermarketnews.com/",
        "article_sel": "article, .card, .views-row",
        "title_sel":   "h1, h2, h3, .title",
        "text_sel":    "p, .description, .summary",
    },
    {
        "name":        "Progressive Grocer",
        "url":         "https://progressivegrocer.com/",
        "article_sel": "article, .card, .views-row",
        "title_sel":   "h1, h2, h3, .title",
        "text_sel":    "p, .description, .summary",
    },
    {
        "name":        "Nielsen IQ Insights",
        "url":         "https://nielseniq.com/global/en/insights/",
        "article_sel": "article, .card, .insight-card, [class*='card']",
        "title_sel":   "h1, h2, h3, .title",
        "text_sel":    "p, .description, .excerpt",
    },
    {
        "name":        "Kantar Inspiration",
        "url":         "https://www.kantar.com/inspiration",
        "article_sel": "article, .card, .content-card, [class*='card']",
        "title_sel":   "h1, h2, h3, .title",
        "text_sel":    "p, .description, .excerpt",
    },
    {
        "name":        "Chicago Booth Kilts",
        "url":         "https://www.chicagobooth.edu/research/kilts/research-data/research-impact",
        "article_sel": "article, .research-item, .content-block, .card",
        "title_sel":   "h1, h2, h3, .title",
        "text_sel":    "p, .description, .summary",
    },
    {
        "name":        "Wired",
        "url":         "https://www.wired.com",
        "article_sel": "article, .card, [class*='SummaryItemWrapper']",
        "title_sel":   "h1, h2, h3, [class*='SummaryItemHedBase']",
        "text_sel":    "p, [class*='SummaryItemDek']",
    },
    # --- Tech & product ---
    {
        "name":        "TechCrunch",
        "url":         "https://techcrunch.com",
        "article_sel": "article, .post-block, [class*='article-card']",
        "title_sel":   "h2, h3, .post-block__title, [class*='title']",
        "text_sel":    "p, .post-block__content, [class*='excerpt']",
    },
    {
        "name":        "The Verge",
        "url":         "https://www.theverge.com",
        "article_sel": "article, [class*='duet--content-cards'], h2",
        "title_sel":   "h2, h3, [class*='title']",
        "text_sel":    "p, [class*='excerpt'], [class*='description']",
    },
    {
        "name":        "Product Hunt",
        "url":         "https://www.producthunt.com",
        "article_sel": "[data-test='post-name'], [class*='styles_item'], section",
        "title_sel":   "h3, h2, [class*='title'], [class*='name']",
        "text_sel":    "p, [class*='tagline'], [class*='description']",
    },
    # --- Podcasts (show notes / episode pages) ---
    {
        "name":        "Masters of Scale (Podcast)",
        "url":         "https://mastersofscale.com/episodes/",
        "article_sel": "article, .episode-card, .post, [class*='episode']",
        "title_sel":   "h1, h2, h3, .episode-title, [class*='title']",
        "text_sel":    "p, .episode-description, [class*='description']",
    },
    {
        "name":        "How I Built This — NPR (Podcast)",
        "url":         "https://www.npr.org/podcasts/510313/how-i-built-this",
        "article_sel": "article, .item, [class*='story-wrap']",
        "title_sel":   "h1, h2, h3, [class*='title']",
        "text_sel":    "p, .teaser, [class*='description']",
    },
    {
        "name":        "Acquired Podcast",
        "url":         "https://www.acquired.fm/episodes",
        "article_sel": "article, .episode, [class*='episode'], li",
        "title_sel":   "h1, h2, h3, [class*='title']",
        "text_sel":    "p, [class*='description'], [class*='summary']",
    },
    {
        "name":        "Lenny's Newsletter & Podcast",
        "url":         "https://www.lennysnewsletter.com/",
        "article_sel": "article, .post-card, [class*='post']",
        "title_sel":   "h1, h2, h3, [class*='title']",
        "text_sel":    "p, [class*='description'], [class*='excerpt']",
    },
    {
        "name":        "Marketing Over Coffee (Podcast)",
        "url":         "https://www.marketingovercoffee.com/",
        "article_sel": "article, .entry, .post",
        "title_sel":   "h1, h2, h3, .entry-title",
        "text_sel":    "p, .entry-content, .excerpt",
    },
    {
        "name":        "My First Million (Podcast)",
        "url":         "https://www.mfmpod.com/",
        "article_sel": "article, .episode-item, [class*='episode'], .post",
        "title_sel":   "h1, h2, h3, [class*='title']",
        "text_sel":    "p, [class*='description'], [class*='summary']",
    },
]

# ---------------------------------------------------------------------------
# Reddit sources
# ---------------------------------------------------------------------------

REDDIT_SOURCES = [
    {"name": "Reddit r/marketing",    "url": "https://www.reddit.com/r/marketing/top.json?t=day&limit=15"},
    {"name": "Reddit r/business",     "url": "https://www.reddit.com/r/business/top.json?t=day&limit=15"},
    {"name": "Reddit r/Entrepreneur", "url": "https://www.reddit.com/r/Entrepreneur/top.json?t=day&limit=10"},
    {"name": "Reddit r/consulting",   "url": "https://www.reddit.com/r/consulting/top.json?t=day&limit=10"},
    {"name": "Reddit r/CPG",          "url": "https://www.reddit.com/r/CPG/top.json?t=week&limit=10"},
]

# ---------------------------------------------------------------------------
# Twitter / X sources via public nitter frontends
# ---------------------------------------------------------------------------

NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.1d4.us",
]

TWITTER_SOURCES = [
    {"name": "Twitter @profgalloway",      "account": "profgalloway"},
    {"name": "Twitter @lennysan",          "account": "lennysan"},
    {"name": "Twitter @markritson",        "account": "markritson"},
    {"name": "Twitter Marketing Search",   "query":   "CPG brand marketing strategy -filter:retweets"},
    {"name": "Twitter C-Suite Moves",      "query":   "CEO OR CMO OR CFO OR COO appointed OR hired OR joins brand OR CPG OR consulting -filter:retweets"},
    {"name": "Twitter MBA Hiring 2027",    "query":   "MBA intern 2027 summer hiring OR recruiting -filter:retweets"},
    {"name": "Twitter LinkedIn MBA Jobs",  "query":   "MBA intern 2027 site:linkedin.com/jobs -filter:retweets"},
]

# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _fetch(url: str, timeout: int = 20) -> BeautifulSoup | None:
    try:
        resp = SESSION.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        log.warning("  Fetch failed (%s): %s", url, exc)
        return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_items(soup: BeautifulSoup, source: dict, max_items: int = 20) -> list[dict]:
    containers = soup.select(source.get("article_sel", "article")) or [soup]
    base_url = source.get("url", "")

    items: list[dict] = []
    for container in containers[:max_items]:
        title_tag = container.select_one(source.get("title_sel", "h1,h2,h3"))
        title = _clean(title_tag.get_text()) if title_tag else ""

        url = ""
        link_tag = container.find("a", href=True)
        if link_tag:
            href = link_tag["href"]
            if href.startswith("http"):
                url = href
            elif href.startswith("/"):
                url = urljoin(base_url, href)

        text_tags = container.select(source.get("text_sel", "p"))[:3]
        text = " ".join(_clean(t.get_text()) for t in text_tags if t.get_text().strip())

        author = ""
        for pat in ["[class*='author']", "[rel='author']", ".byline", "[class*='byline']"]:
            tag = container.select_one(pat)
            if tag:
                author = _clean(tag.get_text())
                break

        date = ""
        time_tag = container.find("time")
        if time_tag:
            date = time_tag.get("datetime", "") or _clean(time_tag.get_text())
        if not date:
            for pat in ["[class*='date']", "[class*='timestamp']", "[class*='pubdate']"]:
                tag = container.select_one(pat)
                if tag:
                    date = _clean(tag.get_text())
                    break

        if title or text:
            items.append({"title": title, "text": text, "author": author, "date": date, "url": url})

    seen: set[str] = set()
    unique: list[dict] = []
    for item in items:
        key = item["title"].lower()[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def scrape_source(source: dict, seen_urls: set[str]) -> str:
    log.info("Scraping: %s", source["name"])
    soup = _fetch(source["url"])
    if soup is None:
        return f"[{source['name']}] — Could not retrieve content (network error or bot block).\n"

    items = _extract_items(soup, source)

    new_items = []
    for item in items:
        key = _article_key(item.get("url", ""), item["title"])
        if key in seen_urls:
            log.info("  Skipping (already sent): %s", item["title"][:60])
        else:
            new_items.append(item)
            seen_urls.add(key)

    if not new_items and not items:
        headings = [_clean(h.get_text()) for h in soup.find_all(["h1", "h2", "h3"])[:15]]
        new_items = [{"title": h, "text": "", "author": "", "date": "", "url": ""} for h in headings if h]
        for item in new_items:
            seen_urls.add(_article_key("", item["title"]))

    if not new_items:
        return f"[{source['name']}] — No new content since last briefing.\n"

    lines = [f"=== {source['name']} ===", f"URL: {source['url']}"]
    for i, item in enumerate(new_items, 1):
        lines.append(f"\n[{i}] {item['title']}")
        if item["author"]:
            lines.append(f"    By: {item['author']}")
        if item["date"]:
            lines.append(f"    Date: {item['date']}")
        if item["text"]:
            lines.append(f"    Preview: {item['text'][:400]}")
    lines.append("")
    return "\n".join(lines)


def scrape_reddit(source: dict, seen_urls: set[str]) -> str:
    log.info("Scraping Reddit: %s", source["name"])
    try:
        resp = SESSION.get(source["url"], timeout=20, headers={**HEADERS, "Accept": "application/json"})
        resp.raise_for_status()
        posts = resp.json().get("data", {}).get("children", [])
    except Exception as exc:
        log.warning("  Reddit fetch failed (%s): %s", source["url"], exc)
        return f"[{source['name']}] — Could not retrieve Reddit content: {exc}\n"

    lines = [f"=== {source['name']} ===", f"URL: {source['url']}"]
    added = 0
    for post in posts[:15]:
        d = post.get("data", {})
        title    = d.get("title", "")
        text     = (d.get("selftext", "") or "")[:400]
        author   = d.get("author", "")
        score    = d.get("score", 0)
        comments = d.get("num_comments", 0)
        post_url = d.get("url", "") or f"https://reddit.com{d.get('permalink', '')}"

        if not title:
            continue

        key = _article_key(post_url, title)
        if key in seen_urls:
            log.info("  Skipping Reddit (already sent): %s", title[:60])
            continue

        seen_urls.add(key)
        added += 1
        lines.append(f"\n[{added}] {title}")
        lines.append(f"    By u/{author} | {score} upvotes | {comments} comments")
        if text:
            lines.append(f"    {text}")

    if added == 0:
        return f"[{source['name']}] — No new posts since last briefing.\n"

    lines.append("")
    return "\n".join(lines)


def scrape_twitter(source: dict, seen_urls: set[str]) -> str:
    name = source["name"]
    log.info("Scraping Twitter: %s", name)
    for base in NITTER_INSTANCES:
        try:
            if "account" in source:
                url = f"{base}/{source['account']}"
            else:
                query = requests.utils.quote(source["query"])
                url = f"{base}/search?q={query}&f=tweets"
            soup = _fetch(url, timeout=15)
            if soup is None:
                continue
            tweets = soup.select(".timeline-item, .tweet-content, [class*='tweet']")
            if not tweets:
                continue
            lines = [f"=== {name} (via nitter) ===", f"URL: {url}"]
            added = 0
            for tweet in tweets[:10]:
                text_el = tweet.select_one(".tweet-content, [class*='content']")
                text = _clean(text_el.get_text()) if text_el else _clean(tweet.get_text())
                if not text or len(text) < 20:
                    continue
                key = text.lower()[:100]
                if key in seen_urls:
                    log.info("  Skipping tweet (already sent): %s", text[:60])
                    continue
                seen_urls.add(key)
                added += 1
                lines.append(f"\n[{added}] {text[:500]}")
            if added == 0:
                return f"[{name}] — No new tweets since last briefing.\n"
            lines.append("")
            return "\n".join(lines)
        except Exception as exc:
            log.warning("  Nitter instance %s failed: %s", base, exc)
            continue
    return f"[{name}] — Twitter unavailable (all nitter instances failed).\n"


def scrape_all(seen_urls: set[str]) -> str:
    blocks: list[str] = []
    for source in SOURCES:
        blocks.append(scrape_source(source, seen_urls))
        time.sleep(2)
    for source in REDDIT_SOURCES:
        blocks.append(scrape_reddit(source, seen_urls))
        time.sleep(1)
    for source in TWITTER_SOURCES:
        blocks.append(scrape_twitter(source, seen_urls))
        time.sleep(2)
    return "\n".join(blocks)

# ---------------------------------------------------------------------------
# C-Suite Radar — executive change detection + hiring search
# ---------------------------------------------------------------------------

_YEAR_2027_RE = re.compile(
    r'2027|summer\s+\'?27\b|class\s+of\s+2028|graduating\s+in\s+2028', re.IGNORECASE
)
_YEAR_PAST_RE = re.compile(
    r'202[456]|summer\s+\'?2[456]\b|class\s+of\s+202[567]', re.IGNORECASE
)
_GENERIC_URL_RE = re.compile(
    r'(linkedin\.com/jobs(?!/view/\d)|indeed\.com|glassdoor\.com|ziprecruiter\.com'
    r'|monster\.com|/careers/?$|/en/careers/?$|/jobs/?$|/en/jobs/?$|/careers/search/?$)',
    re.IGNORECASE,
)


def _load_radar_companies() -> dict[str, dict]:
    """
    Parse the 'Companies to Watch' table in the md to get companies that were
    added dynamically by previous runs, beyond the static TARGET_COMPANIES seed.
    """
    if not MD_PATH.exists():
        return {}
    extra: dict[str, dict] = {}
    try:
        in_watch = False
        for line in MD_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("## Companies to Watch"):
                in_watch = True
                continue
            if line.startswith("## ") and in_watch:
                break
            if not in_watch or not line.startswith("|") or "|---|" in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 4:
                continue
            company  = parts[1]
            industry = parts[3]
            if company and company not in ("Company",) and company not in TARGET_COMPANIES:
                extra[company] = {"industry": industry, "track": "", "tier": "", "aliases": []}
    except Exception as exc:
        log.warning("Could not parse Companies to Watch section: %s", exc)
    return extra


def _add_company_to_radar(company: str, location: str, industry: str, note: str) -> None:
    """
    Append a newly detected company to the 'Companies to Watch' table in the md.
    Called when a C-suite change is detected at a company not yet on the list.
    """
    if not MD_PATH.exists():
        return
    try:
        content = MD_PATH.read_text(encoding="utf-8")
        today   = datetime.date.today().strftime("%Y-%m-%d")
        new_row = f"| {company} | {location} | {industry} | — | Radar — {note} ({today}) |"
        # Find the last row of the Companies to Watch table (before a blank line or end of file)
        marker  = "\n## Companies to Watch"
        start   = content.find(marker)
        if start == -1:
            return
        # Walk forward to find the end of the table
        lines   = content.splitlines(keepends=True)
        in_table = False
        insert_at = len(content)
        pos = 0
        for i, line in enumerate(lines):
            pos += len(line)
            if "## Companies to Watch" in line:
                in_table = True
                continue
            if in_table and line.startswith("## "):
                insert_at = pos - len(line)
                break
            if in_table and line.startswith("|"):
                insert_at = pos
        content = content[:insert_at] + new_row + "\n" + content[insert_at:]
        MD_PATH.write_text(content, encoding="utf-8")
        log.info("Added '%s' to Companies to Watch.", company)
    except Exception as exc:
        log.warning("Failed to add company to watch list: %s", exc)


def detect_executive_changes(scraped_text: str, client: OpenAI) -> list[dict]:
    """
    Scan today's news for C-suite / senior leadership changes at any company
    in industries relevant to Thaiz: CPG, consulting, consumer tech, retail,
    healthcare, beauty. Not limited to TARGET_COMPANIES — newly discovered
    companies get added to the On Radar section and monitored going forward.

    Returns list of {company, industry, location, person, title, change, detail}.
    """
    prompt = (
        "Scan the news content below for C-suite or senior leadership changes "
        "(new hires, departures, promotions, resignations) at companies in:\n"
        "  - Management consulting (McKinsey, BCG, Bain, Deloitte, Accenture, etc.)\n"
        "  - CPG / FMCG / Food & Beverage (P&G, Unilever, PepsiCo, Nestlé, Mars, etc.)\n"
        "  - Consumer tech (Amazon, Google, Meta, Uber, DoorDash, Spotify, Airbnb, etc.)\n"
        "  - Retail and consumer brands (Walmart, Nike, Disney, Target, etc.)\n"
        "  - Healthcare / pharma with MBA programs (Pfizer, J&J, Eli Lilly, etc.)\n"
        "  - Beauty and personal care (L'Oreal, Estée Lauder, etc.)\n\n"
        "Focus on: CEO, CMO, CFO, COO, CTO, President, EVP, SVP Marketing, "
        "SVP Strategy, Chief Strategy Officer, Chief Commercial Officer.\n\n"
        "Return ONLY a valid JSON array. Each element must have:\n"
        '  "company":  company name as it appears in the news\n'
        '  "industry": one of Consulting | CPG | Tech | Retail | Healthcare | Beauty | Other\n'
        '  "location": HQ city and state if mentioned, otherwise "Unknown"\n'
        '  "person":   executive full name\n'
        '  "title":    their new or departing role\n'
        '  "change":   one of "hired" | "departed" | "promoted" | "other"\n'
        '  "detail":   one sentence with source context\n\n'
        "Return [] if no relevant changes are found.\n\n"
        "NEWS:\n" + scraped_text[:20_000]
    )
    try:
        resp = client.chat.completions.create(
            model=GITHUB_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        changes = json.loads(raw)
        if not isinstance(changes, list):
            return []
        for c in changes:
            name        = c.get("company", "").strip()
            c["company"] = _COMPANY_ALIAS_MAP.get(name.lower(), name)
        log.info("C-Suite Radar: detected %d change(s).", len(changes))
        return changes
    except Exception as exc:
        log.warning("Executive change detection failed: %s", exc)
        return []


def _check_year_on_page(url: str) -> str:
    """Returns 'confirmed_2027' | 'wrong_year' | 'no_signal' | 'unreachable'."""
    try:
        resp = SESSION.get(url, timeout=15, allow_redirects=True)
        if resp.status_code >= 400:
            return "unreachable"
        text = resp.text
        if _YEAR_2027_RE.search(text):
            return "confirmed_2027"
        if _YEAR_PAST_RE.search(text):
            return "wrong_year"
        return "no_signal"
    except Exception:
        return "unreachable"


def _is_valid_apply_link(url: str) -> bool:
    if not url or _GENERIC_URL_RE.search(url):
        return False
    m = re.search(r'https?://[^/]+(/[^?#]+)', url)
    if m:
        segs = [s for s in m.group(1).split('/') if s]
        if len(segs) >= 2 and any(re.search(r'\d', s) for s in segs):
            return True
    return False


def _jsearch_company(company_name: str) -> list[dict]:
    """Query JSearch (indexes LinkedIn + other boards) for MBA 2027 intern roles."""
    if not JSEARCH_API_KEY:
        log.info("JSearch skipped — JSEARCH_API_KEY not set.")
        return []
    try:
        resp = requests.get(
            f"{JSEARCH_BASE}/search",
            headers={
                "x-rapidapi-key": JSEARCH_API_KEY,
                "x-rapidapi-host": "jsearch.p.rapidapi.com",
            },
            params={
                "query":       f"MBA intern summer 2027 {company_name}",
                "num_results": "10",
                "date_posted": "month",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as exc:
        log.warning("JSearch failed for %s: %s", company_name, exc)
        return []


def _jsearch_to_row(job: dict, company_name: str, company_info: dict) -> dict | None:
    """Convert a JSearch result to a CSV-ready dict, or None if invalid."""
    apply_url = job.get("job_apply_link") or job.get("job_google_link", "")
    if not _is_valid_apply_link(apply_url):
        return None
    year = _check_year_on_page(apply_url)
    if year == "wrong_year":
        return None
    city     = job.get("job_city", "") or ""
    state    = job.get("job_state", "") or ""
    location = f"{city}, {state}".strip(", ") or "Multiple US Cities"
    return {
        "role":     job.get("job_title", "MBA Intern"),
        "company":  company_name,
        "location": location,
        "industry": company_info.get("industry", ""),
        "track":    company_info.get("track", ""),
        "tier":     str(company_info.get("tier", "")),
        "link":     apply_url,
        "status":   "New" if year == "confirmed_2027" else "Monitor",
    }


def _load_md_dedup_keys() -> set[str]:
    """Return 'company_lower:role_lower' pairs already in the Active Listings table."""
    if not MD_PATH.exists():
        return set()
    keys: set[str] = set()
    try:
        in_active = False
        for line in MD_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("## Active Listings"):
                in_active = True
                continue
            if line.startswith("## ") and in_active:
                break
            if not in_active or not line.startswith("|") or "|---|" in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 5:
                continue
            # Table: | Date | Role | Company | Location | Track | Tier | Status |
            role_raw = parts[2]
            company  = parts[3]
            role     = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', role_raw)
            if company and role and company not in ("Company", "Role"):
                keys.add(f"{company.lower()}:{role.lower()}")
    except Exception as exc:
        log.warning("Could not parse MD for dedup: %s", exc)
    return keys


def _append_jobs_to_md(new_jobs: list[dict]) -> int:
    """Insert new job rows into the Active Listings table in the markdown file."""
    if not new_jobs or not MD_PATH.exists():
        return 0
    existing = _load_md_dedup_keys()
    today    = datetime.date.today().strftime("%Y-%m-%d")
    rows     = []
    for job in new_jobs:
        key = f"{job['company'].lower()}:{job['role'].lower()}"
        if key in existing:
            continue
        link      = job.get("link", "")
        role_cell = f"[{job['role']}]({link})" if link else job["role"]
        rows.append(
            f"| {today} | {role_cell} | {job['company']} "
            f"| {job.get('location', 'Multiple US Cities')} "
            f"| {job.get('track', '')} | {job.get('tier', '')} "
            f"| {job.get('status', 'New')} |"
        )
        existing.add(key)
    if not rows:
        return 0
    try:
        content = MD_PATH.read_text(encoding="utf-8")
        # Insert before the "---" separator that precedes "## Companies to Watch"
        marker = "\n---\n\n## Companies to Watch"
        pos    = content.find(marker)
        if pos == -1:
            content += "\n" + "\n".join(rows) + "\n"
        else:
            content = content[:pos] + "\n" + "\n".join(rows) + "\n" + content[pos:]
        MD_PATH.write_text(content, encoding="utf-8")
        log.info("Appended %d new row(s) to opportunities MD.", len(rows))
    except Exception as exc:
        log.warning("Failed to write to MD file: %s", exc)
        return 0
    return len(rows)


def run_csuite_radar(scraped_text: str, client: OpenAI) -> tuple[list[dict], int]:
    """
    Detect C-suite changes in today's news across all relevant industries.
    For each flagged company:
      - If it's new (not yet on the radar), add it to 'On Radar' in the md.
      - Search JSearch (LinkedIn + job boards) for open MBA 2027 roles.
      - Validate year and append confirmed openings to Active Listings in the md.

    Returns (executive_changes, new_opportunities_added).
    """
    # Build the full monitoring universe: static seed + dynamically added companies
    all_companies = {**TARGET_COMPANIES, **_load_radar_companies()}

    executive_changes = detect_executive_changes(scraped_text, client)
    if not executive_changes:
        return [], 0

    flagged = {c["company"] for c in executive_changes}
    log.info("C-Suite Radar: searching hiring at %d flagged company/ies: %s", len(flagged), flagged)

    candidate_rows: list[dict] = []
    for change in executive_changes:
        company  = change["company"]
        industry = change.get("industry", "Other")
        location = change.get("location", "Unknown")

        # If this company is new, add it to On Radar so it gets monitored every day
        if company not in all_companies:
            note = f"C-suite change — {change['person']} ({change['title']}) {change['change']}"
            _add_company_to_radar(company, location, industry, note)
            all_companies[company] = {"industry": industry, "track": "", "tier": "", "aliases": []}

        info = all_companies[company]
        jobs = _jsearch_company(company)
        log.info("  JSearch → %d result(s) for %s.", len(jobs), company)
        for job in jobs:
            row = _jsearch_to_row(job, company, info)
            if row:
                candidate_rows.append(row)
        time.sleep(1)

    added = _append_jobs_to_md(candidate_rows)
    return executive_changes, added

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """\
You are building a daily recruiting briefing for Thaiz, a Peruvian MBA student at the \
University of Chicago Booth School of Business (Class of 2028). She is recruiting for \
management consulting (McKinsey, BCG, Bain, Deloitte S&O, Accenture Strategy) and \
CPG/consumer brand roles (P&G, Unilever, Kraft Heinz, Nestlé, AB InBev, Colgate). \
Her goal is to be the sharpest candidate in every room — someone who speaks fluent \
American consumer, CPG industry, and consulting strategy.

Today is {today}. Below is raw content scraped from top US business, marketing, CPG, \
and strategy sources. Use ALL sources available. Even if a source only returned headlines \
or failed, note it and extract whatever signal exists.

============================
SCRAPED CONTENT
============================
{scraped_content}
============================

============================
TOPICS ALREADY COVERED IN PREVIOUS BRIEFINGS — DO NOT REPEAT
Every item below has already been sent to Thaiz. Do NOT cover it again in any section. \
Find completely fresh angles, new examples, and different concepts throughout the email.
============================
{covered_topics}
============================

Write a structured daily briefing with the NINE sections below. Be specific: name brands, \
campaigns, dollar figures, percentages, executive names, and product names wherever \
the scraped content mentions them. Never be generic. Every sentence should be something \
she could say in an interview and impress a hiring manager or consulting recruiter.

---

## SECTION 1 — US CONSUMER PULSE
What are American consumers actually doing, feeling, and buying right now?
- Specific behaviors: what they are spending on, cutting back on, worrying about, or obsessing over.
- Generational splits (Gen Z vs. Millennial vs. Boomer) or income-level differences if mentioned.
- Shifts in trust, loyalty, or expectations toward brands.
- Any data points, surveys, or studies cited in the sources.

## SECTION 2 — BRAND & MARKETING MOVES
Which brands made notable moves — campaigns, launches, rebrands, partnerships, or decisions?
- For each move: what did the brand do, what was the strategy behind it, did it land well?
- Focus on CPG, retail, tech, and entertainment — especially: P&G, Unilever, Kraft Heinz, \
Nestlé, AB InBev, Colgate, Coca-Cola, PepsiCo, Walmart, Target, Amazon, Nike, Starbucks, \
Google, Apple, Netflix, Disney.
- Note the channel (social, TV, OOH, influencer, retail media, etc.) and target audience.

## SECTION 3 — CPG & RETAIL INDUSTRY NEWS
What is happening specifically in the CPG and retail industry today?
- Earnings, market share shifts, distribution changes, M&A, private label vs. national brand dynamics.
- Retailer moves (Walmart, Target, Kroger, Costco, Dollar General) that affect CPG brands.
- International or LATAM consumer market developments if any appear in the sources.
- Consulting implications: what strategic questions do these trends raise for MBB clients?

## SECTION 4 — TECH & PRODUCT TRENDS
What is changing in how US companies build, market, and distribute products?
- AI in marketing and advertising, retail tech, streaming and content strategy.
- Changes in measurement, attribution, or ad spend relevant for brand managers.
- Platform momentum shifts (TikTok, YouTube, Meta, retail media networks, CTV).

## SECTION 5 — INTERVIEW-READY FACTS
List exactly 5 specific, citable facts or statistics from today's sources that Thaiz \
can drop naturally in a job interview. Format each as:
  FACT [N]: [the stat or fact, with source name]
  WHY IT MATTERS: [one sentence on why a brand manager or consultant should care]

---

## SECTION 6 — CPG SECTOR VOCABULARY DRILL
ALREADY USED TERMS — do NOT pick any of these, they have already been covered: {used_vocab_terms}

Choose ONE term from the REMAINING CPG industry vocabulary that Thaiz should master. \
Choose from: category management, shelf economics, planogram, trade spend, slotting fees, \
A&P budget, household penetration, buying rate, volumetric share, share of category \
requirements, share of wallet, retailer margin, promotional lift, everyday low price (EDLP) \
vs. Hi-Lo pricing, SKU rationalization, private label threat, line extension vs. brand \
extension, innovation funnel, consumer panel data, basket analysis, cross-elasticity of \
demand, velocity (units/store/week), facings, distribution (ACV and TDP), shelf placement \
logic, promotional mechanics (BOGO, TPR, display, feature-and-display).

Format exactly as:
TERM: [term]
DEFINITION: [plain English, 2–3 sentences — no jargon inside the definition]
WHY IT MATTERS: [one sentence on why a brand manager or CPG consultant must know this cold]
REAL EXAMPLE: [name a specific real brand and a specific concrete situation where this term \
  applies — P&G, Unilever, Kraft Heinz, Coca-Cola, etc. Be specific: company, brand, year, \
  outcome if known]
INTERVIEW HOOK: [one sentence Thaiz could say in an interview to signal fluency in this term]

---

## SECTION 7 — BRAND MANAGEMENT CONCEPT OF THE DAY
ALREADY USED CONCEPTS — do NOT pick any of these, they have already been covered: {used_brand_concepts}

Choose ONE brand management concept or framework from the REMAINING options. \
Choose from: brand equity pyramid (Aaker), brand positioning statement structure, \
consumer insight vs. mere observation, Jobs-to-Be-Done applied to brands, brand \
architecture (house of brands vs. branded house vs. endorsed brand), brand extension \
success factors, brand extension failure autopsies, price-pack architecture, \
occasion-based marketing, penetration vs. frequency as growth levers (Byron Sharp / \
How Brands Grow), physical availability vs. mental availability, emotional vs. \
functional benefits in brand laddering, perceptual mapping, brand tracking metrics \
(awareness / consideration / preference / NPS), creative effectiveness measurement, \
the 4Ps applied to CPG, the brand funnel, repositioning a declining brand.

Format exactly as:
CONCEPT: [concept name]
THEORY: [2–3 crisp sentences — what it is and why it matters]
CLASSIC EXAMPLE: [name a real brand, the specific application of this concept, and the \
  outcome — the more specific the better]
MINI-EXERCISE: Look at one brand move from SECTION 2 above. Apply this concept to \
  analyze that brand's decision in 3–4 sentences. Show your reasoning explicitly — \
  don't just name the concept, demonstrate it.
INTERVIEW ANGLE: [one sentence on how a McKinsey, BCG, or brand management recruiter \
  might test this concept in an interview or case]

---

## SECTION 8 — CONSUMER INSIGHTS & MARKET RESEARCH LITERACY
ALREADY USED METHODOLOGIES — do NOT pick any of these, they have already been covered: {used_insights_methods}

Choose ONE consumer insights methodology or metric from the REMAINING options. \
Choose from: household penetration rate (what it is and how to grow it), purchase \
frequency and buying rate, share of category requirements (loyalty metric), consumer \
segmentation approaches (demographic, psychographic, behavioral, occasion-based), \
occasion mapping and when/where/why frameworks, attitude & usage (A&U) studies, \
panel data (Nielsen / Circana) vs. POS data — what each tells you, concept testing \
methodology, conjoint analysis (what trade-offs consumers make), NPS and its blind \
spots, the shopper vs. consumer vs. customer distinction, path to purchase mapping, \
qualitative vs. quantitative research tradeoffs, social listening as a research tool, \
how to write a consumer insight statement, Jobs-to-Be-Done interview technique.

Format exactly as:
METHODOLOGY: [name]
WHAT IT IS: [plain English, 2 sentences — someone with no research background should \
  understand this]
HOW TO READ IT: [what does a high/low score or result signal? What decision does it unlock?]
BRAND APPLICATION: [one specific real-world example — company, brand, what the data showed, \
  and what strategic decision it drove. The more specific, the better.]
PRACTICE QUESTION: [write the exact interview question a P&G, Unilever, or McKinsey \
  recruiter might ask that requires knowing this — then give a model 2-sentence answer \
  Thaiz could use]

---

## SECTION 9 — P&L LITERACY DRILL
ALREADY USED CONCEPTS — do NOT pick any of these, they have already been covered: {used_pl_concepts}

Choose ONE P&L concept or financial metric from the REMAINING options that a CPG brand \
manager must understand. Choose from: gross revenue vs. net revenue (trade deductions), \
gross margin and why it varies by category, contribution margin, trade spend mechanics \
and ROI, A&P budget (advertising & promotion) as a percent of net revenue, EBITDA and \
how brands contribute to it, incremental revenue vs. cannibalization, price elasticity \
and when to raise vs. hold price, revenue management (price-pack-channel architecture), \
promotional efficiency (lift-to-cost ratio), volume share vs. value share, working vs. \
non-working media spend, marketing mix modeling (MMM) — what it does and its limits, \
how brand investment is justified to finance on a 3-year IRR basis.

Format exactly as:
CONCEPT: [concept name]
FORMULA OR DEFINITION: [precise — if there's a formula, write it out]
WHAT IT TELLS YOU: [one sentence — what decision does knowing this number change?]
WORKED EXAMPLE: [a realistic numerical example with real-ish numbers — e.g., \
  "Brand X generates $120M gross revenue. Retailer deductions are 18%, COGS is 40% \
  of net revenue, and A&P is 14% of net revenue. Calculate gross margin and A&P spend."]
ANSWER: [work through the math step by step — show the arithmetic]
INTERVIEW TRAP: [one common mistake MBA candidates make when discussing this concept — \
  and the correct way to frame it]

---

---

## SECTION 10 — C-SUITE RADAR & HIRING SIGNAL

C-suite moves are the single best leading indicator of MBA hiring: new executives refresh \
teams, new CMOs rebuild brand functions, new CEOs restructure strategy offices. A change at \
a target company is a reason to act this week, not wait for recruiting season.

EXECUTIVE CHANGES DETECTED TODAY AT MONITORED COMPANIES:
{csuite_findings}

For each change detected:
1. What does this leadership move signal about the company's strategic direction or team needs?
2. Does a new hire (vs. departure) make this company MORE or LESS attractive for an MBA \
   summer internship right now — and why?
3. The ONE concrete action Thaiz should take in the next 7 days to capitalize on this signal \
   (reach out to a specific alumni segment, watch a specific team's LinkedIn, attend an event, \
   update her pitch for that firm, etc.)

If no C-suite changes were detected today, use this section to identify the ONE company from \
Sections 2 or 3 showing the strongest expansion or restructuring signal, and give the same \
three-part analysis for that company. Always end with a concrete 7-day action.

---

Length target: 3,000–4,000 words total. Use the exact section headers above. \
Write in a direct, confident tone — like a sharp analyst briefing a Booth MBA student \
who is one week away from a McKinsey first-round interview.
"""

# ---------------------------------------------------------------------------
# Briefing generation
# ---------------------------------------------------------------------------


def generate_briefing(
    scraped_content: str,
    today: str,
    seen: dict,
    client: OpenAI,
    csuite_findings: str = "No C-suite changes detected at monitored companies today.",
) -> str:
    log.info("Generating briefing via GitHub Models (model: %s) …", GITHUB_MODEL)

    if len(scraped_content) > MAX_SCRAPED_CHARS:
        log.warning("Scraped content truncated from %d to %d chars.", len(scraped_content), MAX_SCRAPED_CHARS)
        scraped_content = scraped_content[:MAX_SCRAPED_CHARS] + "\n\n[... content truncated to fit model limits ...]"

    used_vocab    = ", ".join(seen["vocab_terms"])      or "none yet"
    used_brand    = ", ".join(seen["brand_concepts"])   or "none yet"
    used_insights = ", ".join(seen["insights_methods"]) or "none yet"
    used_pl       = ", ".join(seen["pl_concepts"])      or "none yet"

    # Build the covered-topics block (cap at 300 most recent to keep prompt size manageable)
    recent_topics = seen["topics"][-100:] if len(seen["topics"]) > 100 else seen["topics"]
    covered_topics = "\n".join(f"- {t}" for t in recent_topics) if recent_topics else "None yet — this is the first briefing."

    log.info("Injecting %d covered topics into prompt.", len(recent_topics))

    # Use replace() instead of format() to avoid KeyError from curly braces in scraped content
    full_prompt = (PROMPT_TEMPLATE
        .replace("{today}",                 today)
        .replace("{scraped_content}",       scraped_content)
        .replace("{covered_topics}",        covered_topics)
        .replace("{used_vocab_terms}",      used_vocab)
        .replace("{used_brand_concepts}",   used_brand)
        .replace("{used_insights_methods}", used_insights)
        .replace("{used_pl_concepts}",      used_pl)
        .replace("{csuite_findings}",       csuite_findings)
    )
    log.info("Prompt size: %d characters.", len(full_prompt))

    response = client.chat.completions.create(
        model=GITHUB_MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": full_prompt}],
    )

    briefing = response.choices[0].message.content.strip()
    if not briefing:
        raise RuntimeError("GitHub Models returned empty content.")

    log.info("Briefing generated — %d characters.", len(briefing))
    return briefing

# ---------------------------------------------------------------------------
# Email delivery
# ---------------------------------------------------------------------------


def send_email(subject: str, body: str) -> None:
    log.info("Sending email to %s …", RECIPIENT_EMAIL)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT_EMAIL

    msg.attach(MIMEText(body, "plain", "utf-8"))

    html_lines = []
    for line in body.splitlines():
        if line.startswith("## "):
            html_lines.append(
                f"<h2 style='font-family:sans-serif;color:#1a1a2e;margin-top:28px;"
                f"border-bottom:2px solid #e0e0e0;padding-bottom:4px;'>"
                f"{line[3:]}</h2>"
            )
        elif line.startswith("# "):
            html_lines.append(
                f"<h1 style='font-family:sans-serif;color:#0f3460;'>{line[2:]}</h1>"
            )
        elif line.strip() == "---":
            html_lines.append("<hr style='border:none;border-top:1px solid #ddd;margin:16px 0;'>")
        elif line.strip() == "":
            html_lines.append("<br>")
        else:
            escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html_lines.append(
                f"<p style='font-family:Georgia,serif;font-size:14px;"
                f"line-height:1.7;margin:4px 0;'>{escaped}</p>"
            )

    html_body = (
        "<html><body style='max-width:720px;margin:auto;padding:24px;'>"
        f"<h1 style='font-family:sans-serif;color:#0f3460;border-bottom:3px solid #0f3460;"
        f"padding-bottom:8px;'>{subject}</h1>"
        + "\n".join(html_lines)
        + "</body></html>"
    )
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())

    log.info("Email sent successfully.")

# ---------------------------------------------------------------------------
# Local save
# ---------------------------------------------------------------------------


def save_briefing(briefing: str, date_str: str) -> Path:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    path = SAVE_DIR / f"briefing_{date_str}.txt"
    path.write_text(briefing, encoding="utf-8")
    log.info("Saved locally: %s", path)
    return path

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_env() -> list[str]:
    required = {
        "GITHUB_TOKEN":       GITHUB_TOKEN,
        "GMAIL_ADDRESS":      GMAIL_ADDRESS,
        "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
    }
    return [k for k, v in required.items() if not v]

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    missing = validate_env()
    if missing:
        log.error("Missing environment variables: %s", ", ".join(missing))
        log.error("Set them in .env (local) or as GitHub Actions Secrets (CI).")
        sys.exit(1)

    today      = datetime.date.today()
    date_str   = today.strftime("%Y-%m-%d")
    today_long = today.strftime("%A, %B %d, %Y")
    subject    = f"Booth Recruiting Briefing — {today.strftime('%B %d, %Y')}"

    log.info("=== Booth Recruiting Briefing  %s ===", date_str)

    client = OpenAI(base_url=GITHUB_MODELS_URL, api_key=GITHUB_TOKEN)

    # Load full seen record (articles + concepts + topics)
    seen = load_seen()

    # Scrape — new article URLs are added to seen["urls"] in place
    scraped = scrape_all(seen["urls"])
    log.info("Scraping done — %d characters collected.", len(scraped))

    # Persist seen URLs now so they're safe even if generation/email fails
    save_seen(seen)

    # C-Suite Radar — detect exec changes, search LinkedIn/job boards, update CSV
    log.info("Running C-Suite Radar …")
    executive_changes, new_opps_added = run_csuite_radar(scraped, client)

    if executive_changes:
        csuite_lines = [
            f"- {c['company']}: {c['person']} ({c['title']}) — {c['change']} — {c['detail']}"
            for c in executive_changes
        ]
        csuite_findings = "\n".join(csuite_lines)
        if new_opps_added:
            csuite_findings += (
                f"\n\n{new_opps_added} new Summer 2027 internship posting(s) found at "
                "flagged companies and automatically added to the opportunities tracker."
            )
    else:
        csuite_findings = "No C-suite changes detected at monitored companies today."

    log.info("C-Suite Radar done — %d change(s), %d new CSV row(s).", len(executive_changes), new_opps_added)

    # Generate briefing — injects covered topics, concept lists, and C-suite findings
    briefing = generate_briefing(scraped, today_long, seen, client, csuite_findings)

    # Extract which structured concept was chosen in each rotating section (6-9)
    concepts = extract_used_concepts(briefing)
    log.info("Structured concepts today — %s", concepts)

    if concepts.get("vocab_term"):
        seen["vocab_terms"].append(concepts["vocab_term"])
    if concepts.get("brand_concept"):
        seen["brand_concepts"].append(concepts["brand_concept"])
    if concepts.get("insights_method"):
        seen["insights_methods"].append(concepts["insights_method"])
    if concepts.get("pl_concept"):
        seen["pl_concepts"].append(concepts["pl_concept"])

    # Extract all topics from today's briefing via AI and add to history
    log.info("Extracting topics from today's briefing …")
    new_topics = extract_topics_from_briefing(briefing, client)
    log.info("Extracted %d topics from today's briefing.", len(new_topics))

    existing_lower = {t.lower() for t in seen["topics"]}
    for topic in new_topics:
        if topic.lower() not in existing_lower:
            seen["topics"].append(topic)
            existing_lower.add(topic.lower())

    # Final save with everything updated
    save_seen(seen)

    save_briefing(briefing, date_str)

    send_email(subject, briefing)

    log.info("All done. Briefing delivered and saved.")


if __name__ == "__main__":
    main()
