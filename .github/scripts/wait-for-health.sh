#!/usr/bin/env bash
# Wait for the stack to answer /health; dump container logs if it never does.
#
# Runnable locally exactly as CI runs it:
#   .github/scripts/wait-for-health.sh
#   .github/scripts/wait-for-health.sh http://localhost:9000/health 30

set -euo pipefail

URL="${1:-http://localhost:8080/health}"
TIMEOUT="${2:-60}"
COMPOSE_FILE="${COMPOSE_FILE:-docker/docker-compose.yml}"

for ((i = 1; i <= TIMEOUT; i++)); do
    if curl -sf "$URL" > /dev/null 2>&1; then
        echo "healthy after ${i}s: $(curl -s "$URL")"
        exit 0
    fi
    sleep 1
done

# ::error:: is a GitHub Actions annotation; harmless noise when run locally.
echo "::error::the stack never became healthy after ${TIMEOUT}s"
echo "--- container logs ---"
docker compose -f "$COMPOSE_FILE" logs || true
exit 1
