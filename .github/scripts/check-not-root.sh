#!/usr/bin/env bash
# Check the app inside the running stack is not running as root.
#
# The entrypoint starts as root only long enough to adopt the owner of the
# mounted ./data folder, then drops to that user. This catches a regression that
# leaves the app running as root, or back to a hard-coded user id: CI checks out
# as uid 1001, not 1000, which is deliberate cover for the second.
#
#   .github/scripts/check-not-root.sh
#
# Reads COMPOSE_FILE like docker compose does, defaulting to the shipped stack.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
export COMPOSE_FILE="${COMPOSE_FILE:-docker/docker-compose.yml}"

uid=$(docker compose exec -T api sh -c 'grep ^Uid: /proc/1/status | cut -f2')
echo "app runs as uid $uid (data folder owner: $(stat -c %u data))"
if [ "$uid" = "0" ]; then
    echo "::error::the app is running as root; it should adopt the data folder owner"
    exit 1
fi
