# Security checks

`.github/workflows/security.yml` runs on every push and pull request, and again
weekly on a schedule — because a vulnerability disclosed after your last commit
still affects you.

It is deliberately separate from `ci.yml`, so a scanner going red on something
outside your control never blocks a code review.

| Job | What it checks |
|---|---|
| Secrets | `gitleaks` over full git history, plus a check that no credential-bearing file is tracked |
| Dependency CVEs | `pip-audit` against the pinned `requirements.txt` |
| Container image CVEs | `trivy` against the image the Dockerfile produces |

## Running them locally

```bash
# secrets, across all history
docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:latest \
    detect --source=/repo --redact --config=/repo/.gitleaks.toml

# no credential-bearing file is tracked
.github/scripts/check-no-secrets.sh

# python dependency CVEs
pip-audit --requirement requirements.txt --strict

# container image CVEs
docker build -f docker/Dockerfile -t notionsearch-api:scan .
docker save notionsearch-api:scan -o /tmp/image.tar
docker run --rm -v /tmp:/tmp aquasec/trivy:latest image \
    --input /tmp/image.tar --severity HIGH,CRITICAL --ignore-unfixed
```

## Secret scanning

`gitleaks` scans **history**, not just the working tree: a secret that was
committed and later deleted is still published, and deleting the file does not
unpublish it.

`.gitleaks.toml` keeps all the default rules and adds two of its own, because
gitleaks ships no rule for Notion credentials — and a Notion token is the one
secret this project actually handles:

```toml
[[rules]]
id = "notion-integration-token"
regex = '''\bntn_[A-Za-z0-9]{40,}\b'''
```

That was verified rather than assumed: a realistic `ntn_` token is **not**
caught by the default rules alone.

The allowlist names specific fake values (`testkey1234567890`,
`ntn_valid_key_123` and friends) rather than excluding the tests directory
wholesale. A real secret pasted into a test is exactly what this should catch.

### If gitleaks flags something real

Removing the file is not enough — anything pushed must be treated as leaked.
**Rotate the credential first**, then clean the history.

## Why `check-no-secrets.sh` exists alongside gitleaks

gitleaks scans file *contents*. That script checks that whole categories of file
are never committed at all: `.env`, `*.db`, `*.pem`, keys.

It matters here specifically because the app stores the Notion token inside
`data/notionsearch.db`. A single `git add -f data/` would put a live credential
in a public repository, and a binary SQLite file is not something a
content scanner reliably flags.

## Why Trivy uses `--ignore-unfixed`

A CVE with no available fix is not something a rebuild can resolve. Failing the
build on it only teaches people to ignore the job. The scan fails on
HIGH/CRITICAL issues that *have* a fix, which is always actionable: bump the
dependency or the base image.

This is not theoretical. The first run found `starlette 0.41.3` carrying
CVE-2026-48818 — SSRF and NTLM credential theft via UNC paths in `StaticFiles`,
which this app uses to serve `web/` — and three CVEs in `python-multipart`,
a dependency that turned out to be entirely unused and was removed.

## Keeping ahead of it

`.github/dependabot.yml` raises weekly pull requests for pip, GitHub Actions and
the Docker base image, grouped so routine bumps arrive as one review.

Actions are pinned to commit SHAs; Dependabot reads the trailing `# v7.0.1`
comment, so pinning does not strand you on stale versions. See
[Pinning actions](pinning-actions.md).

## Workflow hardening

All workflows declare least-privilege permissions and bounded runtimes:

- `permissions: contents: read` at the top of every workflow. Only the release
  job escalates to `contents: write`, and only because it creates a release.
- `timeout-minutes` on every job. The default lets a hung job run for six hours.
- `persist-credentials: false` on every checkout. Otherwise `actions/checkout`
  leaves the `GITHUB_TOKEN` in `.git/config`, where any later step can read it.

The scanners run as container images rather than third-party actions, so the
tools checking our supply chain do not themselves enlarge it.
