#!/usr/bin/env python3
"""
Daily MBA internship search for Thaiz Barthelmess Miletich — Booth MBA Class 2028.
Targets Summer 2027 internships in brand strategy, consulting, corporate strategy.
Runs daily via GitHub Actions at 8am Lima / 1pm UTC.

Key improvements over previous routine:
- Link validation: every URL is HTTP-checked before writing to the file
- LinkedIn deep search: job listings AND posts hinting at open roles (via DuckDuckGo)
- Smart rule-based filtering: keyword + title analysis to catch ambiguous postings
- Deduplication by URL + (company, title) fingerprint to catch reposts
"""

import os
import re
import time
import hashlib
import datetime
import httpx

JSEARCH_KEY = os.environ.get("JSEARCH_API_KEY", "")
MD_FILE = "applications/summer-2027-opportunities.md"
TODAY = datetime.date.today().strftime("%Y-%m-%d")

# ── Tier lookup ────────────────────────────────────────────────────────────────
TIER_1 = ["mckinsey", "boston consulting", "bcg", "bain", "procter", "p&g", "unilever",
          "pepsico", "pepsi co", "coca-cola", "nestle", "nestlé", "ab inbev", "kraft heinz",
          "mars ", "colgate", "kimberly-clark"]
TIER_2 = ["deloitte", "accenture", "oliver wyman", "kearney", "amazon", "google", "meta",
          "microsoft", "nike", "johnson & johnson", "j&j", "abbott", "3m", "general mills",
          "mondelez", "hershey", "campbell", "conagra", "church & dwight"]
TIER_3 = ["simon-kucher", "l.e.k.", "lek consulting", "zs associates", "cornerstone research",
          "analysis group", "charles river", "huron", "monitor deloitte", "roland berger",
          "strategy&", "pwc strategy", "ey-parthenon", "ibm consulting", "booz allen"]

# ── Relevance keywords ─────────────────────────────────────────────────────────
TITLE_INCLUDE = [
    "intern", "associate", "mba", "summer", "strategy", "brand", "consultant",
    "consulting", "manager", "rotational", "leadership development", "graduate",
    "business development", "corporate development", "strategic planning",
    "general management", "growth", "commercial", "product marketing",
]
TITLE_EXCLUDE = [
    "engineer", "software", "data scientist", "devops", "accountant", "controller",
    "tax ", "audit", "payroll", "nurse", "physician", "driver", "warehouse",
    "technician", "mechanic", "electrician", "plumber",
]
DESC_INCLUDE = [
    "mba", "master of business", "business school", "summer intern", "summer associate",
    "graduate intern", "rotational program", "leadership program", "strategy intern",
    "brand intern", "consulting intern", "corporate strategy", "brand management",
    "consumer insights", "go-to-market", "p&l", "sponsorship", "h-1b",
]

