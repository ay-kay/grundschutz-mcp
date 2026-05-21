#!/usr/bin/env bash
# ============================================================================
# Fetch the BSI Kreuzreferenztabelle (KRT) 2023 XLSX.
# ----------------------------------------------------------------------------
# The KRT contains the M:N mapping between Anforderungen and elementare
# Gefährdungen, plus the C/I/A protection-goal markers per requirement.
# This is the one piece of data that is NOT in the DocBook XML.
#
# Format: one Excel sheet per Baustein (named `KRT_<Code>.xlsx`).
#   - column A: requirement code
#   - column B: requirement title
#   - column C: CIA string (e.g. "CI", "I", "-")
#   - columns D..: one column per elementary threat G 0.x, value 'X'/'-'
#
# Usage:
#   ./scripts/fetch-bsi-krt.sh         # -> ./krt2023.xlsx
# ============================================================================
set -euo pipefail

URL="https://www.bsi.bund.de/SharedDocs/Downloads/DE/BSI/Grundschutz/IT-GS-Kompendium/krt2023_Excel.xlsx?__blob=publicationFile&v=4"
DEST="${1:-krt2023.xlsx}"

if [ -s "$DEST" ]; then
    echo "[fetch-bsi-krt] $DEST exists ($(wc -c < "$DEST") bytes), skipping."
    exit 0
fi

echo "[fetch-bsi-krt] Downloading from BSI -> $DEST"
curl -fsSL --retry 3 -o "$DEST" "$URL"

bytes=$(wc -c < "$DEST")
if [ "$bytes" -lt 100000 ]; then
    echo "[fetch-bsi-krt] ERROR: downloaded file is only $bytes bytes," \
         "expected ~350 KB. Removing partial file."
    rm -f "$DEST"
    exit 1
fi
echo "[fetch-bsi-krt] OK: $DEST ($bytes bytes)"
