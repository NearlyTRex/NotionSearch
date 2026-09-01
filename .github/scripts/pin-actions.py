#!/usr/bin/env python3
"""Pin GitHub Actions to commit SHAs instead of floating tags.

A tag is mutable: whoever controls the action can move `v4` to point at
different code, and every workflow using it silently runs that instead. A commit
SHA cannot be moved, so pinning to one means the workflow runs exactly the code
that was reviewed.

Rewrites this:

    - uses: actions/checkout@v4

into this:

    - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

keeping the human-readable version in a trailing comment, which is what tools
like Dependabot read when they offer an update.

Usage:
    .github/scripts/pin-actions.py              # update files in place
    .github/scripts/pin-actions.py --dry-run    # show what would change
    .github/scripts/pin-actions.py --check      # exit 1 if anything is unpinned.
                                                # Offline, no API calls: for CI
    .github/scripts/pin-actions.py --check-latest   # also flag pins behind the
                                                    # latest release

Set GITHUB_TOKEN (or GH_TOKEN) to raise the API rate limit from 60 to 5000
requests an hour. Unauthenticated is usually fine for a handful of actions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API = "https://api.github.com"

# A `uses:` line, with optional list dash, quoting, and trailing comment.
USES_RE = re.compile(
    r"""^(?P<prefix>\s*(?:-\s+)?uses:\s*)
         (?P<quote>["']?)
         (?P<action>[^"'\s#]+)
         (?P=quote)
         (?P<trailing>\s*(?:\#.*)?)$""",
    re.VERBOSE,
)

# owner/repo, an optional path inside the repo, then @ref.
ACTION_RE = re.compile(r"^(?P<repo>[^/@]+/[^/@]+)(?P<subpath>/[^@]+)?@(?P<ref>.+)$")

SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Leading major version of a tag, e.g. v4.2.2 -> 4. Used only to warn.
MAJOR_RE = re.compile(r"^v?(\d+)")


class PinError(Exception):
    """Something the user needs to be told about in plain language."""


@dataclass
class Use:
    """One `uses:` reference found in a workflow."""

    path: Path
    lineno: int
    repo: str
    subpath: str
    ref: str
    line: str

    @property
    def is_pinned(self) -> bool:
        return bool(SHA_RE.match(self.ref))


# --- GitHub API -----------------------------------------------------------

def _request(url: str) -> dict | list:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "notionsearch-pin-actions",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and "rate limit" in exc.read().decode(errors="replace").lower():
            raise PinError(
                "GitHub API rate limit reached.\n"
                "Set GITHUB_TOKEN to a personal access token to raise it:\n"
                "  export GITHUB_TOKEN=$(gh auth token)"
            ) from exc
        if exc.code == 404:
            raise PinError(f"Not found: {url}") from exc
        raise PinError(f"GitHub API error {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise PinError(f"Could not reach GitHub: {exc.reason}") from exc


def _version_key(tag: str) -> tuple:
    """Sort key for version-ish tags, newest highest.

    Falls back to a low sort position for anything unparseable, so odd tags
    never beat a real release.
    """
    match = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?P<suffix>.*)$", tag)
    if not match:
        return (0, 0, 0, 0, tag)
    major, minor, patch = (int(match.group(i) or 0) for i in (1, 2, 3))
    # A pre-release (-rc1, -beta) sorts below the plain release.
    is_final = 0 if match.group("suffix") else 1
    return (1, major, minor, patch, is_final)


class Resolver:
    """Looks up latest tags and their commit SHAs, caching per repository."""

    def __init__(self) -> None:
        self._latest: dict[str, str] = {}
        self._sha: dict[tuple[str, str], str] = {}

    def latest_tag(self, repo: str) -> str:
        if repo in self._latest:
            return self._latest[repo]

        tag = None
        # Most actions publish releases; that is the version people mean.
        try:
            release = _request(f"{API}/repos/{repo}/releases/latest")
            if isinstance(release, dict):
                tag = release.get("tag_name")
        except PinError:
            tag = None  # Fall back to tags below.

        if not tag:
            tags = _request(f"{API}/repos/{repo}/tags?per_page=100")
            names = [t["name"] for t in tags if isinstance(t, dict) and "name" in t]
            if not names:
                raise PinError(f"{repo} has no releases or tags to pin to")
            tag = max(names, key=_version_key)

        self._latest[repo] = tag
        return tag

    def sha_for(self, repo: str, ref: str) -> str:
        """Commit SHA for a ref.

        Uses the commits endpoint, which dereferences annotated tags for us —
        the tag object's own SHA is not the commit SHA, and using it would
        produce a reference that does not resolve.
        """
        key = (repo, ref)
        if key in self._sha:
            return self._sha[key]

        commit = _request(f"{API}/repos/{repo}/commits/{ref}")
        if not isinstance(commit, dict) or "sha" not in commit:
            raise PinError(f"Could not resolve {repo}@{ref} to a commit")

        sha = commit["sha"]
        if not SHA_RE.match(sha):
            raise PinError(f"{repo}@{ref} returned an unexpected sha: {sha}")

        self._sha[key] = sha
        return sha


# --- scanning and rewriting -----------------------------------------------

def workflow_files(root: Path) -> list[Path]:
    """Workflows plus any composite actions defined in this repository."""
    found: list[Path] = []
    for pattern in ("workflows/*.yml", "workflows/*.yaml",
                    "actions/**/action.yml", "actions/**/action.yaml"):
        found.extend((root / ".github").glob(pattern))
    return sorted(set(found))


def find_uses(path: Path) -> list[Use]:
    uses = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        match = USES_RE.match(line)
        if not match:
            continue

        action = match.group("action")
        # Local actions (./.github/actions/x) and docker:// images have no
        # commit to pin to.
        if action.startswith(("./", ".\\", "docker://")):
            continue

        parsed = ACTION_RE.match(action)
        if not parsed:
            continue

        uses.append(Use(
            path=path,
            lineno=lineno,
            repo=parsed.group("repo"),
            subpath=parsed.group("subpath") or "",
            ref=parsed.group("ref"),
            line=line,
        ))
    return uses


def rewrite_line(line: str, repo: str, subpath: str, sha: str, tag: str) -> str:
    """Replace the ref with a SHA and put the version in a trailing comment."""
    match = USES_RE.match(line)
    if not match:  # pragma: no cover - callers only pass matched lines
        return line
    quote = match.group("quote")
    return f"{match.group('prefix')}{quote}{repo}{subpath}@{sha}{quote} # {tag}"


# --- main -----------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pin GitHub Actions to commit SHAs.",
        epilog="Set GITHUB_TOKEN to raise the API rate limit.",
    )
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any action is not pinned to a SHA. Offline; for CI")
    parser.add_argument("--check-latest", action="store_true",
                        help="also exit 1 if a pin is behind the latest release (needs network)")
    parser.add_argument("--keep-version", action="store_true",
                        help="pin the version already referenced instead of upgrading to latest")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would change without writing")
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help="repository root (default: current directory)")
    args = parser.parse_args(argv)

    files = workflow_files(args.root)
    if not files:
        print(f"No workflows found under {args.root / '.github'}")
        return 0

    all_uses = [use for path in files for use in find_uses(path)]
    if not all_uses:
        print("No external actions to pin.")
        return 0

    # --check answers the security question only: is everything pinned to an
    # immutable SHA? It deliberately makes no network calls, so it is fast,
    # cannot be rate limited, and never fails merely because an upstream
    # project published a release. Staying current is a separate concern —
    # --check-latest, or Dependabot.
    if args.check and not args.check_latest:
        unpinned = [u for u in all_uses if not u.is_pinned]
        for use in unpinned:
            where = f"{use.path.relative_to(args.root)}:{use.lineno}"
            print(f"  unpinned {where}  {use.repo}{use.subpath}@{use.ref}")
        if unpinned:
            print(f"\n::error::{len(unpinned)} action(s) not pinned to a commit SHA. "
                  "Run .github/scripts/pin-actions.py to fix.")
            return 1
        print(f"All {len(all_uses)} action reference(s) are pinned to a commit SHA.")
        return 0

    resolver = Resolver()
    edits: dict[Path, dict[int, str]] = {}
    outdated = 0
    major_bumps: list[str] = []

    for use in all_uses:
        try:
            if args.keep_version and not use.is_pinned:
                # Pin what is already referenced rather than upgrading.
                tag = use.ref
            else:
                tag = resolver.latest_tag(use.repo)
            sha = resolver.sha_for(use.repo, tag)
        except PinError as exc:
            print(f"  !! {use.repo}: {exc}", file=sys.stderr)
            return 1

        # A major bump can change an action's inputs or behaviour, so it needs a
        # real CI run to validate - call it out rather than slipping it in.
        old_major = MAJOR_RE.match(use.ref) if not use.is_pinned else None
        new_major = MAJOR_RE.match(tag)
        if old_major and new_major and old_major.group(1) != new_major.group(1):
            major_bumps.append(f"{use.repo}: {use.ref} -> {tag}")

        new_line = rewrite_line(use.line, use.repo, use.subpath, sha, tag)
        if new_line == use.line:
            print(f"  ok      {use.repo}{use.subpath}@{tag}")
            continue

        outdated += 1
        where = f"{use.path.relative_to(args.root)}:{use.lineno}"
        state = "unpinned" if not use.is_pinned else "outdated"
        print(f"  {state:<8} {where}")
        print(f"           {use.ref}  ->  {sha[:12]}... ({tag})")
        edits.setdefault(use.path, {})[use.lineno] = new_line

    if major_bumps:
        print("\n  WARNING: major version bumps, which can change inputs or behaviour:")
        for bump in sorted(set(major_bumps)):
            print(f"    {bump}")
        print("  Run CI before trusting these. Use --keep-version to pin without upgrading.")

    if not outdated:
        print(f"\nAll {len(all_uses)} action reference(s) pinned and up to date.")
        return 0

    if args.check or args.check_latest:
        print(f"\n::error::{outdated} action reference(s) are unpinned or out of date. "
              "Run .github/scripts/pin-actions.py to fix.")
        return 1

    if args.dry_run:
        print(f"\n{outdated} reference(s) would be updated. Re-run without --dry-run.")
        return 0

    for path, changes in edits.items():
        lines = path.read_text().splitlines(keepends=True)
        for lineno, new_line in changes.items():
            ending = "\n" if lines[lineno - 1].endswith("\n") else ""
            lines[lineno - 1] = new_line + ending
        path.write_text("".join(lines))
        print(f"\nUpdated {path.relative_to(args.root)}")

    print(f"\n{outdated} reference(s) pinned.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PinError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
