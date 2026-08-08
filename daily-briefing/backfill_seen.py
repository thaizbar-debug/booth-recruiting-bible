"""
One-time backfill script — reads all previous "Booth Recruiting Briefing" emails
from Gmail, uses AI to extract every topic covered, and writes them to
seen_articles.json so future briefings never repeat those topics.

Trigger once via the backfill-seen GitHub Actions workflow (workflow_dispatch).
"""

import imaplib
import email
import json
import os
import sys
import time
import logging
from pathlib import Path
from google import genai
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL       = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
SEEN_FILE          = Path(__file__).parent / "seen_articles.json"
# Search for all known subject patterns (old and new format)
SUBJECT_KEYWORDS   = ["Booth Recruiting Briefing", "daily briefing", "daily-briefing", "recruiting briefing"]
MAX_EMAIL_CHARS    = 12_000   # truncate very long emails before sending to AI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gmail helpers
# ---------------------------------------------------------------------------

def _plain_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="ignore")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="ignore")
    return ""


def fetch_briefing_bodies() -> list[str]:
    """Fetch plain-text bodies of all briefing emails from Gmail."""
    log.info("Connecting to Gmail IMAP as %s …", GMAIL_ADDRESS)
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

    bodies: list[str] = []
    seen_ids: set[bytes] = set()

    for mailbox in ["INBOX", "[Gmail]/All Mail"]:
        try:
            status, _ = mail.select(f'"{mailbox}"')
            if status != "OK":
                log.warning("  Could not select mailbox: %s", mailbox)
                continue

            # Search for every known subject pattern
            matched_ids: list[bytes] = []
            for keyword in SUBJECT_KEYWORDS:
                _, message_ids = mail.search(None, f'SUBJECT "{keyword}"')
                ids = message_ids[0].split() if message_ids and message_ids[0] else []
                log.info("  %s / '%s' — %d match(es).", mailbox, keyword, len(ids))
                for msg_id in ids:
                    if msg_id not in seen_ids:
                        matched_ids.append(msg_id)
                        seen_ids.add(msg_id)

            for msg_id in matched_ids:
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                parsed = email.message_from_bytes(msg_data[0][1])
                body = _plain_text(parsed)
                if body and body not in bodies:
                    bodies.append(body)
        except Exception as exc:
            log.warning("  Could not search %s: %s", mailbox, exc)

    mail.logout()
    log.info("Total unique briefing emails fetched: %d", len(bodies))
    return bodies


# ---------------------------------------------------------------------------
# AI topic extraction
# ---------------------------------------------------------------------------

EXTRACT_PROMPT = """\
You are reading a daily recruiting briefing email sent to an MBA student.
Your job is to extract a comprehensive list of ALL topics covered in this email.

Include: news stories, brand moves, company announcements, industry trends, \
statistics and data points, educational concepts, vocabulary terms, frameworks, \
specific case studies, and any named facts. Be specific — write "P&G Tide's Gen Z \
social campaign" not just "P&G campaign". Write "trade spend mechanics and ROI" \
not just "trade spend".

Return ONLY a valid JSON array of strings. No explanation, no markdown, just the array.
Example format: ["topic one", "topic two", "topic three"]

EMAIL CONTENT:
{email_body}
"""


def extract_topics_with_ai(body: str, client: genai.Client) -> list[str]:
    if len(body) > MAX_EMAIL_CHARS:
        body = body[:MAX_EMAIL_CHARS] + "\n[... truncated ...]"

    prompt = EXTRACT_PROMPT.replace("{email_body}", body)

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        raw = (response.text or "").strip()
        # Strip markdown code fences if present
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        topics = json.loads(raw)
        if isinstance(topics, list):
            return [str(t).strip() for t in topics if t]
    except Exception as exc:
        log.warning("  AI extraction failed: %s", exc)
    return []


# ---------------------------------------------------------------------------
# seen_articles.json helpers
# ---------------------------------------------------------------------------

def load_seen() -> dict:
    default = {
        "urls":             [],
        "vocab_terms":      [],
        "brand_concepts":   [],
        "insights_methods": [],
        "pl_concepts":      [],
        "topics":           [],
    }
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            for key in default:
                default[key] = data.get(key, default[key])
        except Exception as exc:
            log.warning("Could not read existing seen file: %s", exc)
    return default


def save_seen(seen: dict) -> None:
    SEEN_FILE.write_text(json.dumps(seen, indent=2), encoding="utf-8")
    log.info("Wrote seen_articles.json — %d topics total.", len(seen["topics"]))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        log.error("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set.")
        sys.exit(1)
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY must be set.")
        sys.exit(1)

    client = genai.Client(api_key=GEMINI_API_KEY)

    bodies = fetch_briefing_bodies()
    if not bodies:
        log.info("No briefing emails found. Nothing to backfill.")
        return

    seen = load_seen()
    existing_topics_lower = {t.lower() for t in seen["topics"]}
    new_count = 0

    for i, body in enumerate(bodies, 1):
        log.info("Processing email %d / %d …", i, len(bodies))
        topics = extract_topics_with_ai(body, client)
        log.info("  Extracted %d topics.", len(topics))

        for topic in topics:
            if topic.lower() not in existing_topics_lower:
                seen["topics"].append(topic)
                existing_topics_lower.add(topic.lower())
                new_count += 1

        time.sleep(1)  # be gentle with the API

    log.info("Backfill complete — %d new topics added (%d total).", new_count, len(seen["topics"]))
    save_seen(seen)


if __name__ == "__main__":
    main()
