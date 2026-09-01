# Releasing

Publishing a Windows installer is one command:

```bash
git tag v0.1.0
git push origin v0.1.0
```

That triggers `.github/workflows/release.yml`, which compiles the installer,
smoke-tests it, and attaches it to a GitHub Release with a checksum.

To rehearse without publishing, run the workflow by hand from the **Actions** tab
and give it a version. It builds and tests the installer, uploads it as an
artifact, and skips creating a release.

## The tag and pyproject.toml must agree

The release job reads `version` from `pyproject.toml` and refuses to build if the
tag disagrees:

```
Tag v0.2.0 does not match pyproject.toml version 0.1.0.
```

So bump the version **before** tagging:

```bash
# set version = "0.2.0" in pyproject.toml
git commit -am "Release 0.2.0"
git tag v0.2.0
git push origin main v0.2.0
```

If you tagged first, either move the tag or fix the file — the error message
spells out both. This stops an installer shipping with a filename that disagrees
with the version the package reports about itself.

Manual runs from the Actions tab skip the check, since the point of those is to
rehearse a build at an arbitrary version.

## What the release job does

1. Reads the version from `pyproject.toml` and checks the tag matches
2. Installs Inno Setup and compiles `packaging/windows/notionsearch.iss`
3. **Silently installs the result** and checks that the app, docker, web and
   scripts folders all landed, that `data/` exists and is writable, and that no
   `.env` or build cruft was packaged
4. Uninstalls again
5. Writes `SHA256SUMS.txt`
6. Creates the GitHub Release with install instructions

Step 3 is the point: it catches an installer that compiles but doesn't work,
which is exactly the failure a user would hit first.

## Before tagging

- CI is green on `main`
- `version` in `pyproject.toml` is bumped and committed (the job enforces this)
- Check `docs/usage/getting-started.md` still matches reality

## The installer is not code-signed

Windows SmartScreen warns that the publisher is unknown, and the user has to
click **More info** → **Run anyway**. This is expected and is called out in the
release notes.

Removing that warning needs a code-signing certificate (a few hundred pounds a
year from a CA). If you get one, add it as repository secrets and sign in the
release workflow with `signtool` after the compile step.

## Adding another workflow

Workflows live in `.github/workflows/`. Keep them lintable:

```bash
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
```

`actionlint` catches expression typos, bad `runs-on` values and YAML mistakes
that would otherwise only show up after pushing.

New actions must be pinned to a commit SHA — CI enforces it. See
[Pinning actions](pinning-actions.md).
