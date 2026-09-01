#!/usr/bin/env bash
# Shellcheck every shell script in the repository.
#
# Finds them by extension *and* by shebang, so a script named without .sh
# (a git hook, an entrypoint) is still checked.
#
#   .github/scripts/shellcheck-all.sh
#   .github/scripts/shellcheck-all.sh --severity=warning

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# --cached --others --exclude-standard: tracked files *and* new ones not yet
# committed, while still honouring .gitignore. Tracked-only would silently skip
# a brand new script, which is exactly when checking matters most.
git_files() {
    git ls-files --cached --others --exclude-standard "$@"
}

mapfile -t scripts < <(
    {
        git_files -- '*.sh' '*.bash'
        # Files whose first line is a shell shebang, whatever they are named.
        git_files | while IFS= read -r f; do
            [ -f "$f" ] || continue
            head -c 2 -- "$f" 2>/dev/null | grep -q '^#!' || continue
            head -n 1 -- "$f" | grep -Eq '^#!.*/(env +)?(ba|da|k|z)?sh\b' && printf '%s\n' "$f"
        done
    } | sort -u
)

if [ "${#scripts[@]}" -eq 0 ]; then
    echo "No shell scripts found."
    exit 0
fi

echo "Checking ${#scripts[@]} shell script(s):"
printf '  %s\n' "${scripts[@]}"
echo

if ! command -v shellcheck > /dev/null 2>&1; then
    echo "shellcheck is not installed." >&2
    echo "  Ubuntu/Mint: sudo apt-get install shellcheck" >&2
    echo "  Or:          docker run --rm -v \"\$PWD:/mnt\" -w /mnt koalaman/shellcheck:stable ..." >&2
    exit 127
fi

if shellcheck "$@" -- "${scripts[@]}"; then
    echo "All shell scripts pass shellcheck."
else
    echo "::error::shellcheck found problems"
    exit 1
fi
