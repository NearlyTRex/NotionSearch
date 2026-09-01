#!/usr/bin/env bash
# Assert that no file which could hold a credential is tracked by git.
#
# gitleaks scans file *contents*; this checks the complementary thing — that
# whole categories of file never get committed at all. The app stores a Notion
# token inside data/notionsearch.db, so a single `git add -f data/` would put a
# live credential in a public repository, and gitleaks would not necessarily
# flag a binary SQLite file.
#
#   .github/scripts/check-no-secrets.sh

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Patterns that must never appear in the index. .env.example is deliberately
# excluded: it is a template and contains no real values.
patterns=(
    '*.env'
    '.env'
    '.env.*'
    '*.db'
    '*.db-shm'
    '*.db-wal'
    '*.sqlite'
    '*.sqlite3'
    '*.pem'
    '*.key'
    '*.p12'
    '*.pfx'
    'id_rsa'
    'id_ed25519'
)

echo "Checking that no credential-bearing files are tracked..."

found=""
for pattern in "${patterns[@]}"; do
    while IFS= read -r file; do
        [ -z "$file" ] && continue
        # Templates and examples are fine; they hold placeholders.
        case "$file" in
            *.example | *.sample | *.template) continue ;;
        esac
        found="${found}${file}"$'\n'
    done < <(git ls-files -- "$pattern" 2>/dev/null || true)
done

if [ -n "$found" ]; then
    echo
    echo "::error::files that may contain credentials are tracked by git:"
    printf '%s' "$found" | sed 's/^/    /'
    echo
    echo "  If one of these is genuinely safe, add an exception to this script."
    echo "  Otherwise remove it from the index:"
    echo "      git rm --cached <file>"
    echo "  and remember that anything already pushed must be treated as leaked:"
    echo "  rotate the credential rather than only deleting the file."
    exit 1
fi

echo "  ok  no credential-bearing files are tracked"

# The data directory holds the database, so its ignore rule matters. Only
# .gitkeep should ever be tracked there.
tracked_data=$(git ls-files -- 'data/*' | grep -v '^data/.gitkeep$' || true)
if [ -n "$tracked_data" ]; then
    echo "::error::unexpected tracked files under data/:"
    printf '%s\n' "$tracked_data" | sed 's/^/    /'
    exit 1
fi
echo "  ok  data/ contains only .gitkeep"
