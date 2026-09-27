"""Tests for .github/scripts/pin-actions.py.

Repository tooling rather than application code, so it lives outside
tests/unit/ (which mirrors app/). It is inside the 100% coverage gate all the
same, because this tool rewrites workflow files in place.

Everything here is offline: the GitHub API is replaced with canned responses.
"""

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "pin-actions.py"


def _load():
    """Import the script by path — its filename is not a valid module name."""
    spec = importlib.util.spec_from_file_location("pin_actions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pin_actions"] = module
    spec.loader.exec_module(module)
    return module


pin = _load()

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SHA2 = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"


def write_workflow(root: Path, body: str, name: str = "ci.yml") -> Path:
    path = root / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


# --- parsing --------------------------------------------------------------

@pytest.mark.parametrize("line,repo,subpath,ref", [
    ("      - uses: actions/checkout@v4", "actions/checkout", "", "v4"),
    ("  uses: actions/setup-python@v5", "actions/setup-python", "", "v5"),
    (f"      - uses: actions/checkout@{SHA} # v7.0.1", "actions/checkout", "", SHA),
    ('      - uses: "actions/checkout@v4"', "actions/checkout", "", "v4"),
    ("      - uses: 'actions/checkout@v4'", "actions/checkout", "", "v4"),
    ("      - uses: github/codeql-action/init@v3", "github/codeql-action", "/init", "v3"),
    ("      - uses: owner/repo@main", "owner/repo", "", "main"),
])
def test_recognises_uses_lines(tmp_path, line, repo, subpath, ref):
    path = write_workflow(tmp_path, f"jobs:\n  x:\n    steps:\n{line}\n")
    found = pin.find_uses(path)
    assert len(found) == 1
    assert (found[0].repo, found[0].subpath, found[0].ref) == (repo, subpath, ref)


@pytest.mark.parametrize("line", [
    "      - uses: ./.github/actions/local",       # local action: no commit exists
    "      - uses: docker://alpine:3.20",          # docker image, not a repo
    "      - run: echo uses: actions/checkout@v4",  # a run step, not a uses
    "      # - uses: actions/checkout@v4",          # commented out
    "      - uses: actions/checkout",               # no ref at all
])
def test_ignores_lines_that_cannot_be_pinned(tmp_path, line):
    path = write_workflow(tmp_path, f"jobs:\n  x:\n    steps:\n{line}\n")
    assert pin.find_uses(path) == []


def test_is_pinned_detects_sha_refs(tmp_path):
    path = write_workflow(tmp_path, (
        "steps:\n"
        "  - uses: actions/checkout@v4\n"
        f"  - uses: actions/setup-python@{SHA}\n"
    ))
    unpinned, pinned = pin.find_uses(path)
    assert unpinned.is_pinned is False
    assert pinned.is_pinned is True


def test_line_numbers_are_reported(tmp_path):
    path = write_workflow(tmp_path, "a\nb\n  - uses: actions/checkout@v4\n")
    assert pin.find_uses(path)[0].lineno == 3


# --- rewriting ------------------------------------------------------------

def test_rewrite_preserves_indentation_and_adds_the_version_comment():
    line = "      - uses: actions/checkout@v4"
    out = pin.rewrite_line(line, "actions/checkout", "", SHA, "v7.0.1")
    assert out == f"      - uses: actions/checkout@{SHA} # v7.0.1"


def test_rewrite_keeps_a_subpath():
    line = "      - uses: github/codeql-action/init@v3"
    out = pin.rewrite_line(line, "github/codeql-action", "/init", SHA, "v3.1.0")
    assert out == f"      - uses: github/codeql-action/init@{SHA} # v3.1.0"


def test_rewrite_replaces_a_stale_comment_rather_than_appending():
    line = f"      - uses: actions/checkout@{SHA} # v4.2.2"
    out = pin.rewrite_line(line, "actions/checkout", "", SHA2, "v7.0.1")
    assert out.count("#") == 1
    assert out.endswith("# v7.0.1")


def test_rewrite_preserves_quoting():
    line = '      - uses: "actions/checkout@v4"'
    out = pin.rewrite_line(line, "actions/checkout", "", SHA, "v7.0.1")
    assert out == f'      - uses: "actions/checkout@{SHA}" # v7.0.1'


# --- version ordering -----------------------------------------------------

def test_newest_version_wins():
    tags = ["v1.0.0", "v4.2.2", "v10.0.0", "v4.10.0"]
    assert max(tags, key=pin._version_key) == "v10.0.0"


def test_prereleases_lose_to_the_final_release():
    assert max(["v2.0.0", "v2.0.0-rc1"], key=pin._version_key) == "v2.0.0"


def test_unparseable_tags_never_win():
    assert max(["v1.0.0", "latest", "nightly"], key=pin._version_key) == "v1.0.0"


def test_missing_components_are_treated_as_zero():
    assert pin._version_key("v3") < pin._version_key("v3.1")


# --- discovery ------------------------------------------------------------

