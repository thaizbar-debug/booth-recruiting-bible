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
GITHUB_MODEL       = os.getenv("GITHUB_MODEL", "gpt-4o-mini")

GITHUB_MODELS_URL  = "https://models.inference.ai.azure.com"
MAX_SCRAPED_CHARS  = 60_000

# Tracks URLs/keys of articles already sent in previous runs
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
# Seen-articles tracking (cross-run deduplication)
# ---------------------------------------------------------------------------

def load_seen() -> set[str]:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            seen = set(data.get("urls", []))
            log.info("Loaded %d previously seen article keys.", len(seen))
            return seen
        except Exception as exc:
            log.warning("Could not load seen file: %s", exc)
    return set()


def save_seen(seen: set[str]) -> None:
    # Cap at 5000 entries so the file stays small (covers ~3 months of daily runs)
    entries = list(seen)
    if len(entries) > 5000:
        entries = entries[-5000:]
    SEEN_FILE.write_text(json.dumps({"urls": entries}, indent=2), encoding="utf-8")
    log.info("Saved %d seen keys to %s", len(entries), SEEN_FILE)


def _article_key(url: str, title: str) -> str:
    """Returns the best dedup key: URL if available, otherwise normalised title."""
    if url and url.startswith("http"):
        return url
    return title.lower()[:100]

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
    {"name": "Twitter @profgalloway",    "account": "profgalloway"},
    {"name": "Twitter @lennysan",        "account": "lennysan"},
    {"name": "Twitter @markritson",      "account": "markritson"},
    {"name": "Twitter Marketing Search", "query":   "CPG brand marketing strategy -filter:retweets"},
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

        # Extract article URL for cross-run deduplication
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


def scrape_source(source: dict, seen: set[str]) -> str:
    log.info("Scraping: %s", source["name"])
    soup = _fetch(source["url"])
    if soup is None:
        return f"[{source['name']}] — Could not retrieve content (network error or bot block).\n"

    items = _extract_items(soup, source)

    # Filter out articles already seen in previous runs
    new_items = []
    for item in items:
        key = _article_key(item.get("url", ""), item["title"])
        if key in seen:
            log.info("  Skipping (already sent): %s", item["title"][:60])
        else:
            new_items.append(item)
            seen.add(key)

    if not new_items and not items:
        headings = [_clean(h.get_text()) for h in soup.find_all(["h1", "h2", "h3"])[:15]]
        new_items = [{"title": h, "text": "", "author": "", "date": "", "url": ""} for h in headings if h]
        for item in new_items:
            seen.add(_article_key("", item["title"]))

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


def scrape_reddit(source: dict, seen: set[str]) -> str:
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
    for i, post in enumerate(posts[:15], 1):
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
        if key in seen:
            log.info("  Skipping Reddit (already sent): %s", title[:60])
            continue

        seen.add(key)
        added += 1
        lines.append(f"\n[{added}] {title}")
        lines.append(f"    By u/{author} | {score} upvotes | {comments} comments")
        if text:
            lines.append(f"    {text}")

    if added == 0:
        return f"[{source['name']}] — No new posts since last briefing.\n"

    lines.append("")
    return "\n".join(lines)


def scrape_twitter(source: dict, seen: set[str]) -> str:
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
                if key in seen:
                    log.info("  Skipping tweet (already sent): %s", text[:60])
                    continue
                seen.add(key)
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


def scrape_all(seen: set[str]) -> str:
    blocks: list[str] = []
    for source in SOURCES:
        blocks.append(scrape_source(source, seen))
        time.sleep(2)
    for source in REDDIT_SOURCES:
        blocks.append(scrape_reddit(source, seen))
        time.sleep(1)
    for source in TWITTER_SOURCES:
        blocks.append(scrape_twitter(source, seen))
        time.sleep(2)
    return "\n".join(blocks)

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
Choose ONE term from CPG industry vocabulary that Thaiz should master. Pick a different \
term each day — rotate across the full spectrum. Never repeat a term used in recent days. \
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
Choose ONE brand management concept or framework. Rotate daily — never repeat. \
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
Choose ONE consumer insights methodology or metric. Rotate daily — never repeat. \
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
Choose ONE P&L concept or financial metric that a CPG brand manager must understand. \
Rotate daily — never repeat. Choose from: gross revenue vs. net revenue (trade deductions), \
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

Length target: 3,000–4,000 words total. Use the exact section headers above. \
Write in a direct, confident tone — like a sharp analyst briefing a Booth MBA student \
who is one week away from a McKinsey first-round interview.
"""

# ---------------------------------------------------------------------------
# Briefing generation
# ---------------------------------------------------------------------------


def generate_briefing(scraped_content: str, today: str) -> str:
    log.info("Generating briefing via GitHub Models (model: %s) …", GITHUB_MODEL)

    if len(scraped_content) > MAX_SCRAPED_CHARS:
        log.warning("Scraped content truncated from %d to %d chars.", len(scraped_content), MAX_SCRAPED_CHARS)
        scraped_content = scraped_content[:MAX_SCRAPED_CHARS] + "\n\n[... content truncated to fit model limits ...]"

    # Use replace() instead of format() — scraped web content often contains {curly braces}
    # from JavaScript/JSON/CSS, which would cause KeyError with str.format().
    full_prompt = PROMPT_TEMPLATE.replace("{today}", today).replace("{scraped_content}", scraped_content)
    log.info("Prompt size: %d characters.", len(full_prompt))

    client = OpenAI(base_url=GITHUB_MODELS_URL, api_key=GITHUB_TOKEN)
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

    seen = load_seen()

    scraped = scrape_all(seen)
    log.info("Scraping done — %d characters collected.", len(scraped))

    save_seen(seen)

    briefing = generate_briefing(scraped, today_long)

    save_briefing(briefing, date_str)

    send_email(subject, briefing)

    log.info("All done. Briefing delivered and saved.")


if __name__ == "__main__":
    main()
