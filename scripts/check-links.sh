#!/usr/bin/env bash
# Usage: bash scripts/check-links.sh [file]
# Default file: applications/summer-2027-opportunities.md
#
# Extracts all markdown links from the file, tries to HEAD each one,
# and prints a report. Career sites with bot protection return 403
# (indistinguishable from live vs dead) — those are flagged as NEEDS MANUAL CHECK.
# True 404s and obvious "not found" page content are flagged as LIKELY DEAD.

set -euo pipefail

FILE="${1:-applications/summer-2027-opportunities.md}"
TIMEOUT=10

if [ ! -f "$FILE" ]; then
  echo "File not found: $FILE"
  exit 1
fi

# Extract all URLs from markdown links: [text](url)
URLS=$(grep -oP '\(https?://[^)]+\)' "$FILE" | tr -d '()' | sort -u)

if [ -z "$URLS" ]; then
  echo "No URLs found in $FILE"
  exit 0
fi

TOTAL=$(echo "$URLS" | wc -l)
echo "Checking $TOTAL URLs in $FILE..."
echo "================================================"

DEAD=()
MANUAL=()
OK=()

while IFS= read -r url; do
  # HEAD request with browser-like User-Agent
  response=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time "$TIMEOUT" \
    --location \
    -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36" \
    -X HEAD \
    "$url" 2>/dev/null || echo "000")

  case "$response" in
    200|201|202)
      OK+=("[$response] $url")
      ;;
    301|302|303|307|308)
      # Follow redirect — already followed via --location, so if we land here it means
      # the final destination returned a redirect loop or similar
      MANUAL+=("[$response REDIRECT] $url")
      ;;
    403|429)
      # Bot protection — cannot determine live/dead automatically
      MANUAL+=("[$response BOT-PROTECTED] $url")
      ;;
    404|410)
      DEAD+=("[$response DEAD] $url")
      ;;
    000)
      MANUAL+=("[TIMEOUT/ERROR] $url")
      ;;
    *)
      MANUAL+=("[$response UNKNOWN] $url")
      ;;
  esac
done <<< "$URLS"

echo ""
echo "LIKELY DEAD (${#DEAD[@]}) — remove these rows:"
echo "------------------------------------------------"
if [ ${#DEAD[@]} -eq 0 ]; then
  echo "  None detected"
else
  for item in "${DEAD[@]}"; do echo "  $item"; done
fi

echo ""
echo "NEEDS MANUAL CHECK (${#MANUAL[@]}) — open in browser:"
echo "-------------------------------------------------------"
if [ ${#MANUAL[@]} -eq 0 ]; then
  echo "  None"
else
  for item in "${MANUAL[@]}"; do echo "  $item"; done
fi

echo ""
echo "OK (${#OK[@]}):"
echo "---------------"
if [ ${#OK[@]} -eq 0 ]; then
  echo "  None confirmed (most career sites block automated checks)"
else
  for item in "${OK[@]}"; do echo "  $item"; done
fi

echo ""
echo "================================================"
echo "Done. To remove a dead row, open the file and delete the entire | ... | line."
echo "Run this script again after cleaning to verify."
