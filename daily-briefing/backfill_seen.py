"""
One-time backfill script — reads all previous "Booth Recruiting Briefing" emails
from Gmail and adds the Section 6-9 concepts to seen_articles.json so they are
never repeated in future briefings.

Run via the backfill-seen GitHub Actions workflow (workflow_dispatch).
"""

import imaplib
import email
import json
import re
import os
import sys
import logging
from pathlib import Path
from email.header import decode_header as _decode_header
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
SEEN_FILE          = Path(__file__).parent / "seen_articles.json"
SUBJECT_KEYWORD    = "Booth Recruiting Briefing"

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
    """Extract plain-text body from an email message."""
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
    """Connect to Gmail IMAP and return plain-text bodies of all briefing emails."""
    log.info("Connecting to Gmail IMAP as %s …", GMAIL_ADDRESS)
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

    bodies: list[str] = []

    # Search both INBOX and [Gmail]/All Mail to catch everything
    for mailbox in ["INBOX", "[Gmail]/All Mail"]:
        try:
            status, _ = mail.select(f'"{mailbox}"')
            if status != "OK":
                continue

            _, message_ids = mail.search(None, f'SUBJECT "{SUBJECT_KEYWORD}"')
            ids = message_ids[0].split() if message_ids and message_ids[0] else []
            log.info("  %s — found %d matching email(s).", mailbox, len(ids))

            for msg_id in ids:
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                parsed = email.message_from_bytes(raw)
                body = _plain_text(parsed)
                if body and body not in bodies:
                    bodies.append(body)
        except Exception as exc:
            log.warning("  Could not search %s: %s", mailbox, exc)

    mail.logout()
    log.info("Total unique briefing email bodies fetched: %d", len(bodies))
    return bodies


# ---------------------------------------------------------------------------
# Concept extraction (mirrors logic in briefing.py)
# ---------------------------------------------------------------------------

def _find_value(text: str, label: str) -> str:
    m = re.search(rf"^{re.escape(label)}:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _section_text(full: str, header: str, next_header: str | None = None) -> str:
    start = full.find(header)
    if start == -1:
        return ""
    end = full.find(next_header, start) if next_header else len(full)
    return full[start:end]


def extract_concepts(body: str) -> dict:
    s6 = _section_text(body, "SECTION 6", "SECTION 7")
    s7 = _section_text(body, "SECTION 7", "SECTION 8")
    s8 = _section_text(body, "SECTION 8", "SECTION 9")
    s9 = _section_text(body, "SECTION 9")
    return {
        "vocab_term":      _find_value(s6, "TERM"),
        "brand_concept":   _find_value(s7, "CONCEPT"),
        "insights_method": _find_value(s8, "METHODOLOGY"),
        "pl_concept":      _find_value(s9, "CONCEPT"),
    }


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
    }
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            default.update({
                "urls":             data.get("urls", []),
                "vocab_terms":      data.get("vocab_terms", []),
                "brand_concepts":   data.get("brand_concepts", []),
                "insights_methods": data.get("insights_methods", []),
                "pl_concepts":      data.get("pl_concepts", []),
            })
        except Exception as exc:
            log.warning("Could not read existing seen file: %s", exc)
    return default


def save_seen(seen: dict) -> None:
    SEEN_FILE.write_text(json.dumps(seen, indent=2), encoding="utf-8")
    log.info("Wrote seen_articles.json — %d vocab, %d brand, %d insights, %d P&L",
             len(seen["vocab_terms"]), len(seen["brand_concepts"]),
             len(seen["insights_methods"]), len(seen["pl_concepts"]))


def _add_unique(lst: list, value: str) -> bool:
    """Append value to list if not already present (case-insensitive). Returns True if added."""
    if value and value.lower() not in {v.lower() for v in lst}:
        lst.append(value)
        return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        log.error("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set.")
        sys.exit(1)

    bodies = fetch_briefing_bodies()
    if not bodies:
        log.info("No briefing emails found. Nothing to backfill.")
        return

    seen = load_seen()

    added = {"vocab_terms": 0, "brand_concepts": 0, "insights_methods": 0, "pl_concepts": 0}

    for i, body in enumerate(bodies, 1):
        concepts = extract_concepts(body)
        log.info("Email %d — extracted: %s", i, concepts)

        if _add_unique(seen["vocab_terms"],      concepts["vocab_term"]):
            added["vocab_terms"] += 1
        if _add_unique(seen["brand_concepts"],   concepts["brand_concept"]):
            added["brand_concepts"] += 1
        if _add_unique(seen["insights_methods"], concepts["insights_method"]):
            added["insights_methods"] += 1
        if _add_unique(seen["pl_concepts"],      concepts["pl_concept"]):
            added["pl_concepts"] += 1

    log.info("New entries added — vocab: %d, brand: %d, insights: %d, P&L: %d",
             added["vocab_terms"], added["brand_concepts"],
             added["insights_methods"], added["pl_concepts"])

    save_seen(seen)
    log.info("Backfill complete.")


if __name__ == "__main__":
    main()
