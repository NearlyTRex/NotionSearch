#!/bin/sh
# Make the bind-mounted data directory writable, whoever owns it.
#
# The app stores its database in a folder bind-mounted from the host. Hard-coding
# a container user (say 1000) breaks the moment the host user's id is anything
# else — a second account, some distros, or a CI runner, where the checkout is
# owned by 1001 and SQLite fails with "unable to open database file".
#
# So instead of guessing, adopt the owner of the mounted directory. That keeps
# files owned by the host user (backups work without sudo) with no configuration.
# PUID/PGID still override it when someone wants a specific user.

set -e

DATA_DIR="${NOTIONSEARCH_DATA:-/data}"
mkdir -p "$DATA_DIR"

# Already unprivileged (someone set `user:` in compose): nothing we can change.
if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

TARGET_UID="${PUID:-$(stat -c %u "$DATA_DIR")}"
TARGET_GID="${PGID:-$(stat -c %g "$DATA_DIR")}"

# Docker Desktop on Windows and macOS reports the mount as root and handles
# permissions in its own translation layer, so staying root is correct there.
if [ "$TARGET_UID" = "0" ]; then
    exec "$@"
fi

# Best effort: on Docker Desktop mounts chown is a no-op, which is fine.
chown "$TARGET_UID:$TARGET_GID" "$DATA_DIR" 2>/dev/null || true

exec gosu "$TARGET_UID:$TARGET_GID" "$@"
