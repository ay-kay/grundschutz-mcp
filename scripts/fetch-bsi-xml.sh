#!/usr/bin/env bash
# ============================================================================
# Fetch the BSI IT-Grundschutz-Kompendium 2023 XML.
# ----------------------------------------------------------------------------
# Source: https://www.bsi.bund.de (official, publication-file URL).
# The file is ~3 MB. We do NOT commit it to the repo to (a) avoid stale
# data when the BSI publishes corrections and (b) sidestep any licensing
# ambiguity about redistribution.
#
# Usage:
#   ./scripts/fetch-bsi-xml.sh        # fetches into ./XML_Kompendium_2023.xml
# ============================================================================
set -euo pipefail

URL="https://www.bsi.bund.de/SharedDocs/Downloads/DE/BSI/Grundschutz/IT-GS-Kompendium/XML_Kompendium_2023.xml?__blob=publicationFile&v=4"
DEST="${1:-XML_Kompendium_2023.xml}"

if [ -s "$DEST" ]; then
    echo "[fetch-bsi-xml] $DEST exists ($(wc -c < "$DEST") bytes), skipping."
    echo "                Delete it first if you want a fresh download."
    exit 0
fi

echo "[fetch-bsi-xml] Downloading from BSI -> $DEST"
curl -fsSL --retry 3 --retry-delay 2 -o "$DEST" "$URL"

bytes=$(wc -c < "$DEST")
if [ "$bytes" -lt 1000000 ]; then
    echo "[fetch-bsi-xml] ERROR: downloaded file is only $bytes bytes," \
         "expected ~3 MB. Removing partial file."
    rm -f "$DEST"
    exit 1
fi
echo "[fetch-bsi-xml] OK: $DEST ($bytes bytes)"
