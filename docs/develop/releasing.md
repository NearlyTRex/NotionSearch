# Releasing

Releases go through two workflows from the shared
[NearlyTRex/Workflows](https://github.com/NearlyTRex/Workflows) library. The version in
`pyproject.toml` is the only place it is written; nothing is tagged by hand.

## Cutting a release

1. **Actions → Prepare Release → Run workflow.** Enter `patch`, `minor`, `major` or an exact
   `x.y.z`. It bumps `version` in `pyproject.toml` on a `release/vX.Y.Z` branch and opens a pull
   request.
2. **Merge that pull request.** On the push to `main`, the `Release` workflow sees a version with
   no tag yet, and:
   1. runs the same tests as CI, since a pull request opened by a workflow doesn't trigger CI;
   2. compiles `packaging/windows/notionsearch.iss` at that version;
   3. **silently installs the result** with `.github/scripts/smoke-test-installer.ps1`, and
      checks the app, docker, web and scripts folders all landed, that `data/` exists and is
      writable, and that no `.env` or build cruft was packaged;
   4. tags the merge commit `vX.Y.Z` and publishes a GitHub Release with the installer and a
      `SHA256SUMS.txt` attached.

The installer appears a few minutes after merging, once the Windows build finishes; watch the
**Actions** tab.

Step 3 is the point: it catches an installer that compiles but doesn't work, which is exactly the
failure a user would hit first.

A push that doesn't change the version finds its tag already there and builds nothing, so every
other merge to `main` is unaffected, and the tag and `pyproject.toml` cannot disagree. **Don't
tag a release yourself**: an existing tag tells the workflow the version is already released.

The app reports the same version: `app/main.py` reads it from `pyproject.toml`, which the
container image and the installer both ship.

## Release notes

The notes start with [`.github/release-notes.md`](../../.github/release-notes.md), the install
steps for Windows and for Linux or macOS, with `{version}`, `{tag}` and `{repository}` filled in.
GitHub's generated notes follow, listing the pull requests merged since the last release. Edit
the release afterwards to add anything specific to it.

## Rehearsing

CI compiles and smoke-tests the installer on every pull request, at a placeholder version, so a
broken installer shows up there rather than at release time.

## Before releasing

- CI is green on `main`
- `docs/usage/getting-started.md` still matches reality

## The installer is not code-signed

Windows SmartScreen warns that the publisher is unknown, and the user has to
click **More info** → **Run anyway**. This is expected and is called out in the
release notes.

Removing that warning needs a code-signing certificate (a few hundred pounds a
year from a CA). If you get one, add it as repository secrets and sign it in the
library's `inno-setup` workflow with `signtool` after the compile step.

## Adding another workflow

Workflows live in `.github/workflows/`. Keep them lintable:

```bash
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
```

`actionlint` catches expression typos, bad `runs-on` values and YAML mistakes
that would otherwise only show up after pushing.

New actions must be pinned to a commit SHA — the Security workflow's zizmor
check enforces it. See [Pinning actions](pinning-actions.md). Prefer a reusable
workflow from the Workflows library over bare steps; if it lacks something, add
it there so every repo gets it.
