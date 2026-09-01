#!/usr/bin/env bash
# Check the app serves its UI and static assets.
#
# Catches a broken static mount, which unit tests cannot see because they use
# an in-process test client rather than a real server.
#
#   .github/scripts/check-endpoints.sh [base-url]

set -euo pipefail

BASE="${1:-http://localhost:8080}"
failed=0

check() {
    local path="$1" description="$2"
    if curl -sf -o /dev/null "${BASE}${path}"; then
        echo "  ok    ${description} (${path})"
    else
        echo "  FAIL  ${description} (${path})"
        failed=1
    fi
}

echo "Checking ${BASE}"
check "/health" "health endpoint"
check "/static/app.js" "javascript"
check "/static/styles.css" "stylesheet"

if curl -sf "${BASE}/" | grep -q "NotionSearch"; then
    echo "  ok    index page renders"
else
    echo "  FAIL  index page did not contain 'NotionSearch'"
    failed=1
fi

if [ "$failed" -ne 0 ]; then
    echo "::error::the app is running but is not serving correctly"
    exit 1
fi
echo "All endpoints OK"
