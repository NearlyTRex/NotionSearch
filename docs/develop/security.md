# Security checks

`.github/workflows/security.yml` runs on every push and pull request, and again
weekly on a schedule — because a vulnerability disclosed after your last commit
still affects you.

It is deliberately separate from `ci.yml`, so a scanner going red on something
outside your control never blocks a code review. Every job is a reusable
workflow from the shared [NearlyTRex/Workflows](https://github.com/NearlyTRex/Workflows)
library, which pins each scanner by hash or digest and keeps them current with
Dependabot.

| Job | What it checks |
|---|---|
| Security | `zizmor` over the workflows, `gitleaks` over full git history, `pip-audit` against the hash-locked `requirements.txt`, and a check that no credential-bearing file is tracked |
| CodeQL | Static analysis of the Python, `web/app.js` and the workflows. Results are under Security → Code scanning |
| Dependency Review | Pull requests only: fails one that adds a dependency with a known vulnerability |
| Container Scan | `trivy` against the image the Dockerfile produces, and against the Dockerfile itself for misconfigurations |

## Running them locally

```bash
# secrets, across all history
docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:latest \
    git --redact /repo

# no credential-bearing file is tracked (from a checkout of NearlyTRex/Workflows)
python3 ../Workflows/actions/check-tracked-files/check_tracked_files.py \
    '*.db' '*.db-shm' '*.db-wal' '*.sqlite' '*.sqlite3' 'data/*' '!data/.gitkeep'

# python dependency CVEs
pip-audit --requirement requirements.txt --require-hashes --disable-pip --strict

# container image CVEs
docker build -f docker/Dockerfile -t notionsearch-api:scan .
docker save notionsearch-api:scan -o /tmp/image.tar
docker run --rm -v /tmp:/tmp aquasec/trivy:latest image \
    --input /tmp/image.tar --severity HIGH,CRITICAL --ignore-unfixed

# Dockerfile misconfigurations (reads .trivyignore)
docker run --rm -v "$PWD:/repo" -w /repo aquasec/trivy:latest config \
    --severity HIGH,CRITICAL docker
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

## Why the tracked-files check exists alongside gitleaks

gitleaks scans file *contents*. The tracked-files check makes sure whole
categories of file are never committed at all. The library covers `.env` files
and private keys; `security.yml` adds SQLite databases and anything under
`data/` except `.gitkeep`, through `tracked-files-patterns`.

It matters here specifically because the app stores the Notion token inside
`data/notionsearch.db`. A single `git add -f data/` would put a live credential
in a public repository, and a binary SQLite file is not something a
content scanner reliably flags.

## Why Trivy uses `--ignore-unfixed`

A CVE with no available fix is not something a rebuild can resolve. Failing the
build on it only teaches people to ignore the job. The scan fails on
HIGH/CRITICAL issues that *have* a fix, which is always actionable: bump the
dependency or the base image.

### Why the image is Alpine

The image used to be built on `python:*-slim`, which is Debian. Even freshly
rebuilt, that carried dozens of HIGH-severity CVEs with no fix available, in
packages Debian marks essential (`util-linux`, `ncurses`, `perl-base`, the
`systemd` libraries) that this app never uses and that cannot be removed.
`--ignore-unfixed` kept the gate green, but the findings never went away, and each
time a fix did land a scan went red until the next rebuild.

On `python:*-alpine`, busybox replaces all of those. The same scan finds nothing,
and the image is half the size. Every dependency, including uvicorn's native
extensions, publishes musllinux wheels, so nothing is compiled during the build.
`apk upgrade` in the Dockerfile picks up fixes published after the base image was
built, and `su-exec` does what `gosu` did in the entrypoint.

### Deliberate exceptions

`.trivyignore` lists the findings that are intended, each with its reason. There
is one: `DS-0002`, "no `USER` in the Dockerfile". The container starts as root
only long enough for the entrypoint to adopt the owner of the mounted `./data`
folder, then drops to that user before the app starts; a fixed `USER` would break
every host whose user id differs. CI's Docker job checks the drop happens.

This is not theoretical. The first run found `starlette 0.41.3` carrying
CVE-2026-48818 — SSRF and NTLM credential theft via UNC paths in `StaticFiles`,
which this app uses to serve `web/` — and three CVEs in `python-multipart`,
a dependency that turned out to be entirely unused and was removed.

## Keeping ahead of it

`.github/dependabot.yml` raises weekly pull requests for pip, GitHub Actions
(including the Workflows library pin), the Docker base image and the Meilisearch
image in `docker-compose.yml`, grouped so routine bumps arrive as one review. Each
waits 7 days after an upstream release, so a compromised release has time to be
caught before it is proposed here.

Actions are pinned to commit SHAs; Dependabot reads the trailing `# v7.0.1`
comment, so pinning does not strand you on stale versions. See
[Pinning actions](pinning-actions.md).

## Workflow hardening

All workflows declare least-privilege permissions and bounded runtimes:

- Read-only permissions by default, granted per job. Only the release job gets
  `contents: write`, because it creates a release, and only CodeQL gets
  `security-events: write`, to upload its results.
- `timeout-minutes` on every job. The default lets a hung job run for six hours.
- `persist-credentials: false` on every checkout. Otherwise `actions/checkout`
  leaves the `GITHUB_TOKEN` in `.git/config`, where any later step can read it.

Inputs and step outputs reach scripts through `env:`, never by `${{ }}`
expansion inside `run:`, where a crafted value would execute as code. zizmor
enforces that.
