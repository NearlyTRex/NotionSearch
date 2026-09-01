# Pinning GitHub Actions

Workflows reference actions by commit SHA, not by tag:

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
```

## Why

A tag is mutable. Whoever controls an action can move `v4` to point at different
code, and every workflow using it silently runs that instead — with whatever
secrets and repository write access the job has. This is a real supply-chain
attack path, not a theoretical one.

A commit SHA cannot be moved. Pinning to one means the workflow runs exactly the
code that was reviewed.

The version stays in a trailing comment so the line is still readable, and so
Dependabot can offer updates.

## Updating them

```bash
.github/scripts/pin-actions.py --dry-run     # see what would change
.github/scripts/pin-actions.py               # upgrade to latest and pin
.github/scripts/pin-actions.py --keep-version  # pin, but do not upgrade
```

`--keep-version` resolves the version already referenced instead of the newest
one. Use it when you want the security benefit without taking an upgrade in the
same change.

Set `GITHUB_TOKEN` to raise the API rate limit from 60 to 5000 requests an hour:

```bash
export GITHUB_TOKEN=$(gh auth token)
```

### Major version bumps

The tool warns when an upgrade crosses a major version:

```
WARNING: major version bumps, which can change inputs or behaviour:
  actions/checkout: v4 -> v7.0.1
```

A major bump can rename or remove inputs, so let CI run before trusting it. If
it breaks, `--keep-version` pins the older version instead.

## Enforcement

CI runs:

```bash
.github/scripts/pin-actions.py --check
```

which fails if any action is not pinned to a SHA.

`--check` deliberately makes **no network calls**. It answers only the security
question — is everything pinned? — so it cannot be rate limited, needs no token,
and never turns the build red merely because an upstream project published a
release. Staying current is a separate concern: use `--check-latest` when you
want that, or let Dependabot open the pull requests.

## Tests

```bash
python -m pytest tests/tools -q
```

The tool rewrites workflow files in place, so its parsing and rewriting are
covered by tests — including that it leaves everything except the `uses:` line
untouched. They live in `tests/tools/` rather than `tests/unit/`, which mirrors
`app/`, and sit outside the 100% coverage gate.
