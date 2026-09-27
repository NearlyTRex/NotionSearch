#!/usr/bin/env bash
# Start a throwaway Meilisearch for the tests, then run a command against it.
#
# The image is read from docker/docker-compose.yml, so the tests always run
# against the version that ships: when Dependabot bumps it there, CI follows.
# In CI the connection settings are also exported to later steps, which is how
# the coverage run finds it.
#
#   .github/scripts/with-meilisearch.sh python -m pytest -q
#   .github/scripts/with-meilisearch.sh          # just start it
#
# Locally the server is left running for the next run; stop it with
#   docker stop notionsearch-test-meili

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

NAME=notionsearch-test-meili
PORT="${MEILI_TEST_PORT:-7700}"
KEY=testkey1234567890

image=$(grep -Eo 'getmeili/meilisearch:[^"[:space:]]+' docker/docker-compose.yml | sort -u)
if [ "$(printf '%s\n' "$image" | grep -c .)" -ne 1 ]; then
    echo "::error::expected exactly one Meilisearch image in docker/docker-compose.yml, found: ${image:-none}"
    exit 1
fi

if [ "$(docker inspect -f '{{.Config.Image}}' "$NAME" 2>/dev/null)" != "$image" ]; then
    docker rm -f "$NAME" > /dev/null 2>&1 || true
    docker run -d --rm --name "$NAME" -p "127.0.0.1:$PORT:7700" \
        -e MEILI_MASTER_KEY="$KEY" -e MEILI_NO_ANALYTICS=true -e MEILI_ENV=development \
        "$image" > /dev/null
fi

for ((i = 1; i <= 60; i++)); do
    if curl -sf "http://127.0.0.1:$PORT/health" > /dev/null 2>&1; then
        echo "$image ready after ${i}s"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "::error::Meilisearch never became healthy"
        docker logs "$NAME" || true
        exit 1
    fi
    sleep 1
done

export MEILI_URL="http://127.0.0.1:$PORT" MEILI_MASTER_KEY="$KEY"
# A missing search engine fails the run instead of silently skipping the
# search tests: a green run that skipped them is not a passing build.
export REQUIRE_MEILI=1

if [ -n "${GITHUB_ENV:-}" ]; then
    printf '%s\n' "MEILI_URL=$MEILI_URL" "MEILI_MASTER_KEY=$MEILI_MASTER_KEY" \
        "REQUIRE_MEILI=$REQUIRE_MEILI" >> "$GITHUB_ENV"
fi

if [ "$#" -gt 0 ]; then
    "$@"
fi