def test_finds_workflows_and_composite_actions(tmp_path):
    write_workflow(tmp_path, "steps: []", "ci.yml")
    write_workflow(tmp_path, "steps: []", "release.yaml")
    composite = tmp_path / ".github" / "actions" / "setup" / "action.yml"
    composite.parent.mkdir(parents=True)
    composite.write_text("runs:\n  using: composite\n")

    names = {p.name for p in pin.workflow_files(tmp_path)}
    assert names == {"ci.yml", "release.yaml", "action.yml"}


def test_no_workflows_is_not_an_error(tmp_path):
    assert pin.main(["--check", "--root", str(tmp_path)]) == 0


# --- --check (offline) ----------------------------------------------------

def test_check_fails_on_an_unpinned_action(tmp_path, capsys):
    write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")
    assert pin.main(["--check", "--root", str(tmp_path)]) == 1
    assert "not pinned" in capsys.readouterr().out


def test_check_passes_when_everything_is_pinned(tmp_path, capsys):
    write_workflow(tmp_path, f"steps:\n  - uses: actions/checkout@{SHA} # v7.0.1\n")
    assert pin.main(["--check", "--root", str(tmp_path)]) == 0
    assert "pinned to a commit SHA" in capsys.readouterr().out


def test_check_ignores_local_actions(tmp_path):
    write_workflow(tmp_path, "steps:\n  - uses: ./.github/actions/setup\n")
    assert pin.main(["--check", "--root", str(tmp_path)]) == 0


def test_check_makes_no_network_calls(tmp_path, monkeypatch):
    """--check must stay usable in CI without a token or the network."""
    def explode(*args, **kwargs):
        raise AssertionError("--check must not call the GitHub API")

    monkeypatch.setattr(pin, "_request", explode)
    write_workflow(tmp_path, f"steps:\n  - uses: actions/checkout@{SHA} # v7.0.1\n")
    assert pin.main(["--check", "--root", str(tmp_path)]) == 0


def test_check_does_not_modify_files(tmp_path):
    path = write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")
    before = path.read_text()
    pin.main(["--check", "--root", str(tmp_path)])
    assert path.read_text() == before


# --- writing --------------------------------------------------------------

def test_pinning_rewrites_only_the_uses_line(tmp_path, monkeypatch):
    body = (
        "name: CI\n"
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: Keep me\n"
        "        run: echo 'uses: actions/checkout@v4'\n"
    )
    path = write_workflow(tmp_path, body)

    monkeypatch.setattr(pin.Resolver, "latest_tag", lambda self, repo: "v7.0.1")
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA)

    assert pin.main(["--root", str(tmp_path)]) == 0

    lines = path.read_text().splitlines()
    assert lines[6] == f"      - uses: actions/checkout@{SHA} # v7.0.1"
    # Everything else, including a run step that merely mentions "uses:", is
    # left exactly as it was.
    assert lines[7] == "      - name: Keep me"
    assert lines[8] == "        run: echo 'uses: actions/checkout@v4'"
    assert path.read_text().endswith("\n"), "trailing newline must survive"


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    path = write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")
    before = path.read_text()

    monkeypatch.setattr(pin.Resolver, "latest_tag", lambda self, repo: "v7.0.1")
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA)

    assert pin.main(["--dry-run", "--root", str(tmp_path)]) == 0
    assert path.read_text() == before


def test_keep_version_pins_without_upgrading(tmp_path, monkeypatch):
    path = write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")

    def must_not_be_called(self, repo):
        raise AssertionError("--keep-version must not look up the latest release")

    monkeypatch.setattr(pin.Resolver, "latest_tag", must_not_be_called)
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA)

    assert pin.main(["--keep-version", "--root", str(tmp_path)]) == 0
    assert path.read_text().strip().endswith(f"actions/checkout@{SHA} # v4")


def test_running_twice_is_idempotent(tmp_path, monkeypatch):
    path = write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")
    monkeypatch.setattr(pin.Resolver, "latest_tag", lambda self, repo: "v7.0.1")
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA)

    pin.main(["--root", str(tmp_path)])
    once = path.read_text()
    pin.main(["--root", str(tmp_path)])
    assert path.read_text() == once


def test_major_bump_is_reported(tmp_path, monkeypatch, capsys):
    write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")
    monkeypatch.setattr(pin.Resolver, "latest_tag", lambda self, repo: "v7.0.1")
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA)

    pin.main(["--dry-run", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "major version bump" in out.lower()
    assert "v4 -> v7.0.1" in out


def test_minor_bump_is_not_reported_as_major(tmp_path, monkeypatch, capsys):
    write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4.1.0\n")
    monkeypatch.setattr(pin.Resolver, "latest_tag", lambda self, repo: "v4.2.2")
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA)

    pin.main(["--dry-run", "--root", str(tmp_path)])
    assert "major version bump" not in capsys.readouterr().out.lower()


def test_api_failure_is_reported_not_crashed(tmp_path, monkeypatch, capsys):
    write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")

    def fail(self, repo):
        raise pin.PinError("GitHub API rate limit reached.")

    monkeypatch.setattr(pin.Resolver, "latest_tag", fail)
    assert pin.main(["--root", str(tmp_path)]) == 1
    assert "rate limit" in capsys.readouterr().err


def test_check_latest_fails_when_a_pin_is_behind(tmp_path, monkeypatch, capsys):
    write_workflow(tmp_path, f"steps:\n  - uses: actions/checkout@{SHA} # v4.0.0\n")
    monkeypatch.setattr(pin.Resolver, "latest_tag", lambda self, repo: "v7.0.1")
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA2)

    assert pin.main(["--check-latest", "--root", str(tmp_path)]) == 1
    assert "unpinned or out of date" in capsys.readouterr().out