TRACK_RULES = [
    (["consulting", "consultant", "advisory", "mckinsey", "bcg", "bain", "deloitte",
      "oliver wyman", "kearney", "ey-parthenon", "strategy&"], "Consulting"),
    (["brand manag", "associate brand", "brand marketing", "brand strategy",
      "cpg", "consumer packaged", "fmcg"], "CPG Brand"),
    (["corporate strategy", "strategic planning", "corp dev", "corporate development",
      "internal strategy"], "Corp Strategy"),
    (["business development", "partnerships", "commercial strategy", "bd "], "BD Strategy"),
    (["growth", "revenue", "p&l", "go-to-market"], "Growth"),
    (["rotational", "leadership development", "general management", "general mgmt"], "General Mgmt"),
    (["product marketing", "consumer marketing", "marketing strategy"], "Marketing"),
    (["tech strategy", "technology strategy", "digital strategy"], "Tech Strategy"),
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def guess_tier(company: str) -> int:
    c = company.lower()
    for kw in TIER_1:
        if kw in c:
            return 1
    for kw in TIER_2:
        if kw in c:
            return 2
    for kw in TIER_3:
        if kw in c:
            return 3
    return 4


def guess_track(title: str, description: str) -> str:
    text = (title + " " + description).lower()
    for keywords, track in TRACK_RULES:
        if any(kw in text for kw in keywords):
            return track
    return "Corp Strategy"


def is_relevant(title: str, description: str) -> bool:
    t = title.lower()
    d = (description or "").lower()
    if any(kw in t for kw in TITLE_EXCLUDE):
        return False
    title_match = any(kw in t for kw in TITLE_INCLUDE)
    desc_match = any(kw in d for kw in DESC_INCLUDE)
    return title_match or desc_match


def make_fingerprint(title: str, company: str) -> str:
    normalized = f"{company.lower().strip()}|{title.lower().strip()}"
    return hashlib.md5(normalized.encode()).hexdigest()


def is_valid_base_url(url: str) -> bool:
    if not url:
        return False
    bad = ["twitter.com", "x.com/i/", "x.com/status/",
           "linkedin.com/posts/", "linkedin.com/pulse/",
           "facebook.com", "instagram.com"]
    return not any(p in url.lower() for p in bad)


def is_link_alive(url: str) -> bool:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    try:
        r = httpx.head(url, follow_redirects=True, timeout=12, headers=headers)
        if r.status_code < 400:
            return True
        if r.status_code in (405, 406):
            r2 = httpx.get(url, follow_redirects=True, timeout=15, headers=headers)
            return r2.status_code < 400
        return False
    except Exception:
        try:
            r = httpx.get(url, follow_redirects=True, timeout=15, headers=headers)
            return r.status_code < 400
        except Exception:
            return False


def get_watch_companies(content: str) -> list:
    companies = []
    in_table = False
    for line in content.split("\n"):
        if "## Companies to Watch" in line:
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if in_table and line.startswith("|") and "---" not in line and "Company" not in line:
            cols = [c.strip() for c in line.strip("|").split("|")]
            if cols and cols[0]:
                companies.append(cols[0])
    return companies


# ── JSearch ────────────────────────────────────────────────────────────────────

def search_jsearch(queries: list) -> list:
    if not JSEARCH_KEY:
        print("JSEARCH_API_KEY not set — skipping JSearch")
        return []
    all_jobs = []
    headers = {
        "X-RapidAPI-Key": JSEARCH_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    for query in queries:
        try:
            r = httpx.get(
                "https://jsearch.p.rapidapi.com/search",
                headers=headers,
                params={"query": query, "page": "1", "num_pages": "2", "date_posted": "week"},
                timeout=30,
            )
            jobs = r.json().get("data", [])
            all_jobs.extend(jobs)
            print(f"  JSearch '{query[:50]}' → {len(jobs)}")
        except Exception as e:
            print(f"  JSearch failed: {query[:50]} — {e}")
        time.sleep(0.4)
    return all_jobs


# ── LinkedIn / DuckDuckGo deep search ─────────────────────────────────────────

def ddg_search(query: str, max_results: int = 8) -> list:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=max_results, timelimit="m")
            return list(results) if results else []
    except Exception as e:
        print(f"  DDG error: {e}")
        return []


def search_linkedin_deep(watch_companies: list) -> list:
    results = []

    listing_queries = [
        'site:linkedin.com/jobs "MBA intern" OR "MBA associate" "summer 2027" (brand OR strategy OR consulting)',
        'site:linkedin.com/jobs "summer associate" MBA 2027 (CPG OR "consumer goods" OR consulting)',
        'site:linkedin.com/jobs "MBA internship" 2027 (strategy OR "brand manager" OR rotational)',
        'site:linkedin.com/jobs "associate brand manager" OR "brand associate" MBA summer 2027',
        'site:linkedin.com/jobs "rotational program" OR "leadership development" MBA consumer goods 2027',
        'site:linkedin.com/jobs "graduate intern" OR "graduate associate" strategy brand consulting 2027',
    ]

    priority = ["McKinsey", "BCG", "Bain", "Deloitte", "P&G", "Unilever", "PepsiCo", "Nike", "Coca-Cola", "AB InBev"]
    for company in priority:
        listing_queries.append(f'site:linkedin.com/jobs "{company}" MBA intern OR associate summer 2027')
    for company in watch_companies[:8]:
        if company not in priority:
            listing_queries.append(f'site:linkedin.com/jobs "{company}" MBA graduate intern summer 2027')

    post_queries = [
        'site:linkedin.com "we\'re growing" "strategy team" OR "brand team" MBA internship 2027',
        'site:linkedin.com "open role" OR "open position" MBA strategy OR brand OR consulting intern 2027',
        'site:linkedin.com "building our team" MBA "business school" strategy OR brand OR consulting',
        'site:linkedin.com recruiter "MBA students" OR "MBA candidates" strategy brand consulting intern 2027',
        'site:linkedin.com "excited to share" "we\'re hiring" MBA strategy OR brand consulting summer 2027',
    ]

    for query in listing_queries + post_queries:
        hits = ddg_search(query, max_results=8)
        for hit in hits:
            url = hit.get("href", "")
            if "linkedin.com" not in url:
                continue
            is_post = any(p in url for p in ["/posts/", "/pulse/", "/feed/"])
            if "/in/" in url and "/posts/" not in url:
                continue  # skip personal profile pages
            results.append({
                "title": hit.get("title", ""),
                "company": "",
                "link": url,
                "description": hit.get("body", ""),
                "is_post": is_post,
            })
        time.sleep(1.8)

    return results


# ── Row builder ────────────────────────────────────────────────────────────────

def build_row(title: str, link: str, company: str, location: str, track: str, tier: int, tag: str = "") -> str:
    label = f"[{tag}] " if tag else ""
    t = (label + title).replace("|", "-")[:90]
    c = company.replace("|", "-")
    loc = location.replace("|", "-")
    return f"| {TODAY} | [{t}]({link}) | {c} | {loc} | {track} | {tier} | New |"


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    with open(MD_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    existing_links = set(re.findall(r'\[.*?\]\((https?://[^)]+)\)', content))
    existing_fps: set = set()
    for line in content.split("\n"):
        if line.startswith("|") and "http" in line:
            m = re.search(r'\[([^\]]+)\]\(https?://[^)]+\)\s*\|\s*([^|]+)', line)
            if m:
                existing_fps.add(make_fingerprint(m.group(1), m.group(2).strip()))

    watch_companies = get_watch_companies(content)
    print(f"Watch list: {len(watch_companies)} companies")
    print(f"Existing: {len(existing_links)} links, {len(existing_fps)} fingerprints\n")

    # ── JSearch queries ────────────────────────────────────────────────────────
    company_queries = []
    for company in watch_companies:
        company_queries.extend([
            f"MBA intern summer 2027 {company}",
            f"brand strategy associate {company}",
        ])

    broad_queries = [
        "MBA summer associate consulting strategy 2027",
        "MBA associate brand manager CPG consumer goods 2027",
        "management consulting summer associate 2027",
        "brand associate CPG MBA summer intern New York Chicago",
        "corporate strategy associate MBA internship 2027",
        "MBA rotational leadership development CPG consumer",
        "summer associate MBA intern brand marketing strategy",
        "associate brand manager MBA rotational summer 2027",
        "MBA business development strategy associate",
        "MBA general management associate consumer goods",
        "strategic planning MBA associate intern summer 2027",
        "MBA intern CPG food beverage consumer brand 2027",
        "MBA summer associate McKinsey BCG Bain Deloitte",
        "consulting intern strategy MBA Chicago New York 2027",
    ]

    print("── JSearch ──")
    raw_jsearch = search_jsearch(company_queries[:20] + broad_queries)
    print(f"JSearch total raw: {len(raw_jsearch)}\n")

    print("── LinkedIn / DuckDuckGo ──")
    linkedin_results = search_linkedin_deep(watch_companies)
    print(f"LinkedIn raw: {len(linkedin_results)}\n")

    # ── Process results ────────────────────────────────────────────────────────
    new_rows = []
    seen_links = set(existing_links)
    seen_fps = set(existing_fps)
    dead_count = 0
    filtered_count = 0

    print("── Processing JSearch ──")
    for job in raw_jsearch:
        link = job.get("job_apply_link", "")
        title = job.get("job_title", "")
        company = job.get("employer_name", "")
        description = (job.get("job_description", "") or "")[:800]
        city = job.get("job_city", "") or ""
        state = job.get("job_state", "") or ""
        location = f"{city}, {state}".strip(", ") or "Various"

        if not is_valid_base_url(link) or link in seen_links:
            continue
        fp = make_fingerprint(title, company)
        if fp in seen_fps:
            continue
        if not is_relevant(title, description):
            filtered_count += 1
            continue
        if not is_link_alive(link):
            dead_count += 1
            print(f"  DEAD: {company} — {title[:45]}")
            continue

        seen_links.add(link)
        seen_fps.add(fp)
        tier = guess_tier(company)
        track = guess_track(title, description)
        new_rows.append((tier, build_row(title, link, company, location, track, tier)))
        print(f"  ✓ Tier {tier} [{track}] {company}: {title[:45]}")

    print("\n── Processing LinkedIn ──")
    for item in linkedin_results:
        link = item["link"]
        title = item["title"]
        description = item["description"]
        is_post = item.get("is_post", False)

        if link in seen_links:
            continue
        if not is_relevant(title, description):
            filtered_count += 1
            continue
        if not is_link_alive(link):
            dead_count += 1
            continue

        # Try to extract company name from title (e.g. "Strategy Intern at Nike | LinkedIn")
        company = ""
        m = re.search(r" at ([^|–\-]+)", title)
        if m:
            company = m.group(1).strip()

        fp = make_fingerprint(title, company)
        if fp in seen_fps:
            continue

        seen_links.add(link)
        seen_fps.add(fp)
        tier = guess_tier(company)
        track = guess_track(title, description)
        tag = "POST" if is_post else ""
        location = "LinkedIn Post" if is_post else "LinkedIn Jobs"
        new_rows.append((tier, build_row(title, link, company, location, track, tier, tag=tag)))
        print(f"  ✓ {'[POST]' if is_post else '[JOB] '} Tier {tier} [{track}] {company or '?'}: {title[:45]}")

    # ── Write to file ──────────────────────────────────────────────────────────
    new_rows.sort(key=lambda x: x[0])
    row_strings = [r for _, r in new_rows]

    if row_strings:
        insert_marker = "\n---\n\n## Companies to Watch"
        new_content = content.replace(insert_marker, "\n" + "\n".join(row_strings) + insert_marker)
        with open(MD_FILE, "w", encoding="utf-8") as f:
            f.write(new_content)

    # ── Summary ────────────────────────────────────────────────────────────────
    by_tier: dict = {}
    for tier, _ in new_rows:
        by_tier[tier] = by_tier.get(tier, 0) + 1

    print(f"""
════════════════════════════════════
JOB SEARCH — {TODAY}
════════════════════════════════════
New roles added  : {len(new_rows)}
Dead links skipped: {dead_count}
Irrelevant filtered: {filtered_count}
By tier: {by_tier}
""")

    if new_rows:
        print("Top picks:")
        for _, row in new_rows[:5]:
            print(f"  {row[:110]}")
    else:
        print("No new roles today.")


if __name__ == "__main__":
    main()