def test_workflows_with_no_external_actions(tmp_path, capsys):
    write_workflow(tmp_path, "steps:\n  - run: echo hi\n")
    assert pin.main(["--check", "--root", str(tmp_path)]) == 0
    assert "No external actions" in capsys.readouterr().out


# --- the GitHub API -------------------------------------------------------

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", {}, io.BytesIO(body))


def test_request_sends_a_token_when_one_is_set(monkeypatch):
    seen = {}

    def urlopen(req, timeout):
        seen.update(req.headers)
        return FakeResponse(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(pin.urllib.request, "urlopen", urlopen)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "abc")
    assert pin._request("https://api.github.com/x") == {"ok": True}
    assert seen["Authorization"] == "Bearer abc"


def test_request_without_a_token(monkeypatch):
    seen = {}

    def urlopen(req, timeout):
        seen.update(req.headers)
        return FakeResponse(b"[]")

    monkeypatch.setattr(pin.urllib.request, "urlopen", urlopen)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert pin._request("https://api.github.com/x") == []
    assert "Authorization" not in seen


@pytest.mark.parametrize("error,message", [
    (http_error(403, b"API rate limit exceeded"), "rate limit reached"),
    (http_error(403, b"forbidden"), "GitHub API error 403"),
    (http_error(404), "Not found"),
    (http_error(500), "GitHub API error 500"),
    (urllib.error.URLError("no route"), "Could not reach GitHub: no route"),
])
def test_request_turns_failures_into_plain_errors(monkeypatch, error, message):
    def urlopen(req, timeout):
        raise error

    monkeypatch.setattr(pin.urllib.request, "urlopen", urlopen)
    with pytest.raises(pin.PinError, match=message):
        pin._request("https://api.github.com/x")


def fake_api(monkeypatch, responses):
    """Answer _request from a dict of URL suffix -> response (or exception)."""
    calls = []

    def request(url):
        calls.append(url)
        for suffix, response in responses.items():
            if url.endswith(suffix):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected call {url}")

    monkeypatch.setattr(pin, "_request", request)
    return calls


def test_latest_tag_prefers_the_latest_release_and_caches_it(monkeypatch):
    calls = fake_api(monkeypatch, {"/releases/latest": {"tag_name": "v7.0.1"}})
    resolver = pin.Resolver()
    assert resolver.latest_tag("actions/checkout") == "v7.0.1"
    assert resolver.latest_tag("actions/checkout") == "v7.0.1"
    assert len(calls) == 1


@pytest.mark.parametrize("release", [
    pin.PinError("Not found"),  # no releases at all
    {"tag_name": None},         # a release without a tag
    [],                         # something that is not a release
])
def test_latest_tag_falls_back_to_the_highest_tag(monkeypatch, release):
    fake_api(monkeypatch, {
        "/releases/latest": release,
        "/tags?per_page=100": [{"name": "v1.0.0"}, {"name": "v2.1.0"}, {"name": "latest"}, "junk"],
    })
    assert pin.Resolver().latest_tag("o/r") == "v2.1.0"


def test_latest_tag_with_nothing_to_pin_to(monkeypatch):
    fake_api(monkeypatch, {"/releases/latest": pin.PinError("Not found"), "/tags?per_page=100": []})
    with pytest.raises(pin.PinError, match="no releases or tags"):
        pin.Resolver().latest_tag("o/r")


def test_sha_for_resolves_and_caches(monkeypatch):
    calls = fake_api(monkeypatch, {"/commits/v7.0.1": {"sha": SHA}})
    resolver = pin.Resolver()
    assert resolver.sha_for("actions/checkout", "v7.0.1") == SHA
    assert resolver.sha_for("actions/checkout", "v7.0.1") == SHA
    assert len(calls) == 1


@pytest.mark.parametrize("commit,message", [
    ({"message": "no sha here"}, "Could not resolve"),
    ([], "Could not resolve"),
    ({"sha": "not-a-sha"}, "unexpected sha"),
])
def test_sha_for_rejects_bad_answers(monkeypatch, commit, message):
    fake_api(monkeypatch, {"/commits/v1": commit})
    with pytest.raises(pin.PinError, match=message):
        pin.Resolver().sha_for("o/r", "v1")
